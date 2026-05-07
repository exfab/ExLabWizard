#!/usr/bin/env python3
"""Stub ``rsync`` binary for tests.

Acts like ``rsync -avz --checksum -e ... <local> <user>@<host>:<remote_path>``
under the test harness's PATH override. Reads the
``STUB_RSYNC_BEHAVIOR`` environment variable to drive deterministic
outcomes:

- ``success`` (default): copies ``local`` to ``<dest_root>/<remote_path>``.
- ``network_error``: stderr "rsync: connection unexpectedly closed"; rc 30.
- ``auth_error``: stderr "Permission denied (publickey)"; rc 5.
- ``hash_mismatch``: stderr "checksum mismatch"; rc 1.

Destination root comes from ``STUB_RSYNC_DEST_ROOT``.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _parse_positional(argv: list[str]) -> tuple[str, str]:
    """Return ``(local, remote_target)`` from an rsync argv.

    The stub recognizes ``-e <cmd>`` and ``--bwlimit=K`` flags, then
    treats the last two positional arguments as ``(local, remote)``.
    """
    positional: list[str] = []
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "-e":
            # consume the value
            i += 2
            continue
        if arg.startswith("--bwlimit"):
            i += 1
            continue
        if arg.startswith("-"):
            i += 1
            continue
        positional.append(arg)
        i += 1
    if len(positional) < 2:
        sys.stderr.write("stub_rsync: missing positional args\n")
        sys.exit(2)
    return positional[-2], positional[-1]


def main() -> int:
    behavior = os.environ.get("STUB_RSYNC_BEHAVIOR", "success")
    dest_root = os.environ.get("STUB_RSYNC_DEST_ROOT", "")

    if behavior == "network_error":
        sys.stderr.write("rsync: connection unexpectedly closed\n")
        return 30
    if behavior == "auth_error":
        sys.stderr.write("Permission denied (publickey)\n")
        return 5
    if behavior == "hash_mismatch":
        sys.stderr.write("checksum mismatch\n")
        return 1

    if behavior != "success":
        sys.stderr.write(f"stub_rsync: unknown behavior {behavior!r}\n")
        return 2

    local, remote_target = _parse_positional(sys.argv)
    if ":" not in remote_target:
        sys.stderr.write(f"stub_rsync: malformed target {remote_target!r}\n")
        return 2
    _, remote_path = remote_target.split(":", 1)

    src = Path(local)
    if not dest_root:
        return 0
    dst = Path(dest_root) / remote_path.lstrip("/")
    dst.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        for item in src.iterdir():
            target = dst / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
    else:
        shutil.copy2(src, dst / src.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
