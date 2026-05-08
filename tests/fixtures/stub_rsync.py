#!/usr/bin/env python3
"""Stub ``rsync`` (and ``ssh``) binary for tests.

When invoked under the name ``rsync`` (the test harness symlink / copy),
acts like ``rsync -avz --checksum -e ... <local> <user>@<host>:<remote_path>``
and reads the ``STUB_RSYNC_BEHAVIOR`` environment variable.

When invoked under the name ``ssh`` (the harness installs the same
script under both names), the stub responds to the rsync_ssh hashsum
command shape ``ssh -i <key> -o BatchMode=yes <target> "find <path> -type
f -exec sha256sum {} +"`` by emitting a SHA-256 manifest on stdout.

Behaviors:

- ``success`` (default): rsync copies ``local`` to ``<dest_root>/<remote_path>``;
  ssh emits the hashsum manifest from ``STUB_RSYNC_HASHSUM_PATH`` or by
  walking ``STUB_RSYNC_DEST_ROOT``.
- ``network_error``: stderr "rsync: connection unexpectedly closed"; rc 30.
- ``auth_error``: stderr "Permission denied (publickey)"; rc 5.
- ``hash_mismatch``: stderr "checksum mismatch"; rc 1.
- ``hashsum_success``: same as success but only emits the hashsum manifest
  for an ssh invocation; rsync invocations behave like success.

Destination root comes from ``STUB_RSYNC_DEST_ROOT``.

If ``STUB_RSYNC_RECORD_PATH`` is set, every invocation appends one JSON
array (``sys.argv``) to that file. Default behavior (no env vars set) is
byte-identical to the historical stub.
"""

from __future__ import annotations

import hashlib
import json
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


def _extract_find_path(argv: list[str]) -> str:
    """Return the directory the ssh-side ``find ... -type f`` walks.

    The rsync_ssh hashsum command is a single argv entry of the form
    ``find <path> -type f -exec sha256sum {} +``. We tokenize and pull
    the second word, returning ``""`` if the shape is unexpected.
    """
    for arg in argv:
        if "find " in arg and "-type f" in arg:
            tokens = arg.split()
            if len(tokens) >= 2 and tokens[0] == "find":
                return tokens[1]
    return ""


def _emit_hashsum(argv: list[str]) -> int:
    """Emit a SHA-256 manifest on stdout for the ssh-find-sha256sum verb.

    Source priority:
    1. ``STUB_RSYNC_HASHSUM_PATH``: read the file verbatim.
    2. ``STUB_RSYNC_DEST_ROOT``: walk
       ``<dest_root>/<find_path>`` (parsed from the argv ``find ...``
       shell command) and compute hashes. Paths are emitted in the
       absolute form ``<find_path>/<rel>`` so the rsync_ssh driver's
       prefix-strip step is exercised end-to-end.

    Falls back to empty stdout with rc=0 when neither is set.
    """
    source_path = os.environ.get("STUB_RSYNC_HASHSUM_PATH", "")
    if source_path:
        try:
            sys.stdout.write(Path(source_path).read_text())
        except OSError as exc:
            sys.stderr.write(f"stub_rsync hashsum: cannot read {source_path}: {exc}\n")
            return 1
        return 0

    dest_root = os.environ.get("STUB_RSYNC_DEST_ROOT", "")
    if not dest_root:
        return 0
    find_path = _extract_find_path(argv)
    root = Path(dest_root) / find_path.lstrip("/") if find_path else Path(dest_root)
    if not root.exists():
        return 0
    files = sorted(p for p in root.rglob("*") if p.is_file())
    prefix = find_path.rstrip("/") if find_path else ""
    for f in files:
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        rel = f.relative_to(root).as_posix()
        emitted = f"{prefix}/{rel}" if prefix else rel
        sys.stdout.write(f"{digest}  {emitted}\n")
    return 0


def main() -> int:
    record_path = os.environ.get("STUB_RSYNC_RECORD_PATH")
    if record_path:
        with open(record_path, "a") as f:
            f.write(json.dumps(sys.argv) + "\n")

    behavior = os.environ.get("STUB_RSYNC_BEHAVIOR", "success")
    dest_root = os.environ.get("STUB_RSYNC_DEST_ROOT", "")

    invocation = Path(sys.argv[0]).name
    is_ssh = invocation == "ssh" or (len(sys.argv) >= 2 and "find " in " ".join(sys.argv))

    if is_ssh:
        # ssh-shaped invocation (rsync_ssh hashsum path).
        if behavior == "network_error":
            sys.stderr.write("rsync: connection unexpectedly closed\n")
            return 30
        if behavior == "auth_error":
            sys.stderr.write("Permission denied (publickey)\n")
            return 5
        if behavior in ("success", "hashsum_success"):
            return _emit_hashsum(sys.argv)
        sys.stderr.write(f"stub_rsync ssh: unknown behavior {behavior!r}\n")
        return 2

    if behavior == "network_error":
        sys.stderr.write("rsync: connection unexpectedly closed\n")
        return 30
    if behavior == "auth_error":
        sys.stderr.write("Permission denied (publickey)\n")
        return 5
    if behavior == "hash_mismatch":
        sys.stderr.write("checksum mismatch\n")
        return 1

    if behavior not in ("success", "hashsum_success"):
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
