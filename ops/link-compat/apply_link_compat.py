"""Install the link and MCP method fixes. Preflight every anchor before any write.

Does not replace gateway.py or mcp_server.py. Does not change prices.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
NEW_FILES = (
    ("scrapling/monetize/link_compat.py", HERE / "files" / "link_compat.py"),
    ("scrapling/monetize/mcp_method.py", HERE / "files" / "mcp_method.py"),
)
EDITS = (
    (
        "scrapling/monetize/gateway.py",
        "from .mcp_server import register_mcp_routes\n",
        "from .link_compat import register_link_compat\nfrom .mcp_server import register_mcp_routes\n",
    ),
    (
        "scrapling/monetize/gateway.py",
        "    register_mcp_routes(app, _public_base_url)\n",
        "    register_mcp_routes(app, _public_base_url)\n    register_link_compat(app)\n",
    ),
    (
        "scrapling/monetize/mcp_server.py",
        "from scrapling.core._compat import json_dumps_bytes, json_loads\n",
        "from scrapling.core._compat import json_dumps_bytes, json_loads\n\n"
        "from .mcp_method import method_decision, protocol_error\n",
    ),
    (
        "scrapling/monetize/mcp_server.py",
        "    async def mcp_endpoint(request: Request) -> Response:\n        try:\n",
        "    async def mcp_endpoint(request: Request) -> Response:\n"
        "        rejected = protocol_error(request.headers.get(\"mcp-protocol-version\"))\n"
        "        if rejected is not None:\n"
        "            return JSONResponse(rejected, status_code=400)\n"
        "        try:\n",
    ),
    (
        "scrapling/monetize/mcp_server.py",
        "    async def mcp_no_stream() -> Response:\n"
        "        # Stateless server: no server-initiated SSE stream and no sessions to delete.\n"
        "        return Response(status_code=405, headers={\"Allow\": \"POST\"})\n",
        "    @app.options(\"/mcp\", include_in_schema=False)\n"
        "    @app.options(\"/api/mcp\", include_in_schema=False)\n"
        "    @app.options(\"/mcp/v1\", include_in_schema=False)\n"
        "    async def mcp_options() -> Response:\n"
        "        status, headers = method_decision(\"OPTIONS\")\n"
        "        return Response(status_code=status, headers=headers)\n\n"
        "    async def mcp_no_stream() -> Response:\n"
        "        # Stateless: no SSE stream and no session. Do not answer 200.\n"
        "        status, headers = method_decision(\"GET\")\n"
        "        return Response(status_code=status, headers=headers)\n",
    ),
)


class CutoverError(Exception):
    pass


def _grouped():
    out = {}
    for rel, old, new in EDITS:
        out.setdefault(rel, []).append((old, new))
    return out


def preflight(root: Path) -> list:
    grouped = _grouped()
    problems = []
    planned = []
    for rel, pairs in grouped.items():
        path = root / rel
        if not path.is_file() or path.is_symlink():
            problems.append(f"missing {rel}")
            continue
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
            # A finished replacement still contains its old anchor. That is
            # already applied when the new block occurs once and accounts for
            # every copy of the old anchor.
            if new_n == 1 and old in new and old_n == new.count(old):
                continue
            if old_n == 1 and new_n == 0:
                updated = updated.replace(old, new, 1)
                changed = True
            elif old_n == 0 and new_n == 1:
                continue
            else:
                problems.append(f"{rel} old={old_n} new={new_n}")
        planned.append((rel, path, original, updated.encode("utf-8"), path.stat().st_mode, changed))
    creates = []
    for rel, src in NEW_FILES:
        if not src.is_file():
            problems.append(f"package missing {src.name}")
            continue
        dest = root / rel
        data = src.read_bytes()
        if dest.exists():
            if dest.is_symlink() or not dest.is_file():
                problems.append(f"{rel} is not a regular file")
            elif dest.read_bytes() != data:
                problems.append(f"{rel} exists and differs; not overwritten")
            else:
                creates.append((rel, dest, None, data, dest.stat().st_mode, False))
        else:
            creates.append((rel, dest, None, data, 0o644, True))
    if problems:
        raise CutoverError("preflight failed, no files written: " + "; ".join(problems))
    changed = [rel for rel, *_rest, flag in planned if flag]
    idle = [rel for rel, *_rest, flag in planned if not flag]
    if changed and idle:
        raise CutoverError("preflight failed, mixed state, no files written: " + ",".join(changed))
    return planned, creates


def _write_new(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".linkcompat-tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode & 0o777)
    try:
        os.write(fd, data)
        os.fsync(fd)
    except Exception:
        os.close(fd)
        tmp.unlink(missing_ok=True)
        raise
    os.close(fd)
    os.chmod(tmp, mode & 0o777)
    os.replace(tmp, path)


def apply_tree(root: Path, backup_dir: Path | None = None) -> list[str]:
    planned, creates = preflight(root)
    notes = []
    file_changes = [item for item in planned if item[5]]
    new_changes = [item for item in creates if item[5]]
    if not file_changes and not new_changes:
        return [f"already {item[0]}" for item in planned + creates]
    parent = root / ".link-compat-backups"
    parent.mkdir(parents=True, exist_ok=True)
    backup_root = Path(backup_dir) if backup_dir else parent / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{os.getpid()}-{time.time_ns()}"
    backup_root.mkdir(parents=True, exist_ok=False)
    originals = []
    try:
        for rel, path, original, updated, mode, changed in file_changes:
            dest = backup_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            _write_new(dest, original, mode)
            originals.append((path, original, mode))
            _write_new(path, updated, mode)
            notes.append(f"patched {rel} {hashlib.sha256(original).hexdigest()} -> {hashlib.sha256(path.read_bytes()).hexdigest()}")
        for rel, dest, _old, data, mode, changed in new_changes:
            _write_new(dest, data, mode)
            notes.append(f"added {rel}")
    except Exception as exc:
        for path, original, mode in reversed(originals):
            _write_new(path, original, mode)
        for rel, dest, _old, _data, _mode, changed in new_changes:
            if dest.exists() and dest.is_file():
                dest.unlink()
        raise CutoverError(f"write failed and originals were restored: {exc}") from exc
    notes.insert(0, f"backup {backup_root}")
    return notes


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: apply_link_compat.py ROOT", file=sys.stderr)
        return 2
    try:
        for line in apply_tree(Path(argv[1]).resolve()):
            print(line)
    except CutoverError as exc:
        print(f"abort: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
