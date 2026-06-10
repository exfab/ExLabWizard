#!/usr/bin/env python3
"""Stub ``rsync`` binary for tests.

rsync-over-ssh NAS transport (2026-06-10). Behaviors are selected via
``STUB_RSYNC_BEHAVIOR``:

- ``success`` (default): push copies the ``--files-from`` subset (or the
  whole source tree) into ``STUB_RSYNC_DEST_ROOT/<remote-path>``;
  ``--list-only`` emits a real listing of that subtree; ``-n`` dry-runs
  emit nothing (all equal).
- ``network_error`` — "Connection refused" on stderr, exit 255.
- ``auth_error`` — "Permission denied (publickey)." on stderr, exit 255.
- ``check_differ`` — dry-run itemizes every files-from entry as
  ``>fcst...... <path>``.
- ``check_missing`` — dry-run itemizes every entry as ``>f+++++++++ <path>``.

``STUB_RSYNC_RECORD_PATH`` appends one JSON line of argv per invocation.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path


def _flag_value(argv: list[str], prefix: str) -> str | None:
    for arg in argv:
        if arg.startswith(prefix + "="):
            return arg.split("=", 1)[1]
    return None


def _positional(argv: list[str]) -> list[str]:
    out: list[str] = []
    skip_next = False
    for arg in argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if arg == "-e":
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        out.append(arg)
    return out


def _files_from_paths(argv: list[str]) -> list[str]:
    listing = _flag_value(argv, "--files-from")
    if listing is None:
        return []
    try:
        return [
            line.strip()
            for line in Path(listing).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except OSError:
        return []


def _remote_path(spec: str) -> str:
    # "user@host:/path" -> "/path"
    return spec.split(":", 1)[1] if ":" in spec else spec


def main() -> int:
    argv = sys.argv
    record = os.environ.get("STUB_RSYNC_RECORD_PATH")
    if record:
        with open(record, "a") as f:
            f.write(json.dumps(argv) + "\n")

    behavior = os.environ.get("STUB_RSYNC_BEHAVIOR", "success")
    dest_root = os.environ.get("STUB_RSYNC_DEST_ROOT", "")

    if behavior == "network_error":
        sys.stderr.write("ssh: connect to host nas01: Connection refused\n")
        return 255
    if behavior == "auth_error":
        sys.stderr.write("Permission denied (publickey).\n")
        return 255

    positional = _positional(argv)

    # ---- list-only --------------------------------------------------------
    if "--list-only" in argv:
        target = _remote_path(positional[-1]).rstrip("/")
        root = Path(dest_root) / target.lstrip("/")
        if not root.is_dir():
            sys.stderr.write(f'rsync: change_dir "{target}" failed\n')
            return 23
        recursive = "-r" in argv
        paths = sorted(root.rglob("*")) if recursive else sorted(root.iterdir())
        for path in [root, *paths]:
            st = path.stat()
            stamp = datetime.fromtimestamp(st.st_mtime).strftime("%Y/%m/%d %H:%M:%S")
            rel = "." if path == root else path.relative_to(root).as_posix()
            kind = "d" if path.is_dir() else "-"
            sys.stdout.write(f"{kind}rw-r--r-- {st.st_size:>14,} {stamp} {rel}\n")
        return 0

    # ---- dry-run check ----------------------------------------------------
    # Exact-match the driver's dry-run spellings; a substring scan for "n"
    # would misroute any future flag merely containing the letter.
    if "-n" in argv or "--dry-run" in argv or "-rni" in argv:
        rels = _files_from_paths(argv)
        if behavior == "check_differ":
            for rel in rels:
                sys.stdout.write(f">fcst...... {rel}\n")
        elif behavior == "check_missing":
            for rel in rels:
                sys.stdout.write(f">f+++++++++ {rel}\n")
        return 0

    # ---- push -------------------------------------------------------------
    if len(positional) < 2:
        sys.stderr.write(f"stub_rsync: unexpected argv {argv!r}\n")
        return 1
    src = Path(positional[-2].rstrip("/"))
    if not dest_root:
        return 0
    dst = Path(dest_root) / _remote_path(positional[-1]).lstrip("/")
    rels = _files_from_paths(argv)
    if rels:
        for rel in rels:
            source = src / rel
            if not source.is_file():
                continue
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    else:
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
