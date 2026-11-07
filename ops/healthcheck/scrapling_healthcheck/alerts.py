"""Local outbox plus an optional HTTPS webhook. No chat delivery by itself."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional
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
        "id": f"{check_id}|{current}|{int(now)}",
        "kind": kind,
        "check_id": check_id,
        "summary": summary,
        "fingerprint": current,
        "at": stamp,
    }


def webhook_url() -> str:
    return (os.environ.get("SCRAPLING_HEALTH_WEBHOOK_URL") or "").strip()


def recovery_opt_in() -> bool:
    return (os.environ.get("SCRAPLING_HEALTH_WEBHOOK_RECOVERY") or "").strip().lower() in {"1", "true", "yes", "on"}


def auth_headers() -> Optional[Dict[str, str]]:
    """Return extra headers, {} when unset, or None when the pair is incomplete.

    The header name is whatever the operator sets. This does not assume Bearer,
    Basic, or any vendor format, and it does not invent one.
    """
    name = (os.environ.get("SCRAPLING_HEALTH_WEBHOOK_AUTH_HEADER") or "").strip()
    value = os.environ.get("SCRAPLING_HEALTH_WEBHOOK_AUTH_VALUE")
    value = "" if value is None else value.strip()
    if not name and not value:
        return {}
    if not name or not value:
        return None
    return {name: value}


def webhook_sends(kind: str, recovery: bool) -> bool:
    if kind == "failure":
        return True
    if kind == "recovery" and recovery:
        return True
    return False


def webhook_request(url: str, event: Dict[str, Any], auth: Dict[str, str]) -> urllib.request.Request:
    body = json.dumps({
        "id": event["id"],
        "kind": event["kind"],
        "check_id": event["check_id"],
        "summary": event["summary"],
        "at": event["at"],
    }).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "ScraplingHealthCheck/1.0"}
    headers.update(auth)
    return urllib.request.Request(url, data=body, method="POST", headers=headers)


def post_webhook(url: str, event: Dict[str, Any], auth: Optional[Dict[str, str]] = None, attempts: int = 3) -> str:
    """POST one event. Returns delivered, skipped, or failed. Never sends credentials unless the operator named that header."""
    parts = urlparse(url)
    if parts.scheme != "https" or not parts.hostname:
        return "skipped"
    req = webhook_request(url, event, auth or {})
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
        if attempt + 1 < attempts:
            time.sleep(0.2 * (attempt + 1))
    return "failed"


def write_outbox(path: str, events: List[Dict[str, Any]], sender=None) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    url = webhook_url()
    recovery = recovery_opt_in()
    auth = auth_headers()
    send = post_webhook if sender is None else sender
    with open(path, "a", encoding="utf-8") as handle:
        for event in events:
            record = dict(event)
            if not url:
                record["webhook"] = "not_configured"
                record["delivery"] = "local only; no external chat until a webhook is connected"
            elif not webhook_sends(str(event.get("kind")), recovery):
                record["webhook"] = "filtered"
                record["delivery"] = "kept in the local outbox; webhook sends failures only"
            elif auth is None:
                record["webhook"] = "auth_incomplete"
                record["delivery"] = "auth header and value must both be set; no format was assumed"
            else:
                try:
                    record["webhook"] = send(url, event, auth)
                except Exception:
                    record["webhook"] = "failed"
            handle.write(json.dumps(record, sort_keys=True) + "\n")
