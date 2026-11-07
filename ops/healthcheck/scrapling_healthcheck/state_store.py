"""Persistent check fingerprints, exclusive lock, rotating reports."""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional


class RunLock:
    def __init__(self, path: str, stale_seconds: float = 1800) -> None:
        self.path = path
        self.stale_seconds = stale_seconds
        self._fd: Optional[int] = None

    def acquire(self) -> bool:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        for _ in range(2):
            try:
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, str(os.getpid()).encode())
                return True
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(self.path)
                except OSError:
                    age = 0
                if age <= self.stale_seconds:
                    return False
                try:
                    os.unlink(self.path)
                except OSError:
                    return False
        return False

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            os.unlink(self.path)
        except OSError:
            pass


def load_state(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {"checks": {}, "corrupt": False}
    except (OSError, json.JSONDecodeError):
        quarantine = path + ".corrupt"
        try:
            os.replace(path, quarantine)
        except OSError:
            pass
        return {"checks": {}, "corrupt": True}
    if not isinstance(data, dict) or not isinstance(data.get("checks"), dict):
        return {"checks": {}, "corrupt": True}
    data["corrupt"] = False
    return data


def save_state(path: str, state: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    payload = {"checks": state.get("checks") or {}}
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    os.replace(tmp, path)
