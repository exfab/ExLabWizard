#!/usr/bin/env python3
"""Stub ``rclone`` binary for tests.

Acts like the rclone CLI under the test harness's PATH override. Verbs
covered: ``copy``, ``obscure``, ``about``, ``check``. Deterministic
outcomes are selected via ``STUB_RCLONE_BEHAVIOR``:

Push (``rclone copy ... <local> <remote>:<path>``):
- ``success`` (default) — copies ``local`` to ``<dest_root>/<path>``.
- ``network_error`` — prints "network timeout" to stderr, exits 1.
- ``auth_error`` — prints "401 Unauthorized" to stderr, exits 1.
- ``hash_mismatch`` — prints "hash mismatch on file" to stderr, exits 1.

Obscure (``rclone obscure -``):
- ``obscure_success`` — emits ``STUB_RCLONE_OBSCURE_OUT`` (default
  ``OBSCURED``) to stdout.

About (``rclone about <remote>:``):
- ``about_success`` — emits ``STUB_RCLONE_ABOUT_JSON`` (default
  ``{}``) to stdout.
- ``about_auth_error`` — prints "401 Unauthorized" to stderr, exits 1.

Check (``rclone check --download --combined <out> --files-from <list>
       <local> <remote>:<path>``) -- Phase 2:
- ``check_success`` — writes one ``= <path>`` line per file in
  ``--files-from`` to ``--combined``; exits 0.
- ``check_differ`` — writes ``* <path>`` for every file; exits 1.
- ``check_missing`` — writes ``- <path>`` for every file; exits 1.

Optional env probes (any verb):
- ``STUB_RCLONE_RECORD_PATH`` — append one JSON-array line of
  ``sys.argv`` per invocation.
- ``STUB_RCLONE_ENV_DUMP`` — write ``KEY=VALUE\\n`` lines for every env
  var starting with ``RCLONE_CONFIG_`` to that path so tests can assert
  on env injection.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path


def _parse_copy_args(argv: list[str]) -> tuple[str, str]:
    """Return ``(local, remote_spec)`` from a ``rclone copy`` argv."""
    if len(argv) < 4 or argv[1] != "copy":
        sys.stderr.write(f"stub_rclone: unexpected argv: {argv!r}\n")
        sys.exit(2)
    positional = [a for a in argv[2:] if not a.startswith("--") and not _is_flag_value(a, argv)]
    if len(positional) < 2:
        sys.stderr.write("stub_rclone: missing positional args\n")
        sys.exit(2)
    return positional[-2], positional[-1]


def _is_flag_value(arg: str, argv: list[str]) -> bool:
    """Return True if ``arg`` is the value-half of a ``--flag value`` pair."""
    flags_with_value = {
        "--bwlimit",
        "--transfers",
        "--files-from",
        "--combined",
        "--differ",
        "--missing-on-dst",
        "--missing-on-src",
        "--error",
    }
    idx = argv.index(arg)
    if idx == 0:
        return False
    return argv[idx - 1] in flags_with_value


def _emit_combined(argv: list[str], prefix: str) -> int:
    """Write ``<prefix> <path>`` per file in ``--files-from`` to ``--combined``.

    ``prefix`` is one of ``=`` / ``*`` / ``+`` / ``-`` / ``!``. Returns
    rc=0 for ``=`` (equal) and rc=1 otherwise -- matches real rclone,
    which returns non-zero whenever any file differs.
    """
    files_from = _flag_value(argv, "--files-from")
    combined = _flag_value(argv, "--combined")
    if combined is None:
        sys.stderr.write("stub_rclone check: missing --combined\n")
        return 2
    paths: list[str] = []
    if files_from is not None:
        try:
            paths = [
                line.strip()
                for line in Path(files_from).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except OSError as exc:
            sys.stderr.write(f"stub_rclone check: cannot read files-from: {exc}\n")
            return 2
    with Path(combined).open("w", encoding="utf-8") as f:
        for path in paths:
            f.write(f"{prefix} {path}\n")
    return 0 if prefix == "=" else 1


def _flag_value(argv: list[str], flag: str) -> str | None:
    """Return the value following ``flag`` in ``argv`` or ``None``."""
    try:
        idx = argv.index(flag)
    except ValueError:
        return None
    if idx + 1 >= len(argv):
        return None
    return argv[idx + 1]


def _dump_env() -> None:
    """When ``STUB_RCLONE_ENV_DUMP`` is set, write the rclone-prefixed env."""
    dump = os.environ.get("STUB_RCLONE_ENV_DUMP")
    if not dump:
        return
    with open(dump, "w") as f:
        for key, value in sorted(os.environ.items()):
            if key.startswith("RCLONE_CONFIG_"):
                f.write(f"{key}={value}\n")


def main() -> int:
    record_path = os.environ.get("STUB_RCLONE_RECORD_PATH")
    if record_path:
        with open(record_path, "a") as f:
            f.write(json.dumps(sys.argv) + "\n")
    _dump_env()

    behavior = os.environ.get("STUB_RCLONE_BEHAVIOR", "success")
    dest_root = os.environ.get("STUB_RCLONE_DEST_ROOT", "")

    verb = sys.argv[1] if len(sys.argv) >= 2 else ""

    # ---- obscure ----------------------------------------------------------
    if verb == "obscure":
        # Consume stdin so the parent's communicate(input=...) doesn't block.
        import contextlib

        with contextlib.suppress(OSError):
            sys.stdin.read()
        # Production code always invokes obscure before push / about / check
        # / hashsum, so STUB_RCLONE_BEHAVIOR controlling auth_error on
        # *those* verbs must not short-circuit obscure too -- tests need to
        # observe the failure on the actual verb. Obscure honors only its
        # own dedicated `obscure_*` behaviors.
        if behavior in ("obscure_network_error",):
            sys.stderr.write("network timeout\n")
            return 1
        if behavior in ("obscure_auth_error",):
            sys.stderr.write("401 Unauthorized\n")
            return 1
        sys.stdout.write(os.environ.get("STUB_RCLONE_OBSCURE_OUT", "OBSCURED") + "\n")
        return 0

    # ---- about ------------------------------------------------------------
    if verb == "about":
        if behavior == "about_auth_error":
            sys.stderr.write("401 Unauthorized\n")
            return 1
        if behavior == "network_error":
            sys.stderr.write("network timeout\n")
            return 1
        sys.stdout.write(os.environ.get("STUB_RCLONE_ABOUT_JSON", "{}"))
        return 0

    # ---- check ------------------------------------------------------------
    if verb == "check":
        if behavior == "auth_error":
            sys.stderr.write("401 Unauthorized\n")
            return 1
        if behavior == "network_error":
            sys.stderr.write("network timeout\n")
            return 1
        if behavior == "check_differ":
            return _emit_combined(sys.argv, "*")
        if behavior == "check_missing":
            return _emit_combined(sys.argv, "-")
        # default / "success" / "check_success" -> all equal
        return _emit_combined(sys.argv, "=")

    # ---- copy (default verb) ---------------------------------------------
    if behavior == "network_error":
        sys.stderr.write("network timeout\n")
        return 1
    if behavior == "auth_error":
        sys.stderr.write("401 Unauthorized\n")
        return 1
    if behavior == "hash_mismatch":
        sys.stderr.write("hash mismatch on file\n")
        return 1

    if behavior not in ("success", "obscure_success", "about_success"):
        sys.stderr.write(f"stub_rclone: unknown behavior {behavior!r}\n")
        return 2

    local, remote_spec = _parse_copy_args(sys.argv)
    if ":" not in remote_spec:
        sys.stderr.write(f"stub_rclone: malformed remote {remote_spec!r}\n")
        return 2
    _, remote_path = remote_spec.split(":", 1)

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
