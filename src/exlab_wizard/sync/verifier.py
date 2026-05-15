"""SHA-256 hash verifier for synced runs. Backend Spec §7.1.4.

After a transport reports success, the job moves to ``AWAITING_VERIFY``.
The verifier walks the local subtree, computes a SHA-256 per file, writes
the manifest to ``<run>/.exlab-wizard/checksums.sha256`` (one ``sha256
path`` line per file), and compares against a remote manifest (or against
itself for self-consistency tests).

The on-disk manifest format mirrors the output of the ``sha256sum`` UNIX
tool: each line has ``<hex-sha256>  <relative-path>``. ``ingest.json`` and
the cache spec already reference ``.exlab-wizard/checksums.sha256``;
this module is the writer.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from exlab_wizard import paths
from exlab_wizard.constants import CACHE_DIR_NAME, CHECKSUMS_RELATIVE
from exlab_wizard.io import atomic_write_bytes
from exlab_wizard.logging import get_logger

if TYPE_CHECKING:
    from exlab_wizard.sync.transports import TransportErrorKind

__all__ = [
    "Verifier",
    "VerifyResult",
    "format_manifest",
    "parse_manifest",
]

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """Outcome of a verifier pass.

    ``ok`` is True iff every file in the manifest matched. ``mismatched``
    lists relative paths whose hash differed; ``missing`` lists paths in
    the manifest that no longer exist on disk; ``extra`` lists files on
    disk that were not in the manifest (informational only).

    ``error_kind`` is set when the remote-hash probe could not complete
    (the underlying :class:`exlab_wizard.sync.transports.TransportError`
    classified the failure as AUTH / NETWORK / UNKNOWN). The queue worker
    keys off this field to route via the §7.1.5 retry policy: AUTH ->
    terminal FAILED, NETWORK / UNKNOWN -> backoff retry. ``None`` for
    every non-remote-probe outcome.
    """

    ok: bool
    mismatched: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    manifest: dict[str, str] = field(default_factory=dict)
    error_kind: TransportErrorKind | None = None


def _iter_files(run_path: Path) -> list[Path]:
    """Return every regular file under ``run_path`` (depth-first).

    Uses ``Path.rglob('*')`` and filters to regular files. The manifest
    format is independent of walk order, but we sort the result by
    relative path so the manifest file is reproducible byte-for-byte.
    """
    files: list[Path] = []
    for path in run_path.rglob("*"):
        if path.is_file():
            files.append(path)
    return files


def _is_inside_cache_dir(rel_path: Path) -> bool:
    """Return True iff a relative path is inside ``.exlab-wizard/``.

    The checksum manifest itself lives under ``.exlab-wizard/`` and we
    do NOT include the cache directory in the manifest -- otherwise the
    manifest would record its own hash, which is impossible (the file
    would change as a result of being written).
    """
    return CACHE_DIR_NAME in rel_path.parts


async def _compute_sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of ``path``.

    The hash is computed in 64 KiB chunks via ``asyncio.to_thread`` so
    a large file does not block the event loop.
    """

    def _read_and_hash() -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(65536)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    return await asyncio.to_thread(_read_and_hash)


def format_manifest(manifest: dict[str, str]) -> str:
    """Return the on-disk text form of a manifest.

    Each line is ``<hex-sha256>  <relative-path>``. Lines are sorted by
    relative path so the file is reproducible across hosts.
    """
    return (
        "\n".join(f"{hex_digest}  {rel_path}" for rel_path, hex_digest in sorted(manifest.items()))
        + "\n"
    )


def parse_manifest(text: str) -> dict[str, str]:
    """Parse the on-disk text form of a manifest.

    Tolerant of single- and double-space separators (``sha256sum``
    outputs either form depending on the host). Empty lines are ignored.
    """
    result: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # Split on the first run of whitespace -- works for both
        # ``<hex>  <path>`` (sha256sum default) and ``<hex> <path>``.
        parts = line.split(None, 1)
        if len(parts) != 2:
            _log.warning("malformed manifest line: %r", line)
            continue
        hex_digest, rel_path = parts
        result[rel_path] = hex_digest
    return result


