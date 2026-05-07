#!/usr/bin/env python3
"""Stub ``rclone`` binary for tests.

Acts like ``rclone copy --checksum [--bwlimit X] <local> <remote>:<path>``
under the test harness's PATH override. Reads the
``STUB_RCLONE_BEHAVIOR`` environment variable to drive deterministic
outcomes:

- ``success`` (default): copies ``local`` to ``<dest_root>/<path>`` so the
  verifier sees real content.
- ``network_error``: prints "network timeout" to stderr and exits 1.
- ``auth_error``: prints "401 Unauthorized" to stderr and exits 1.
- ``hash_mismatch``: prints "hash mismatch on file" to stderr and exits 1.

The destination root is read from ``STUB_RCLONE_DEST_ROOT`` so the test
harness can map a fake remote name onto a real on-disk directory.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _parse_args(argv: list[str]) -> tuple[str, str]:
    """Return ``(local, remote_spec)`` from a ``rclone copy`` argv.

    The stub doesn't replicate the full rclone CLI; it only needs the
    last two positional arguments after the ``copy`` verb.
    """
    if len(argv) < 4 or argv[1] != "copy":
        sys.stderr.write(f"stub_rclone: unexpected argv: {argv!r}\n")
        sys.exit(2)
    positional = [a for a in argv[2:] if not a.startswith("--") and not _is_flag_value(a, argv)]
    if len(positional) < 2:
        sys.stderr.write("stub_rclone: missing positional args\n")
        sys.exit(2)
    return positional[-2], positional[-1]


def _is_flag_value(arg: str, argv: list[str]) -> bool:
    """Return True if ``arg`` is the value-half of a ``--flag value`` pair.

    The stub treats ``--bwlimit`` and ``--transfers`` as taking a value;
    other flags it sees are forms like ``--checksum`` (no value).
    """
    flags_with_value = {"--bwlimit", "--transfers"}
    idx = argv.index(arg)
    if idx == 0:
        return False
    return argv[idx - 1] in flags_with_value


def main() -> int:
    behavior = os.environ.get("STUB_RCLONE_BEHAVIOR", "success")
    dest_root = os.environ.get("STUB_RCLONE_DEST_ROOT", "")

    if behavior == "network_error":
        sys.stderr.write("network timeout\n")
        return 1
    if behavior == "auth_error":
        sys.stderr.write("401 Unauthorized\n")
        return 1
    if behavior == "hash_mismatch":
        sys.stderr.write("hash mismatch on file\n")
        return 1

    if behavior != "success":
        sys.stderr.write(f"stub_rclone: unknown behavior {behavior!r}\n")
        return 2

    local, remote_spec = _parse_args(sys.argv)
    # rclone remote spec is "<name>:<path>"; split on the first colon.
    if ":" not in remote_spec:
        sys.stderr.write(f"stub_rclone: malformed remote {remote_spec!r}\n")
        return 2
    _, remote_path = remote_spec.split(":", 1)

    src = Path(local)
    if not dest_root:
        # Without a configured destination root the stub still succeeds
        # silently; the verifier will only need the local manifest.
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
