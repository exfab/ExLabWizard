"""Unit tests for ``exlab_wizard.sync.nas_client.NASSyncClient``.

Backend Spec §7.1, §7.3. These tests inject a stub push callable so the
client never spawns a real subprocess; the integration test in
``tests/integration/test_nas_sync.py`` exercises the full pipeline with
the Python stub binaries.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from exlab_wizard.api.schemas import (
    CreationJson,
    LimsProjectBlock,
    OverrideEntry,
    PathsBlock,
    TemplateBlock,
    msgspec_json,
    override_entry_to_dict,
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
from exlab_wizard.constants import CACHE_DIR_NAME, CREATION_JSON_NAME, CREATION_JSON_VERSION
from exlab_wizard.sync.nas_client import HandleState, NASSyncClient
from exlab_wizard.sync.queue import SyncJobState
from exlab_wizard.sync.transports import TransportErrorKind, TransportResult
from exlab_wizard.validator.engine import Validator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_config(local_root: Path, *, retain_cache: bool = True) -> Config:
    return Config(
        paths=PathsConfig(
            templates_dir="/tpl",
            plugin_dir="/plg",
            local_root=str(local_root),
        ),
        equipment=[
            EquipmentConfig(
                id="EQ1",
                label="Eq 1",
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
            min_verify_passes=2,
            min_age_hours=24,
            retain_cache=retain_cache,
        ),
    )


def _make_creation(local_path: Path, *, overrides: list[dict] | None = None) -> CreationJson:
    return CreationJson(
        schema_version=CREATION_JSON_VERSION,
        created_at="2026-04-17T14:32:00Z",
        created_by="asmith",
        level="run",
        run_kind="experimental",
        lims_project=LimsProjectBlock(uid="abc", short_id="PROJ-0042", name_at_creation="X"),
        template=TemplateBlock(
            name="confocal_run",
            version="1.0",
            source_path="x",
            run_scope="experimental",
        ),
        variables={},
        paths=PathsBlock(local=str(local_path), nas="/srv/nas/run"),
        validation_overrides=overrides or [],
    )


async def _populate_run(local_root: Path) -> Path:
    """Build a clean run directory with a creation.json under EQ1/PROJ-0042/."""
    run_dir = local_root / "EQ1" / "PROJ-0042" / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    (run_dir / "data.bin").write_bytes(b"payload")
    cache = run_dir / CACHE_DIR_NAME
    cache.mkdir()
    creation_path = cache / CREATION_JSON_NAME
    payload = _make_creation(run_dir)
    creation_path.write_bytes(msgspec_json.encode(payload))
    return run_dir


def _make_push_factory(
    *, ok: bool = True, error_kind: TransportErrorKind | None = None
) -> Callable[[EquipmentConfig], Callable[..., Any]]:
    """A push callable factory that yields deterministic outcomes for tests."""

    async def _push(local: Path, *, bwlimit_kibps: int | None) -> TransportResult:
        return TransportResult(ok=ok, error_kind=error_kind, returncode=0 if ok else 1)

    def factory(_eq: EquipmentConfig) -> Callable[..., Any]:
        return _push

    return factory


@pytest.fixture()
async def writer() -> CreationWriter:
    return CreationWriter(lock_timeout_seconds=10.0)


# ---------------------------------------------------------------------------
# enqueue: gate behavior
# ---------------------------------------------------------------------------


async def test_enqueue_blocks_run_with_hard_finding_no_override(
    tmp_path: Path, writer: CreationWriter
) -> None:
    """A run path containing ``<placeholder>`` is gated and not queued."""
    cfg = _build_config(tmp_path)
    bad_root = tmp_path / "EQ1" / "PROJ-0042" / "Run_<run_date>"
    bad_root.mkdir(parents=True)
    cache = bad_root / CACHE_DIR_NAME
    cache.mkdir()
    creation_path = cache / CREATION_JSON_NAME
    payload = _make_creation(bad_root)
    creation_path.write_bytes(msgspec_json.encode(payload))

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_make_push_factory(),
    )
    await client.init()
    try:
        handle = await client.enqueue(bad_root)
        assert handle.state == HandleState.BLOCKED
        # creation.json must reflect the block.
        decoded = msgspec_json.decode(creation_path.read_bytes(), type=CreationJson)
        assert decoded.sync_status == "blocked_by_validation"
    finally:
        await client.close()


async def test_enqueue_clean_run_creates_queued_row(tmp_path: Path, writer: CreationWriter) -> None:
    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_make_push_factory(),
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir)
        assert handle.state == HandleState.QUEUED
        assert handle.job_id
        # status reflects the row.
        status = await client.status(run_dir)
        assert status in {
            SyncJobState.QUEUED.value,
            SyncJobState.RUNNING.value,
            SyncJobState.AWAITING_VERIFY.value,
            SyncJobState.VERIFIED.value,
            SyncJobState.CLEANUP_ELIGIBLE.value,
            SyncJobState.CLEANED.value,
        }
    finally:
        await client.close()


async def test_enqueue_with_active_override_unblocks(
    tmp_path: Path, writer: CreationWriter
) -> None:
    """A run with placeholder findings + matching active overrides is queued."""
    cfg = _build_config(tmp_path)
    bad_root = tmp_path / "EQ1" / "PROJ-0042" / "Run_<run_date>"
    bad_root.mkdir(parents=True)
    cache = bad_root / CACHE_DIR_NAME
    cache.mkdir()
    creation_path = cache / CREATION_JSON_NAME
    overrides = [
        override_entry_to_dict(
            OverrideEntry(
                id="o1",
                problem_class="unresolved_placeholder_token",
                operator="x",
                recorded_at="2026-04-17T14:32:00Z",
                reason="legacy",
            )
        ),
        override_entry_to_dict(
            OverrideEntry(
                id="o2",
                problem_class="illegal_filesystem_character",
                operator="x",
                recorded_at="2026-04-17T14:32:00Z",
                reason="legacy",
            )
        ),
    ]
    payload = _make_creation(bad_root, overrides=overrides)
    creation_path.write_bytes(msgspec_json.encode(payload))

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_make_push_factory(),
    )
    await client.init()
    try:
        handle = await client.enqueue(bad_root)
        assert handle.state == HandleState.QUEUED
    finally:
        await client.close()


async def test_status_returns_none_when_no_job(tmp_path: Path, writer: CreationWriter) -> None:
    cfg = _build_config(tmp_path)
    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_make_push_factory(),
    )
    await client.init()
    try:
        assert await client.status(tmp_path / "missing") == "none"
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# retry / force_verify
# ---------------------------------------------------------------------------


async def test_retry_resets_failed_to_queued(tmp_path: Path, writer: CreationWriter) -> None:
    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        # Auth failure path -> terminal FAILED.
        push_callable_factory=_make_push_factory(ok=False, error_kind=TransportErrorKind.AUTH),
        worker_poll_interval_s=0.01,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir)
        # Wait until the worker has marked the job as FAILED.
        for _ in range(200):
            row = await client._queue.get_by_id(handle.job_id)
            if row is not None and row.state is SyncJobState.FAILED:
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("worker did not transition to FAILED in time")

        await client.retry(handle.job_id)
        row = await client._queue.get_by_id(handle.job_id)
        assert row is not None
        assert row.state is SyncJobState.QUEUED
        assert row.attempts == 0
        assert row.last_error is None
    finally:
        await client.close()


async def test_force_verify_returns_self_consistent(tmp_path: Path, writer: CreationWriter) -> None:
    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_make_push_factory(),
    )
    await client.init()
    try:
        result = await client.force_verify(run_dir)
        assert result.ok is True
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Worker pipeline (via a directly-constructed client)
# ---------------------------------------------------------------------------


async def test_worker_drives_to_verified_and_marks_synced(
    tmp_path: Path, writer: CreationWriter
) -> None:
    """A successful push -> AWAITING_VERIFY -> VERIFIED -> sync_status='synced'."""
    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_make_push_factory(ok=True),
        # Optimistic remote_stat default + low min_age_hours so cleanup
        # interlocks won't accidentally trigger for default config.
        worker_poll_interval_s=0.01,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir)
        for _ in range(300):
            row = await client._queue.get_by_id(handle.job_id)
            if row is not None and row.state in {
                SyncJobState.VERIFIED,
                SyncJobState.CLEANUP_ELIGIBLE,
                SyncJobState.CLEANED,
            }:
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("worker did not reach VERIFIED in time")
        creation_path = run_dir / CACHE_DIR_NAME / CREATION_JSON_NAME
        decoded = msgspec_json.decode(creation_path.read_bytes(), type=CreationJson)
        assert decoded.sync_status == "synced"
    finally:
        await client.close()


async def test_worker_terminal_failed_on_auth_error(tmp_path: Path, writer: CreationWriter) -> None:
    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_make_push_factory(ok=False, error_kind=TransportErrorKind.AUTH),
        worker_poll_interval_s=0.01,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir)
        for _ in range(200):
            row = await client._queue.get_by_id(handle.job_id)
            if row is not None and row.state is SyncJobState.FAILED:
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("worker did not reach FAILED on auth error")
    finally:
        await client.close()


async def test_enqueue_existing_failed_resets_to_queued(
    tmp_path: Path, writer: CreationWriter
) -> None:
    """Re-enqueueing a FAILED row resets it back to QUEUED."""
    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_make_push_factory(ok=False, error_kind=TransportErrorKind.AUTH),
        worker_poll_interval_s=0.01,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir)
        for _ in range(200):
            row = await client._queue.get_by_id(handle.job_id)
            if row is not None and row.state is SyncJobState.FAILED:
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("worker did not reach FAILED")
        # Now the same enqueue call should reset to QUEUED.
        handle2 = await client.enqueue(run_dir)
        assert handle2.state == HandleState.QUEUED
        assert handle2.job_id == handle.job_id
    finally:
        await client.close()


async def test_enqueue_idempotent_for_already_queued_row(
    tmp_path: Path, writer: CreationWriter
) -> None:
    """Enqueueing twice for the same path returns the same job id."""
    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)
    # Use a slow stub to keep the row from progressing past QUEUED.

    async def _slow(local: Path, *, bwlimit_kibps: int | None) -> TransportResult:
        await asyncio.sleep(0.5)
        return TransportResult(ok=True)

    def factory(_eq: EquipmentConfig) -> Callable[..., Any]:
        return _slow

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=factory,
        worker_poll_interval_s=0.01,
    )
    await client.init()
    try:
        handle1 = await client.enqueue(run_dir)
        handle2 = await client.enqueue(run_dir)
        assert handle1.job_id == handle2.job_id
    finally:
        await client.close()


async def test_close_is_idempotent(tmp_path: Path, writer: CreationWriter) -> None:
    cfg = _build_config(tmp_path)
    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
    )
    await client.init()
    await client.close()
    await client.close()
