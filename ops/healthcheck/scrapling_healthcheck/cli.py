"""python -m scrapling_healthcheck once|daemon|stop --config config/healthcheck.json"""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .daemon import (
    StopFlag,
    append_log,
    daemonize,
    install_stop_signals,
    interval_of,
    pid_alive,
    pid_path,
    read_pid,
    remove_pid,
    run_loop,
    stop_daemon,
    write_pid,
)
from .engine import execute, load_config


def _root(args) -> str:
    if args.root:
        return args.root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _state_dir(config, root: str) -> str:
    raw = config.get("state_dir") or "var/state"
    return raw if os.path.isabs(raw) else os.path.join(root, raw)


def _log_dir(config, root: str) -> str:
    raw = config.get("log_dir") or "var/logs"
    return raw if os.path.isabs(raw) else os.path.join(root, raw)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="scrapling_healthcheck")
    parser.add_argument("command", choices=("once", "daemon", "stop"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--root", default="")
    parser.add_argument("--detach", action="store_true")
    parser.add_argument("--interval", type=float, default=None)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"error exit=2 {exc.__class__.__name__}")
        return 2
    root = _root(args)
    if args.command == "once":
        return execute(config, root)
    if args.command == "stop":
        status = stop_daemon(pid_path(_state_dir(config, root)))
        print(status)
        return 0 if status in ("stopped", "not_running") else 2
    try:
        interval = interval_of(config, args.interval)
    except ValueError as exc:
        print(f"error exit=2 {exc}")
        return 2
    state = pid_path(_state_dir(config, root))
    existing = read_pid(state)
    if existing and pid_alive(existing):
        print(f"error exit=2 already running pid {existing}")
        return 2
    if existing:
        remove_pid(state)
    log_path = os.path.join(_log_dir(config, root), "daemon.log")
    if args.detach:
        if not hasattr(os, "fork"):
            print("error exit=2 detach is not available on this platform")
            return 2
        daemonize()
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        devnull = os.open(os.devnull, os.O_RDONLY)
        os.dup2(devnull, 0)
        os.dup2(log_fd, 1)
        os.dup2(log_fd, 2)
        os.close(devnull)
        os.close(log_fd)
    write_pid(state, os.getpid())
    stop = StopFlag()
    install_stop_signals(stop)
    append_log(log_path, f"daemon start pid={os.getpid()} interval={int(interval)} version={__version__}")
    try:
        run_loop(config, root, interval, stop, execute)
    finally:
        append_log(log_path, f"daemon stop pid={os.getpid()}")
        remove_pid(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
