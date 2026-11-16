"""In-process 4-hour loop. A background launch does not survive reboot."""
from __future__ import annotations

import os
import signal
import time
from typing import Callable, Optional

DEFAULT_INTERVAL = 4 * 60 * 60
MIN_INTERVAL = 60


class StopFlag:
    def __init__(self) -> None:
        self.stop = False

    def handle(self, signum, frame) -> None:
        self.stop = True


def interval_of(config: dict, override: Optional[float]) -> float:
    raw = float(config.get("interval_seconds") or DEFAULT_INTERVAL) if override is None else float(override)
    if raw < MIN_INTERVAL:
        raise ValueError("interval must be at least 60 seconds")
    return raw


def wait(seconds: float, stop: StopFlag, slice_s: float = 1.0) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while not stop.stop:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        try:
            time.sleep(min(slice_s, remaining))
        except InterruptedError:
            continue


def run_loop(config, root: str, interval: float, stop: StopFlag, execute_fn: Callable, slice_s: float = 1.0) -> None:
    """Run once, then wait the full interval. Missed time is not replayed as a burst."""
    while not stop.stop:
        execute_fn(config, root)
        if stop.stop:
            return
        wait(interval, stop, slice_s=slice_s)


def pid_path(state_dir: str) -> str:
    return os.path.join(state_dir, "daemon.pid")


def read_pid(path: str) -> Optional[int]:
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read().strip()
        return int(text)
    except (OSError, ValueError):
        return None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    proc_stat = f"/proc/{pid}/stat"
    if os.path.exists("/proc"):
        try:
            with open(proc_stat, encoding="utf-8") as handle:
                state = handle.read().rsplit(")", 1)[-1].split()[0]
            return state not in ("Z", "X")
        except OSError:
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def write_pid(path: str, pid: int) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(str(pid))
    os.replace(tmp, path)


def remove_pid(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def stop_daemon(path: str, wait_seconds: float = 10) -> str:
    pid = read_pid(path)
    if pid is None:
        return "not_running"
    if not pid_alive(pid):
        remove_pid(path)
        return "not_running"
    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if not pid_alive(pid):
            remove_pid(path)
            return "stopped"
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    time.sleep(0.1)
    if pid_alive(pid):
        return "failed"
    remove_pid(path)
    return "stopped"


def append_log(path: str, line: str, max_bytes: int = 1_000_000, backups: int = 5) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.exists(path) and os.path.getsize(path) >= max_bytes:
        oldest = f"{path}.{backups}"
        if os.path.exists(oldest):
            os.remove(oldest)
        for index in range(backups, 1, -1):
            src = f"{path}.{index - 1}"
            dst = f"{path}.{index}"
            if os.path.exists(src):
                os.replace(src, dst)
        os.replace(path, path + ".1")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line)
        if not line.endswith("\n"):
            handle.write("\n")


def install_stop_signals(stop: StopFlag) -> None:
    signal.signal(signal.SIGTERM, stop.handle)
    signal.signal(signal.SIGINT, stop.handle)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, signal.SIG_IGN)


def daemonize() -> None:
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
