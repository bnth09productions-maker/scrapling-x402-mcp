"""One health run: lock, checks, deduped events, reports."""
from __future__ import annotations

import json
import logging
import os
import time
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List, Tuple

from .alerts import event_for, write_outbox
from .checks import Check, Runner, reject_banned_config


def summarize(checks: List[Check]) -> Tuple[str, int]:
    """Exit 1 is a real failure. Exit 3 is a coverage gap. Exit 4 is a known stall.

    Exit 0 is a complete pass: every check passed. A known stall does not page
    by itself; worsening past its window is a failure.
    """
    if any(item.fails_run() for item in checks):
        return "fail", 1
    if any(item.status == "not_configured" for item in checks):
        return "incomplete", 3
    if any(item.status == "watch" for item in checks):
        return "degraded", 4
    return "ok", 0
from .http_client import HttpClient
from .redact import redact
from .state_store import RunLock, load_state, save_state


def load_config(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        config = json.load(handle)
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    reject_banned_config(config)
    return config


def _dirs(config: Dict[str, Any], root: str) -> Dict[str, str]:
    def place(key: str, default: str) -> str:
        raw = config.get(key) or default
        return raw if os.path.isabs(raw) else os.path.join(root, raw)

    return {
        "state": place("state_dir", "var/state"),
        "reports": place("report_dir", "var/reports"),
        "logs": place("log_dir", "var/logs"),
        "outbox": place("outbox_dir", "var/outbox"),
    }


def _logger(log_dir: str) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger("scrapling_healthcheck")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = RotatingFileHandler(
        os.path.join(log_dir, "healthcheck.log"), maxBytes=1_000_000, backupCount=5, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)sZ %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def _events(previous: Dict[str, Any], checks: List[Check], now: float) -> List[Dict[str, Any]]:
    events = []
    prior = previous.get("checks") or {}
    if previous.get("corrupt"):
        events.append({
            "id": "state|recovered",
            "kind": "change",
            "check_id": "state",
            "summary": "previous state file was corrupt and was quarantined",
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        })
    for check in checks:
        old = (prior.get(check.check_id) or {}).get("fingerprint")
        event = event_for(check.check_id, old or "", check.fingerprint, check.summary, now)
        if event and (old or check.status != "pass"):
            # First observation of a pass is not an alert. First failure is.
            if old is None and check.status == "pass":
                continue
            if old is None and check.status in ("watch", "not_configured"):
                continue
            events.append(event)
    return events


def _write_reports(directory: str, payload: Dict[str, Any], keep: int) -> Tuple[str, str]:
    os.makedirs(directory, exist_ok=True)
    stamp = payload["finished_at"].replace("-", "").replace(":", "")
    json_path = os.path.join(directory, stamp + ".json")
    md_path = os.path.join(directory, stamp + ".md")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    lines = [
        f"# Scrapling health {payload['finished_at']}",
        "",
        f"Overall: **{payload['overall']}** (exit {payload['exit_code']}).",
        "",
        "Each row is its own status. Overall incomplete means an optional local path is unset (a coverage gap, not a pass). Required evidence that is absent is missing and fails the run. A known stall inside its window is watch; older than that window is fail.",
        "",
        "| Check | Status | Summary |",
        "|---|---|---|",
    ]
    for row in payload["checks"]:
        lines.append(f"| {row['check_id']} | {row['status']} | {row['summary']} |")
    if payload["events"]:
        lines.extend(["", "## New events", ""])
        for event in payload["events"]:
            lines.append(f"- {event['kind']} {event['check_id']}: {event['summary']}")
    lines.append("")
    lines.append("No external chat or email is sent unless SCRAPLING_HEALTH_WEBHOOK_URL is an https URL set in the process environment.")
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    files = sorted(name for name in os.listdir(directory) if name.endswith(".json"))
    for name in files[:-keep] if keep > 0 else []:
        stem = name[:-5]
        for suffix in (".json", ".md"):
            try:
                os.remove(os.path.join(directory, stem + suffix))
            except OSError:
                pass
    return json_path, md_path


def execute(config: Dict[str, Any], root: str, client: HttpClient | None = None, now: float | None = None) -> int:
    reject_banned_config(config)
    now = time.time() if now is None else now
    paths = _dirs(config, root)
    logger = _logger(paths["logs"])
    lock = RunLock(os.path.join(paths["state"], "run.lock"))
    if not lock.acquire():
        logger.error("another health run holds the lock")
        return 2
    try:
        state_path = os.path.join(paths["state"], "state.json")
        previous = load_state(state_path)
        http = client or HttpClient(
            timeout=float(config.get("timeout_seconds") or 15),
            max_bytes=int(config.get("max_bytes") or 2_000_000),
            user_agent=str(config.get("user_agent") or "ScraplingHealthCheck/1.0 (+https://scraplingdata.xyz; internal monitor)"),
        )
        checks = Runner(config, http, now).run()
        events = _events(previous, checks, now)
        overall, exit_code = summarize(checks)
        finished = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        rows = [
            {"check_id": item.check_id, "status": item.status, "summary": redact(item.summary),
             "fingerprint": item.fingerprint}
            for item in checks
        ]
        payload = {
            "finished_at": finished,
            "overall": overall,
            "exit_code": exit_code,
            "public_pass": [row["check_id"] for row in rows if row["status"] == "pass" and not row["check_id"].startswith("local:")],
            "coverage_gaps": [row["check_id"] for row in rows if row["status"] == "not_configured"],
            "base_url": config.get("base_url"),
            "checks": rows,
            "events": events,
            "note": "Webhook posts failure events only, and only when SCRAPLING_HEALTH_WEBHOOK_URL is https. Recovery is opt-in. Coverage gaps stay in this report.",
        }
        json_path, md_path = _write_reports(paths["reports"], payload, int(config.get("keep_reports") or 48))
        if events:
            write_outbox(os.path.join(paths["outbox"], "events.jsonl"), events)
        save_state(state_path, {
            "checks": {item.check_id: {"fingerprint": item.fingerprint, "status": item.status} for item in checks},
            "corrupt": False,
        })
        logger.info("overall=%s exit=%s report=%s events=%s", payload["overall"], exit_code, json_path, len(events))
        for handler in list(logger.handlers):
            handler.close()
        logger.handlers.clear()
        print(f"{payload['overall']} exit={exit_code} report={md_path}")
        return exit_code
    except Exception as exc:
        logger.exception("health run failed: %s", type(exc).__name__)
        print(f"error exit=2 {type(exc).__name__}")
        return 2
    finally:
        lock.release()
