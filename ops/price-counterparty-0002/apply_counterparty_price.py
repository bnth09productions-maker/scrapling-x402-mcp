"""Narrow cutover: counterparty check $0.004 -> $0.002.

Aborts unless each old literal is present once, or the new literal is already
present once and the old one is gone. Does not replace whole files.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

EDITS = (
    (
        "scrapling/monetize/agent_api.py",
        '"path": "/v1/check/counterparty", "price": 0.004,',
        '"path": "/v1/check/counterparty", "price": 0.002,',
    ),
    (
        "scrapling/monetize/mcp_server.py",
        "Counterparty Check ($0.004)",
        "Counterparty Check ($0.002)",
    ),
    (
        "scrapling/monetize/mcp_server.py",
        "PAID $0.004 USDC. Before paying or trading:",
        "PAID $0.002 USDC. Before paying or trading:",
    ),
    (
        "scrapling/monetize/upd25.py",
        "before you pay or trade ($0.004)",
        "before you pay or trade ($0.002)",
    ),
    (
        "scrapling/monetize/gateway.py",
        "/v1/check/counterparty?chain=&address= $0.004 ",
        "/v1/check/counterparty?chain=&address= $0.002 ",
    ),
    (
        "scrapling/monetize/skill_scrapling_data.md",
        "| `/v1/check/counterparty?chain=ethereum&address=0x...` | $0.004 |",
        "| `/v1/check/counterparty?chain=ethereum&address=0x...` | $0.002 |",
    ),
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply_tree(root: Path) -> list[str]:
    notes = []
    by_file: dict[str, list[tuple[str, str]]] = {}
    for rel, old, new in EDITS:
        by_file.setdefault(rel, []).append((old, new))
    for rel, pairs in by_file.items():
        path = root / rel
        if not path.is_file():
            raise SystemExit(f"abort: missing {rel}")
        text = path.read_text(encoding="utf-8")
        updated = text
        changed = False
        for old, new in pairs:
            old_n, new_n = updated.count(old), updated.count(new)
            if old_n == 1 and new_n == 0:
                updated = updated.replace(old, new, 1)
                changed = True
            elif old_n == 0 and new_n == 1:
                notes.append(f"already {rel}")
            else:
                raise SystemExit(f"abort: {rel} old={old_n} new={new_n}")
        if changed:
            before = sha256(path)
            path.write_text(updated, encoding="utf-8")
            notes.append(f"patched {rel} {before} -> {sha256(path)}")
    return notes


def rollback_tree(root: Path) -> list[str]:
    global EDITS
    forward = EDITS
    EDITS = tuple((rel, new, old) for rel, old, new in forward)
    try:
        return apply_tree(root)
    finally:
        EDITS = forward


def main(argv: list[str]) -> int:
    args = argv[1:]
    rollback = False
    if args and args[0] == "--rollback":
        rollback = True
        args = args[1:]
    if len(args) != 1:
        print("usage: apply_counterparty_price.py [--rollback] ROOT", file=sys.stderr)
        return 2
    root = Path(args[0]).resolve()
    for line in (rollback_tree(root) if rollback else apply_tree(root)):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