class Verifier:
    """SHA-256 verifier. Backend Spec §7.1.4."""

    async def compute_local_manifest(self, run_path: Path) -> dict[str, str]:
        """Walk ``run_path`` and compute a SHA-256 per file.

        Writes the manifest to ``run_path/.exlab-wizard/checksums.sha256``
        as a side-effect (the §7.1.4 contract). Files inside the
        ``.exlab-wizard/`` cache subtree are excluded so the manifest does
        not record its own hash.
        """
        if not run_path.exists() or not run_path.is_dir():  # noqa: ASYNC240 -- one-shot stat
            msg = f"run_path does not exist or is not a directory: {run_path}"
            raise FileNotFoundError(msg)

        manifest: dict[str, str] = {}
        for file_path in _iter_files(run_path):
            rel = file_path.relative_to(run_path)
            if _is_inside_cache_dir(rel):
                continue
            manifest[str(rel.as_posix())] = await _compute_sha256(file_path)

        # Persist to .exlab-wizard/checksums.sha256.
        paths.cache_dir(run_path).mkdir(parents=True, exist_ok=True)
        checksums_path = run_path / CHECKSUMS_RELATIVE
        atomic_write_bytes(checksums_path, format_manifest(manifest).encode("utf-8"))
        return manifest

    async def verify_against_local(self, run_path: Path, manifest: dict[str, str]) -> VerifyResult:
        """Re-hash every entry in ``manifest`` against the local subtree.

        Returns a :class:`VerifyResult` with ``ok=True`` iff every entry
        in the manifest exists locally with the recorded hash.

        Files on disk that are NOT in the manifest are returned in
        ``extra`` for diagnostic logging but do not by themselves cause
        ``ok=False``; a partial transport that wrote a fresh file would
        be caught by a later compute_local_manifest pass.
        """
        if not run_path.exists() or not run_path.is_dir():  # noqa: ASYNC240 -- one-shot stat
            msg = f"run_path does not exist or is not a directory: {run_path}"
            raise FileNotFoundError(msg)

        mismatched: list[str] = []
        missing: list[str] = []
        extra: list[str] = []
        observed: dict[str, str] = {}

        for rel_path, expected_hash in manifest.items():
            target = run_path / rel_path
            if not target.exists():
                missing.append(rel_path)
                continue
            actual = await _compute_sha256(target)
            observed[rel_path] = actual
            if actual != expected_hash:
                mismatched.append(rel_path)

        # Populate `extra` for files on disk not present in the manifest.
        for file_path in _iter_files(run_path):
            rel = file_path.relative_to(run_path)
            if _is_inside_cache_dir(rel):
                continue
            rel_str = str(rel.as_posix())
            if rel_str not in manifest:
                extra.append(rel_str)

        ok = not mismatched and not missing
        return VerifyResult(
            ok=ok,
            mismatched=tuple(sorted(mismatched)),
            missing=tuple(sorted(missing)),
            extra=tuple(sorted(extra)),
            manifest=observed,
        )

    def verify_against_remote(
        self,
        local_manifest: dict[str, str],
        remote_manifest: dict[str, str],
    ) -> VerifyResult:
        """Compare a local manifest against a remote-derived manifest.

        Pure dict comparison with no I/O. Use after the transport reports
        success, with ``remote_manifest`` derived from a remote hash probe
        (e.g. ``rclone hashsum sha256`` or ``ssh ... sha256sum``).

        - ``mismatched``: keys present in both with differing hex digests.
        - ``missing``: keys present locally but absent remotely; this is
          the integrity-in-transit failure mode.
        - ``extra``: keys present remotely but not locally; informational
          only and does not flip ``ok``.
        - ``ok = not mismatched and not missing``. An empty
          ``remote_manifest`` therefore yields ``ok=False`` with every
          local key listed in ``missing``.
        """
        # TODO Sec 7.1.4: streaming-download fallback (verify.max_stream_bytes) deferred
        mismatched: list[str] = []
        missing: list[str] = []
        extra: list[str] = []

        for rel_path, expected_hash in local_manifest.items():
            remote_hash = remote_manifest.get(rel_path)
            if remote_hash is None:
                missing.append(rel_path)
                continue
            if remote_hash != expected_hash:
                mismatched.append(rel_path)

        for rel_path in remote_manifest:
            if rel_path not in local_manifest:
                extra.append(rel_path)

        ok = not mismatched and not missing
        return VerifyResult(
            ok=ok,
            mismatched=tuple(sorted(mismatched)),
            missing=tuple(sorted(missing)),
            extra=tuple(sorted(extra)),
            manifest=dict(local_manifest),
        )
