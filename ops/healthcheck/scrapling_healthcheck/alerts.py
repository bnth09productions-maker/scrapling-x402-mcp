"""Local outbox plus an optional HTTPS webhook. No chat delivery by itself."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List
from urllib.parse import urlparse


def event_for(check_id: str, previous: str, current: str, summary: str, now: float) -> Dict[str, Any] | None:
    if previous == current:
        return None
    if current.startswith("fail") or current.startswith("missing"):
        kind = "failure"
    elif previous.startswith("fail") or previous.startswith("missing"):
        kind = "recovery"
    else:
        kind = "change"
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    return {
        "id": f"{check_id}|{current}|{stamp[:13]}",
        "kind": kind,
        "check_id": check_id,
        "summary": summary,
        "at": stamp,
    }


def webhook_url() -> str:
    return (os.environ.get("SCRAPLING_HEALTH_WEBHOOK_URL") or "").strip()


def post_webhook(url: str, event: Dict[str, Any], attempts: int = 3) -> str:
    """POST one event. Returns delivered, skipped, or failed. Never sends credentials."""
    parts = urlparse(url)
    if parts.scheme != "https" or not parts.hostname:
        return "skipped"
    body = json.dumps({
        "id": event["id"],
        "kind": event["kind"],
        "check_id": event["check_id"],
        "summary": event["summary"],
        "at": event["at"],
    }).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "ScraplingHealthCheck/1.0"},
    )
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if 200 <= resp.status < 300:
                    return "delivered"
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return "failed"
        except Exception:
            pass
        time.sleep(0.2 * (attempt + 1))
    return "failed"


def write_outbox(path: str, events: List[Dict[str, Any]], sender=post_webhook) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    url = webhook_url()
    with open(path, "a", encoding="utf-8") as handle:
        for event in events:
            record = dict(event)
            record["webhook"] = "not_configured" if not url else sender(url, event)
            if record["webhook"] == "not_configured":
                record["delivery"] = "local only; no external chat until a webhook is connected"
            handle.write(json.dumps(record, sort_keys=True) + "\n")
