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
- ``hashsum_success``: emits a SHA-256 manifest to stdout for
  ``rclone hashsum sha256 <remote>``. The manifest source is
  ``STUB_RCLONE_HASHSUM_PATH`` (read verbatim) when set, otherwise it is
  computed by walking ``STUB_RCLONE_DEST_ROOT``.

The destination root is read from ``STUB_RCLONE_DEST_ROOT`` so the test
harness can map a fake remote name onto a real on-disk directory.

If ``STUB_RCLONE_RECORD_PATH`` is set, every invocation appends one JSON
array (``sys.argv``) to that file. This makes argv assertions in tests
trivial and is silent when the env var is unset (default behavior is
byte-identical to the historical stub).
"""

from __future__ import annotations

import hashlib
import json
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


def _emit_hashsum(argv: list[str]) -> int:
    """Emit a SHA-256 manifest on stdout for the ``hashsum`` verb.

    Source priority:
    1. ``STUB_RCLONE_HASHSUM_PATH``: read the file verbatim.
    2. ``STUB_RCLONE_DEST_ROOT``: walk
       ``<dest_root>/<remote_path>`` (parsed from the trailing
       ``<remote>:<path>`` arg) and compute hashes. Paths are emitted
       relative to that subtree so the verifier sees the same keys it
       computes locally.

    Falls back to an empty manifest with rc=0 when neither is set.
    """
    source_path = os.environ.get("STUB_RCLONE_HASHSUM_PATH", "")
    if source_path:
        try:
            sys.stdout.write(Path(source_path).read_text())
        except OSError as exc:
            sys.stderr.write(f"stub_rclone hashsum: cannot read {source_path}: {exc}\n")
            return 1
        return 0

    dest_root = os.environ.get("STUB_RCLONE_DEST_ROOT", "")
    if not dest_root:
        return 0

    # Argv shape: rclone hashsum sha256 <remote>:<path>
    target_arg = argv[-1] if len(argv) >= 4 else ""
    remote_path = ""
    if ":" in target_arg:
        _, remote_path = target_arg.split(":", 1)
    root = Path(dest_root) / remote_path.lstrip("/") if remote_path else Path(dest_root)
    if not root.exists():
        return 0
    files = sorted(p for p in root.rglob("*") if p.is_file())
    for f in files:
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        rel = f.relative_to(root).as_posix()
        sys.stdout.write(f"{digest}  {rel}\n")
    return 0


def main() -> int:
    record_path = os.environ.get("STUB_RCLONE_RECORD_PATH")
    if record_path:
        with open(record_path, "a") as f:
            f.write(json.dumps(sys.argv) + "\n")

    behavior = os.environ.get("STUB_RCLONE_BEHAVIOR", "success")
    dest_root = os.environ.get("STUB_RCLONE_DEST_ROOT", "")

    # Hashsum verb: branch before any of the copy-only behaviors so the
    # copy path is unaffected by the new behavior.
    if len(sys.argv) >= 2 and sys.argv[1] == "hashsum":
        if behavior == "network_error":
            sys.stderr.write("network timeout\n")
            return 1
        if behavior == "auth_error":
            sys.stderr.write("401 Unauthorized\n")
            return 1
        if behavior in ("success", "hashsum_success"):
            return _emit_hashsum(sys.argv)
        sys.stderr.write(f"stub_rclone hashsum: unknown behavior {behavior!r}\n")
        return 2

    if behavior == "network_error":
        sys.stderr.write("network timeout\n")
        return 1
    if behavior == "auth_error":
        sys.stderr.write("401 Unauthorized\n")
        return 1
    if behavior == "hash_mismatch":
        sys.stderr.write("hash mismatch on file\n")
        return 1

    if behavior not in ("success", "hashsum_success"):
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
