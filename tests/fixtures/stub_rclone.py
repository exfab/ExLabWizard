#!/usr/bin/env python3
"""Stub ``rclone`` binary for tests.

Acts like the rclone CLI under the test harness's PATH override. Verbs
covered: ``copy``, ``obscure``, ``about``, ``check``, ``lsjson``.
Deterministic outcomes are selected via ``STUB_RCLONE_BEHAVIOR``:

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

Lsjson (``rclone lsjson -R <remote>:<path>``) -- rclone-named-remote
migration: emits a JSON array of every file under
``<dest_root>/<path>`` with its real ``Path`` / ``Size`` / ``ModTime``
so the routine reconcile credits files whose size + modtime match local.
- ``lsjson_empty`` — emits ``[]`` (a remote with nothing) so the
  reconcile finds no matches.
- any other behavior — lists the real dest_root subtree.

Optional env probes (any verb):
- ``STUB_RCLONE_RECORD_PATH`` — append one JSON-array line of
  ``sys.argv`` per invocation.
- ``STUB_RCLONE_ENV_DUMP`` — write ``KEY=VALUE\\n`` lines for every env
  var starting with ``RCLONE_CONFIG_`` to that path so tests can assert
  on env injection.
- ``STUB_RCLONE_REQUIRE_ENV`` — a comma-separated list of env-key
  *suffixes* (e.g. ``TYPE,HOST,USER,PASS``). The stub fails with exit 3
  unless, for some ``RCLONE_CONFIG_<remote>_`` prefix, every listed
  suffix is present (and ``PASS`` is non-empty). This proves the
  production code path injected the inline backend credentials rather
  than the stub silently accepting an unconfigured remote. The
  ``obscure`` verb is exempt (it runs before the env is built).
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
        "--checkers",
        "--config",
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


def _require_env() -> int:
    """Enforce ``STUB_RCLONE_REQUIRE_ENV``; return 0 if satisfied else 3.

    Confirms the production code injected a full inline backend: for at
    least one ``RCLONE_CONFIG_<remote>_`` prefix, every required suffix
    is present (``PASS`` additionally must be non-empty).
    """
    spec = os.environ.get("STUB_RCLONE_REQUIRE_ENV")
    if not spec:
        return 0
    required = [s.strip().upper() for s in spec.split(",") if s.strip()]
    # Group the rclone env by remote prefix (everything up to the suffix).
    prefixes: set[str] = set()
    for key in os.environ:
        if key.startswith("RCLONE_CONFIG_"):
            prefixes.add(key.rsplit("_", 1)[0] + "_")
    for prefix in prefixes:
        ok = True
        for suffix in required:
            value = os.environ.get(f"{prefix}{suffix}")
            if value is None or (suffix == "PASS" and not value):
                ok = False
                break
        if ok:
            return 0
    sys.stderr.write(
        f"stub_rclone: STUB_RCLONE_REQUIRE_ENV={spec!r} not satisfied by injected env\n"
    )
    return 3


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

    # Every non-obscure verb runs after the production code has built the
    # inline backend env, so this is where the injection assertion applies.
    env_rc = _require_env()
    if env_rc != 0:
        return env_rc

    # ---- about ------------------------------------------------------------
    if verb == "about":
        if behavior == "about_auth_error":
            sys.stderr.write("401 Unauthorized\n")
            return 1
        if behavior == "network_error":
            sys.stderr.write("network timeout\n")
            return 1
        # The remote must be a proper rclone spec ("<remote>:"); a bare
        # name is read by real rclone as a local path and fails with
        # "directory not found". Reject it here so a probe that forgets
        # the colon can't pass under the stub (regression guard for the
        # 2026-05-28 equipment-probe fix).
        remote_arg = next((a for a in sys.argv[2:] if not a.startswith("-")), "")
        if ":" not in remote_arg:
            sys.stderr.write(f"stub_rclone about: remote {remote_arg!r} is not a remote spec\n")
            return 2
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

    # ---- lsjson -----------------------------------------------------------
    if verb == "lsjson":
        if behavior == "auth_error":
            sys.stderr.write("401 Unauthorized\n")
            return 1
        if behavior == "network_error":
            sys.stderr.write("network timeout\n")
            return 1
        # The remote spec is the positional arg containing a colon; skip
        # flags and their values (e.g. ``--checkers 8``).
        remote_arg = next((a for a in sys.argv[2:] if ":" in a and not a.startswith("-")), "")
        if not remote_arg:
            sys.stderr.write("stub_rclone lsjson: no remote spec in argv\n")
            return 2
        if behavior == "lsjson_empty" or not dest_root:
            sys.stdout.write("[]")
            return 0
        _, remote_path = remote_arg.split(":", 1)
        listing_root = Path(dest_root) / remote_path.lstrip("/")
        rows: list[dict[str, object]] = []
        if listing_root.is_dir():
            for path in sorted(listing_root.rglob("*")):
                rel = path.relative_to(listing_root).as_posix()
                if path.is_dir():
                    rows.append({"Path": rel, "Size": -1, "ModTime": "", "IsDir": True})
                else:
                    st = path.stat()
                    import datetime as _dt

                    mod = (
                        _dt.datetime.fromtimestamp(st.st_mtime, tz=_dt.UTC)
                        .isoformat()
                        .replace("+00:00", "Z")
                    )
                    rows.append(
                        {"Path": rel, "Size": st.st_size, "ModTime": mod, "IsDir": False}
                    )
        sys.stdout.write(json.dumps(rows))
        return 0

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
