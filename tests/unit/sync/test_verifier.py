"""Tests for ``exlab_wizard.sync.verifier``.

Covers SHA-256 manifest computation and verification. Backend Spec
§7.1.4. The verifier walks the run subtree, computes per-file hashes,
writes ``.exlab-wizard/checksums.sha256``, and compares against itself
on a re-verify pass.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from exlab_wizard.constants import CACHE_DIR_NAME, CHECKSUMS_RELATIVE
from exlab_wizard.sync.verifier import Verifier, format_manifest, parse_manifest


def _populate_run(run_path: Path) -> dict[str, bytes]:
    """Helper: write a small subtree under ``run_path`` and return the
    expected ``{rel_path -> bytes}`` map."""
    contents = {
        "data/a.txt": b"alpha\n",
        "data/b.txt": b"beta\n",
        "metadata.json": b'{"k":1}',
    }
    for rel, data in contents.items():
        target = run_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return contents


async def test_compute_local_manifest_hashes_files(tmp_path: Path) -> None:
    """Manifest contains every regular file with its SHA-256 hex."""
    run_path = tmp_path / "run"
    run_path.mkdir()
    contents = _populate_run(run_path)

    verifier = Verifier()
    manifest = await verifier.compute_local_manifest(run_path)

    assert set(manifest) == set(contents)
    for rel, data in contents.items():
        assert manifest[rel] == hashlib.sha256(data).hexdigest()


async def test_compute_local_manifest_writes_checksums_file(tmp_path: Path) -> None:
    """Manifest is written to ``.exlab-wizard/checksums.sha256`` on disk."""
    run_path = tmp_path / "run"
    run_path.mkdir()
    _populate_run(run_path)

    verifier = Verifier()
    manifest = await verifier.compute_local_manifest(run_path)

    out = run_path / CHECKSUMS_RELATIVE
    assert out.exists()
    parsed = parse_manifest(out.read_text(encoding="utf-8"))
    assert parsed == manifest


async def test_compute_excludes_cache_dir(tmp_path: Path) -> None:
    """Files under ``.exlab-wizard/`` are excluded from the manifest."""
    run_path = tmp_path / "run"
    run_path.mkdir()
    (run_path / "data.bin").write_bytes(b"data")
    (run_path / CACHE_DIR_NAME).mkdir()
    (run_path / CACHE_DIR_NAME / "stale.json").write_bytes(b"{}")

    verifier = Verifier()
    manifest = await verifier.compute_local_manifest(run_path)
    assert set(manifest) == {"data.bin"}


async def test_verify_against_local_matches(tmp_path: Path) -> None:
    """Verify-against-local of a freshly-computed manifest reports OK."""
    run_path = tmp_path / "run"
    run_path.mkdir()
    _populate_run(run_path)

    verifier = Verifier()
    manifest = await verifier.compute_local_manifest(run_path)
    result = await verifier.verify_against_local(run_path, manifest)
    assert result.ok is True
    assert result.mismatched == ()
    assert result.missing == ()


async def test_verify_detects_mismatch(tmp_path: Path) -> None:
    """A bit-flipped file is reported as mismatched."""
    run_path = tmp_path / "run"
    run_path.mkdir()
    _populate_run(run_path)

    verifier = Verifier()
    manifest = await verifier.compute_local_manifest(run_path)
    # Mutate one of the files in place.
    (run_path / "data" / "a.txt").write_bytes(b"alpha-mutated\n")

    result = await verifier.verify_against_local(run_path, manifest)
    assert result.ok is False
    assert "data/a.txt" in result.mismatched


async def test_verify_detects_missing(tmp_path: Path) -> None:
    """A deleted file is reported as missing."""
    run_path = tmp_path / "run"
    run_path.mkdir()
    _populate_run(run_path)

    verifier = Verifier()
    manifest = await verifier.compute_local_manifest(run_path)
    (run_path / "data" / "a.txt").unlink()

    result = await verifier.verify_against_local(run_path, manifest)
    assert result.ok is False
    assert "data/a.txt" in result.missing


async def test_verify_reports_extras(tmp_path: Path) -> None:
    """A new file not in the manifest is listed as extra (informational)."""
    run_path = tmp_path / "run"
    run_path.mkdir()
    _populate_run(run_path)

    verifier = Verifier()
    manifest = await verifier.compute_local_manifest(run_path)
    (run_path / "appeared.txt").write_bytes(b"x")

    result = await verifier.verify_against_local(run_path, manifest)
    # Extras alone don't fail the verify; the manifest was still satisfied.
    assert result.ok is True
    assert "appeared.txt" in result.extra


async def test_compute_missing_run_path_raises(tmp_path: Path) -> None:
    verifier = Verifier()
    with pytest.raises(FileNotFoundError):
        await verifier.compute_local_manifest(tmp_path / "absent")


async def test_verify_missing_run_path_raises(tmp_path: Path) -> None:
    verifier = Verifier()
    with pytest.raises(FileNotFoundError):
        await verifier.verify_against_local(tmp_path / "absent", {})


def test_format_and_parse_manifest_round_trip() -> None:
    manifest = {"a.txt": "deadbeef", "subdir/b": "cafebabe"}
    text = format_manifest(manifest)
    parsed = parse_manifest(text)
    assert parsed == manifest


def test_parse_manifest_tolerates_blank_and_malformed_lines() -> None:
    text = "abc123  hello\n\nbad-line\nffff  ok\n"
    parsed = parse_manifest(text)
    assert parsed == {"hello": "abc123", "ok": "ffff"}
