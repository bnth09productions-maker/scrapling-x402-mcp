"""Narrow cutover: counterparty check $0.004 -> $0.002.

Preflight checks every anchor in every file before any backup or replace.
A failed preflight does not write. A failure during replace restores the
files already swapped. Bytes, newline style, and permission bits are kept.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import time
from dataclasses import dataclass
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


class CutoverError(Exception):
    """Preflight or replace failed. Target files are unchanged when this is raised."""


@dataclass
class Planned:
    rel: str
    path: Path
    original: bytes
    updated: bytes
    mode: int
    changed: bool


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _files(edits) -> dict[str, list[tuple[str, str]]]:
    grouped: dict[str, list[tuple[str, str]]] = {}
    for rel, old, new in edits:
        grouped.setdefault(rel, []).append((old, new))
    return grouped


def preflight(root: Path, edits=EDITS) -> list[Planned]:
    """Read every target. Raise CutoverError before the caller writes."""
    grouped = _files(edits)
    missing = [rel for rel in grouped if not (root / rel).is_file() or (root / rel).is_symlink()]
    if missing:
        raise CutoverError("missing or not a regular file: " + ", ".join(missing))
    planned: list[Planned] = []
    problems: list[str] = []
    for rel, pairs in grouped.items():
        path = root / rel
        original = path.read_bytes()
        try:
            text = original.decode("utf-8")
        except UnicodeDecodeError:
            problems.append(f"{rel} is not utf-8")
            continue
        updated = text
        changed = False
        for old, new in pairs:
            old_n, new_n = updated.count(old), updated.count(new)
            if old_n == 1 and new_n == 0:
                updated = updated.replace(old, new, 1)
                changed = True
            elif old_n == 0 and new_n == 1:
                continue
            else:
                problems.append(f"{rel} old={old_n} new={new_n}")
        new_bytes = updated.encode("utf-8")
        if not changed and new_bytes != original:
            problems.append(f"{rel} decode/encode would change bytes")
        planned.append(Planned(rel, path, original, new_bytes, path.stat().st_mode, changed))
    if problems:
        raise CutoverError("preflight failed, no files written: " + "; ".join(problems))
    changed_rels = [item.rel for item in planned if item.changed]
    ready_rels = [item.rel for item in planned if not item.changed]
    if changed_rels and ready_rels:
        raise CutoverError(
            "preflight failed, mixed state, no files written: still old "
            + ", ".join(changed_rels)
            + "; already new "
            + ", ".join(ready_rels)
        )
    return planned


def _write_new(path: Path, data: bytes, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, mode & 0o777)
    try:
        os.write(fd, data)
        os.fsync(fd)
    except Exception:
        os.close(fd)
        path.unlink(missing_ok=True)
        raise
    os.close(fd)
    os.chmod(path, mode & 0o777)


def _tmp(path: Path, label: str) -> Path:
    return path.with_name(path.name + label)


def _discard_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _restore(done: list[Planned]) -> None:
    for item in reversed(done):
        restore = _tmp(item.path, ".counterparty-restore")
        _write_new(restore, item.original, item.mode)
        os.replace(restore, item.path)
        os.chmod(item.path, item.mode & 0o777)


def apply_tree(root: Path, edits=EDITS, backup_dir: Path | None = None, replace=os.replace) -> list[str]:
    planned = preflight(root, edits)
    if not any(item.changed for item in planned):
        return [f"already {item.rel}" for item in planned]
    parent = None
    parent_created = False
    if backup_dir is not None:
        backup_root = Path(backup_dir)
    else:
        parent = root / ".counterparty-price-backups"
        parent_created = not parent.exists()
        parent.mkdir(parents=True, exist_ok=True)
        backup_root = parent / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{os.getpid()}-{time.time_ns()}"
    temps: list[Path] = []
    created = False
    try:
        backup_root.mkdir(parents=True, exist_ok=False)
        created = True
        for item in planned:
            if not item.changed:
                continue
            dest = backup_root / item.rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            _write_new(dest, item.original, item.mode)
        for item in planned:
            if not item.changed:
                continue
            tmp = _tmp(item.path, ".counterparty-tmp")
            _write_new(tmp, item.updated, item.mode)
            temps.append(tmp)
    except Exception as exc:
        for tmp in temps:
            tmp.unlink(missing_ok=True)
        if created:
            _discard_tree(backup_root)
        if parent_created and parent is not None and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
        raise CutoverError(f"staging failed, no files replaced: {exc}") from exc

    done: list[Planned] = []
    try:
        for item in planned:
            if not item.changed:
                continue
            tmp = _tmp(item.path, ".counterparty-tmp")
            replace(tmp, item.path)
            os.chmod(item.path, item.mode & 0o777)
            done.append(item)
    except Exception as exc:
        try:
            _restore(done)
        except Exception as restore_exc:
            raise CutoverError(
                f"replace failed ({exc}) and restore failed ({restore_exc}). "
                f"Backups remain in {backup_root}"
            ) from restore_exc
        for tmp in temps:
            tmp.unlink(missing_ok=True)
        raise CutoverError(f"replace failed and the original bytes were restored: {exc}. Backups remain in {backup_root}") from exc

    notes = [f"backup {backup_root}"]
    for item in planned:
        if item.changed:
            notes.append(f"patched {item.rel} {hashlib.sha256(item.original).hexdigest()} -> {sha256(item.path)}")
    return notes


def rollback_tree(root: Path, edits=EDITS, backup_dir: Path | None = None, replace=os.replace) -> list[str]:
    reversed_edits = tuple((rel, new, old) for rel, old, new in edits)
    return apply_tree(root, reversed_edits, backup_dir=backup_dir, replace=replace)


def main(argv: list[str]) -> int:
    args = argv[1:]
    rollback = False
    preflight_only = False
    backup_dir = None
    positionals = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--rollback":
            rollback = True
        elif arg == "--preflight":
            preflight_only = True
        elif arg == "--backup-dir":
            index += 1
            if index >= len(args):
                print("usage: apply_counterparty_price.py [--preflight] [--rollback] [--backup-dir DIR] ROOT", file=sys.stderr)
                return 2
            backup_dir = Path(args[index])
        else:
            positionals.append(arg)
        index += 1
    if len(positionals) != 1:
        print("usage: apply_counterparty_price.py [--preflight] [--rollback] [--backup-dir DIR] ROOT", file=sys.stderr)
        return 2
    root = Path(positionals[0]).resolve()
    try:
        if preflight_only:
            edits = tuple((rel, new, old) for rel, old, new in EDITS) if rollback else EDITS
            planned = preflight(root, edits)
            for item in planned:
                state = "change" if item.changed else "already"
                print(f"preflight {state} {item.rel}")
            return 0
        notes = rollback_tree(root, backup_dir=backup_dir) if rollback else apply_tree(root, backup_dir=backup_dir)
    except CutoverError as exc:
        print(f"abort: {exc}", file=sys.stderr)
        return 1
    for line in notes:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
