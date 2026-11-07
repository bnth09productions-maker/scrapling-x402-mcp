"""python -m scrapling_healthcheck once --config config/healthcheck.json"""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .engine import execute, load_config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="scrapling_healthcheck")
    parser.add_argument("command", choices=("once",))
    parser.add_argument("--config", required=True)
    parser.add_argument("--root", default="")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"error exit=2 {exc.__class__.__name__}")
        return 2
    root = args.root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if args.command == "once":
        return execute(config, root)
    return 2


if __name__ == "__main__":
    sys.exit(main())
