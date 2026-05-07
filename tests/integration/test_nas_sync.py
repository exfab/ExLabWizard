"""End-to-end integration tests for the NAS sync subsystem (Phase 10).

These tests drive the full pipeline through the stub binaries on PATH so
we exercise the real subprocess + transport-driver path. The
``NASSyncClient`` in turn drives the queue / verifier / pre-sync gate
end-to-end, asserting:

- Pre-Sync Gate gates run paths with hard-tier findings.
- Successful happy-path runs flip ``creation.json`` ``sync_status`` to
  ``"synced"`` and move the queue row through ``QUEUED -> RUNNING ->
  AWAITING_VERIFY -> VERIFIED -> CLEANED`` once interlocks pass.
- Auth failures terminate at ``FAILED`` with no retries.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from exlab_wizard.api.schemas import (
    CreationJson,
    LimsProjectBlock,
    PathsBlock,
    TemplateBlock,
    msgspec_json,
)
from exlab_wizard.cache.creation_writer import CreationWriter
from exlab_wizard.config.models import (
    BandwidthConfig,
    Config,
    EquipmentConfig,
    NASCleanupConfig,
    PathsConfig,
    RcloneTransport,
)
from exlab_wizard.constants import (
    CACHE_DIR_NAME,
    CREATION_JSON_NAME,
    CREATION_JSON_VERSION,
)
from exlab_wizard.sync.nas_client import HandleState, NASSyncClient
from exlab_wizard.sync.queue import SyncJobRow, SyncJobState
from exlab_wizard.validator.engine import Validator


@pytest.fixture()
def stub_binaries_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Install the rclone + rsync stubs onto PATH for the test."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fixtures = Path(__file__).parent.parent / "fixtures"
    for src_name, dst_name in (
        ("stub_rclone.py", "rclone"),
        ("stub_rsync.py", "rsync"),
    ):
        target = bin_dir / dst_name
        shutil.copy(fixtures / src_name, target)
        st = target.stat()
        target.chmod(st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return bin_dir


def _build_config(local_root: Path) -> Config:
    return Config(
        paths=PathsConfig(templates_dir="/tpl", plugin_dir="/plg", local_root=str(local_root)),
        equipment=[
            EquipmentConfig(
                id="EQ1",
                label="Equipment 1",
                local_root=str(local_root),
                nas_root="/nas",
                completeness_signal="sentinel_file",
                sentinel_filename="DONE",
                transport=RcloneTransport(
                    type="rclone",
                    rclone_remote="lab-nas",
                    rclone_remote_path="/srv/nas",
                    bandwidth=BandwidthConfig(),
                ),
            )
        ],
        nas_cleanup=NASCleanupConfig(
            enabled=True,
            min_verify_passes=1,  # one pass is enough so cleanup runs in test
            min_age_hours=0,
            retain_cache=True,
        ),
    )


def _make_creation(local_path: Path) -> CreationJson:
    return CreationJson(
        schema_version=CREATION_JSON_VERSION,
        created_at="2026-04-17T14:32:00Z",
        created_by="asmith",
        level="run",
        run_kind="experimental",
        lims_project=LimsProjectBlock(uid="abc", short_id="PROJ-0042", name_at_creation="Test"),
        template=TemplateBlock(
            name="confocal_run",
            version="1.0",
            source_path="x",
            run_scope="experimental",
        ),
        variables={},
        paths=PathsBlock(local=str(local_path), nas="/srv/nas/run"),
    )


async def _populate_run(local_root: Path) -> Path:
    run_dir = local_root / "EQ1" / "PROJ-0042" / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    (run_dir / "data.bin").write_bytes(b"payload-bytes")
    cache = run_dir / CACHE_DIR_NAME
    cache.mkdir()
    creation_path = cache / CREATION_JSON_NAME
    creation_path.write_bytes(msgspec_json.encode(_make_creation(run_dir)))
    return run_dir


async def _wait_for_state(
    queue_get: Callable[[str], asyncio.Future[SyncJobRow | None]],
    job_id: str,
    targets: set[SyncJobState],
    *,
    timeout_s: float = 8.0,
    poll_s: float = 0.05,
) -> SyncJobRow:
    """Poll ``queue.get_by_id`` until ``state`` is in ``targets`` or timeout."""
    elapsed = 0.0
    while elapsed < timeout_s:
        row = await queue_get(job_id)
        if row is not None and row.state in targets:
            return row
        await asyncio.sleep(poll_s)
        elapsed += poll_s
    msg = f"job {job_id} never reached {targets!r}"
    raise AssertionError(msg)


async def test_full_happy_path_via_stub_rclone(
    stub_binaries_on_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enqueue -> RUNNING -> AWAITING_VERIFY -> VERIFIED -> CLEANED.

    Uses the Python stub_rclone binary on PATH; the stub copies the
    source tree into ``STUB_RCLONE_DEST_ROOT`` so the verifier sees real
    contents and the local-only verify pass succeeds.
    """
    local_root = tmp_path / "local"
    local_root.mkdir()
    nas_root = tmp_path / "nas"
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "success")
    monkeypatch.setenv("STUB_RCLONE_DEST_ROOT", str(nas_root))

    cfg = _build_config(local_root)
    run_dir = await _populate_run(local_root)
    writer = CreationWriter(lock_timeout_seconds=10.0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        worker_poll_interval_s=0.01,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir)
        assert handle.state == HandleState.QUEUED

        # The worker should drive through to VERIFIED + CLEANED (since
        # the test config sets min_verify_passes=1 and min_age_hours=0).
        row = await _wait_for_state(
            client._queue.get_by_id,
            handle.job_id,
            {SyncJobState.CLEANED, SyncJobState.CLEANUP_ELIGIBLE, SyncJobState.VERIFIED},
        )
        assert row.state in {
            SyncJobState.VERIFIED,
            SyncJobState.CLEANUP_ELIGIBLE,
            SyncJobState.CLEANED,
        }

        # ``creation.json`` reflects the synced status.
        creation_path = run_dir / CACHE_DIR_NAME / CREATION_JSON_NAME
        decoded = msgspec_json.decode(creation_path.read_bytes(), type=CreationJson)
        assert decoded.sync_status == "synced"
    finally:
        await client.close()


async def test_pre_sync_gate_blocks_run_with_placeholder_in_path(
    stub_binaries_on_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run path with ``<run_date>`` is gated; sync_status -> blocked_by_validation."""
    local_root = tmp_path / "local"
    local_root.mkdir()
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "success")

    cfg = _build_config(local_root)

    bad_dir = local_root / "EQ1" / "PROJ-0042" / "Run_<run_date>"
    bad_dir.mkdir(parents=True)
    (bad_dir / "data.bin").write_bytes(b"x")
    cache = bad_dir / CACHE_DIR_NAME
    cache.mkdir()
    creation_path = cache / CREATION_JSON_NAME
    creation_path.write_bytes(msgspec_json.encode(_make_creation(bad_dir)))

    writer = CreationWriter(lock_timeout_seconds=10.0)
    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        worker_poll_interval_s=0.01,
    )
    await client.init()
    try:
        handle = await client.enqueue(bad_dir)
        assert handle.state == HandleState.BLOCKED
        decoded = msgspec_json.decode(creation_path.read_bytes(), type=CreationJson)
        assert decoded.sync_status == "blocked_by_validation"
    finally:
        await client.close()


async def test_auth_error_terminates_failed(
    stub_binaries_on_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stub returns ``auth_error`` -> queue row terminates FAILED."""
    local_root = tmp_path / "local"
    local_root.mkdir()
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "auth_error")

    cfg = _build_config(local_root)
    run_dir = await _populate_run(local_root)
    writer = CreationWriter(lock_timeout_seconds=10.0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        worker_poll_interval_s=0.01,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir)
        row = await _wait_for_state(client._queue.get_by_id, handle.job_id, {SyncJobState.FAILED})
        assert row.state is SyncJobState.FAILED
    finally:
        await client.close()


async def test_force_verify_returns_ok_after_compute(
    stub_binaries_on_path: Path, tmp_path: Path
) -> None:
    """``force_verify`` runs a manifest pass against the local subtree."""
    local_root = tmp_path / "local"
    local_root.mkdir()
    cfg = _build_config(local_root)
    run_dir = await _populate_run(local_root)

    writer = CreationWriter(lock_timeout_seconds=10.0)
    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
    )
    await client.init()
    try:
        result = await client.force_verify(run_dir)
        assert result.ok is True
        # The manifest file landed in the cache subtree.
        assert (run_dir / CACHE_DIR_NAME / "checksums.sha256").exists()
    finally:
        await client.close()
