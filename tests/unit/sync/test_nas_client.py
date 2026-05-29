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
    Config,
    EquipmentConfig,
    NASCleanupConfig,
    NasConfig,
    OrchestratorConfig,
    PathsConfig,
    RclonePerf,
)
from exlab_wizard.constants import (
    CACHE_DIR_NAME,
    CREATION_JSON_NAME,
    CREATION_JSON_VERSION,
    SyncMode,
)
from exlab_wizard.constants import (
    SyncHandleState as HandleState,
)
from exlab_wizard.sync.nas_client import NASSyncClient
from exlab_wizard.sync.queue import SyncJobState
from exlab_wizard.sync.transports import TransportErrorKind, TransportResult
from exlab_wizard.validator.engine import Validator
from tests.unit.sync._helpers import (
    local_lsjson_factory,
    missing_one_lsjson_factory,
)

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
    run_dir = local_root / "EQ1" / "PROJ-0042" / "Runs" / "Run_2026-04-17T14-32-00"
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

    async def _push(
        local: Path, *, bwlimit_kibps: int | None, files_from: object = None
    ) -> TransportResult:
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
    bad_root = tmp_path / "EQ1" / "PROJ-0042" / "Runs" / "Run_<run_date>"
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
    bad_root = tmp_path / "EQ1" / "PROJ-0042" / "Runs" / "Run_<run_date>"
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
        # Routine reconcile (rclone-named-remote migration): the post-push
        # verify path now lists the remote via lsjson and credits files
        # whose size + modtime match local. Inject a perfect listing so
        # the reconcile succeeds without a real rclone binary.
        lsjson_callable_factory=local_lsjson_factory(),
        # Optimistic remote_stat default + high min_age_hours/passes so
        # cleanup interlocks won't trigger for the default config.
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


async def test_routine_reconcile_marks_synced_from_lsjson(
    tmp_path: Path, writer: CreationWriter
) -> None:
    """The routine post-push path credits files via lsjson, NOT check --download.

    A run reconciled from a perfect lsjson listing reaches VERIFIED and
    credits every file in ``sync_state.json``; the injected hash-verify
    ``check`` callable (reserved for the cleanup integrity gate) must NOT
    be touched on this routine path.
    """
    from exlab_wizard.cache.sync_state_writer import SyncStateWriter

    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)
    sync_state = SyncStateWriter()

    check_calls = {"n": 0}

    async def _check_should_not_run(local: Path, *, files_from: Path):
        check_calls["n"] += 1
        from exlab_wizard.sync.transports.rclone import CheckResult

        return CheckResult()

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        sync_state_writer=sync_state,
        push_callable_factory=_make_push_factory(ok=True),
        lsjson_callable_factory=local_lsjson_factory(),
        check_callable_factory=lambda _eq: _check_should_not_run,
        worker_poll_interval_s=0.01,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir, ["data.bin"])
        for _ in range(400):
            row = await client._queue.get_by_id(handle.job_id)
            if row is not None and row.state in {
                SyncJobState.VERIFIED,
                SyncJobState.CLEANUP_ELIGIBLE,
                SyncJobState.CLEANED,
            }:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("worker did not reconcile to VERIFIED via lsjson")

        # The data file is credited in sync_state.json from the lsjson match.
        state = await sync_state.read(run_dir)
        assert "data.bin" in state.files
        assert state.files["data.bin"].synced_signature is not None
        assert state.files["data.bin"].verified_at is not None
        # The hash-verify check callable was never invoked on the routine
        # path (default cleanup interlocks defer the gate).
        assert check_calls["n"] == 0
    finally:
        await client.close()


async def test_routine_reconcile_requeues_when_remote_file_missing(
    tmp_path: Path, writer: CreationWriter
) -> None:
    """A file absent from the lsjson listing leaves the job re-queued.

    The present file is credited; the job re-queues with
    ``remote_reconcile_incomplete`` rather than promoting to VERIFIED.
    """
    from exlab_wizard.cache.sync_state_writer import SyncStateWriter

    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)
    # Add a second file so the subset has a laggard.
    (run_dir / "other.bin").write_bytes(b"more")
    sync_state = SyncStateWriter()

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        sync_state_writer=sync_state,
        push_callable_factory=_make_push_factory(ok=True),
        # "other.bin" never appears remotely -> reconcile incomplete.
        lsjson_callable_factory=missing_one_lsjson_factory("other.bin"),
        worker_poll_interval_s=0.01,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir, ["data.bin", "other.bin"])
        # The credited file lands in sync_state; the job never reaches VERIFIED.
        for _ in range(200):
            state = await sync_state.read(run_dir)
            if "data.bin" in state.files and state.files["data.bin"].verified_at:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("data.bin was never credited from the partial listing")

        # The job keeps cycling QUEUED -> RUNNING -> AWAITING_VERIFY ->
        # QUEUED because "other.bin" never reconciles; it must never reach a
        # terminal/VERIFIED state.
        await asyncio.sleep(0.05)
        row = await client._queue.get_by_id(handle.job_id)
        assert row is not None
        assert row.state not in {
            SyncJobState.VERIFIED,
            SyncJobState.CLEANUP_ELIGIBLE,
            SyncJobState.CLEANED,
            SyncJobState.FAILED,
        }
        state = await sync_state.read(run_dir)
        assert "other.bin" not in state.files
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

    async def _slow(
        local: Path, *, bwlimit_kibps: int | None, files_from: object = None
    ) -> TransportResult:
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


# ---------------------------------------------------------------------------
# _mark_cleaned: writes sync_status="cleaned" after local cleanup
# ---------------------------------------------------------------------------


async def test_mark_cleaned_writes_cleaned_to_creation_json(
    tmp_path: Path, writer: CreationWriter
) -> None:
    """``_mark_cleaned`` flips ``creation.json.sync_status`` to ``"cleaned"``."""
    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)
    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
    )
    await client.init()
    try:
        await client._mark_cleaned(run_dir)
        creation_path = run_dir / CACHE_DIR_NAME / CREATION_JSON_NAME
        decoded = msgspec_json.decode(creation_path.read_bytes(), type=CreationJson)
        assert decoded.sync_status == "cleaned"
    finally:
        await client.close()


async def test_mark_cleaned_is_noop_when_creation_json_missing(
    tmp_path: Path, writer: CreationWriter
) -> None:
    """With ``retain_cache=False`` the cache dir is gone; ``_mark_cleaned`` no-ops."""
    cfg = _build_config(tmp_path, retain_cache=False)
    run_dir = tmp_path / "EQ1" / "PROJ-0042" / "Runs" / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)  # no .exlab-wizard/creation.json

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
    )
    await client.init()
    try:
        # Should not raise even though creation.json doesn't exist.
        await client._mark_cleaned(run_dir)
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Phase 4: per-file enqueue + verify reconciliation
# ---------------------------------------------------------------------------


async def test_enqueue_with_files_inserts_subset(tmp_path: Path, writer: CreationWriter) -> None:
    """``enqueue(run, files=[...])`` stores the subset on the queue row."""
    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)

    async def _slow(local: Path, *, bwlimit_kibps: int | None, files_from: object = None):
        await asyncio.sleep(0.5)
        return TransportResult(ok=True)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=lambda _eq: _slow,
        worker_poll_interval_s=0.01,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir, ["data.bin"])
        assert handle.state == HandleState.QUEUED
        row = await client._queue.get_by_run_path(run_dir)
        assert row is not None
        assert row.files == ("data.bin",)
    finally:
        await client.close()


async def test_enqueue_requeues_terminal_job_with_new_files(
    tmp_path: Path, writer: CreationWriter
) -> None:
    """A terminal (FAILED) job is re-armed in QUEUED with a fresh file subset."""
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
        handle = await client.enqueue(run_dir, ["data.bin"])
        for _ in range(200):
            row = await client._queue.get_by_id(handle.job_id)
            if row is not None and row.state is SyncJobState.FAILED:
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("worker did not reach FAILED")
        # Re-enqueue with a new subset -> re-armed QUEUED carrying the new files.
        handle2 = await client.enqueue(run_dir, ["other.bin"])
        assert handle2.state == HandleState.QUEUED
        row2 = await client._queue.get_by_run_path(run_dir)
        assert row2 is not None
        assert row2.files == ("other.bin",)
    finally:
        await client.close()


async def test_enqueue_noops_active_job_with_new_files(
    tmp_path: Path, writer: CreationWriter
) -> None:
    """An active (QUEUED) job is left untouched when re-enqueued with new files."""
    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)

    async def _slow(local: Path, *, bwlimit_kibps: int | None, files_from: object = None):
        await asyncio.sleep(0.5)
        return TransportResult(ok=True)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=lambda _eq: _slow,
        worker_poll_interval_s=0.01,
    )
    await client.init()
    try:
        handle1 = await client.enqueue(run_dir, ["data.bin"])
        handle2 = await client.enqueue(run_dir, ["other.bin"])
        # Same job; the new files do NOT overwrite the active row's subset.
        assert handle1.job_id == handle2.job_id
        row = await client._queue.get_by_run_path(run_dir)
        assert row is not None
        assert row.files == ("data.bin",)
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# apply_config: live reload (no tray relaunch)
# ---------------------------------------------------------------------------


def test_apply_config_swaps_equipment_map(tmp_path: Path) -> None:
    """A live config swap rebuilds the equipment lookup in place."""
    client = NASSyncClient(
        config=_build_config(tmp_path),
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=CreationWriter(),
        push_callable_factory=_make_push_factory(),
    )
    assert set(client._equipment_by_id) == {"EQ1"}

    cfg2 = Config(
        paths=PathsConfig(local_root=str(tmp_path)),
        equipment=[
            EquipmentConfig(
                id="EQ2",
                label="Eq 2",
                local_root=str(tmp_path),
                nas_root="/nas",
            )
        ],
    )
    client.apply_config(cfg2)

    assert client._config is cfg2
    # EQ1 dropped, EQ2 resolvable -- exactly what a relaunch would have produced.
    assert set(client._equipment_by_id) == {"EQ2"}


# ---------------------------------------------------------------------------
# Target selection by sync_mode (rclone.conf NAS-sync migration, Phase 8)
# ---------------------------------------------------------------------------


def _client_for_target_test(config: Config, tmp_path: Path) -> NASSyncClient:
    return NASSyncClient(
        config=config,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=CreationWriter(),
    )


def test_target_for_stage_mode_uses_staging_remote(tmp_path: Path) -> None:
    """stage-mode equipment push to the orchestrator's staging remote."""
    stage_eq = EquipmentConfig(
        id="STAGE_01",
        label="Stage 1",
        local_root=str(tmp_path),
        nas_root="/nas",
        sync_mode=SyncMode.STAGE,
    )
    config = Config(
        paths=PathsConfig(local_root=str(tmp_path)),
        equipment=[stage_eq],
        nas=NasConfig(remote="nas01", base_root="/srv/nas"),
        orchestrator=OrchestratorConfig(
            label="LAB",
            staging_remote="stagepc",
            staging_base_root="/staging",
        ),
    )
    client = _client_for_target_test(config, tmp_path)
    run = tmp_path / "STAGE_01" / "PROJ-0001" / "Runs" / "Run_2026-05-29"

    target = client._target_for_equipment(stage_eq, run)

    assert target == "stagepc:/staging/STAGE_01/Run_2026-05-29"


def test_target_for_nas_mode_uses_nas_remote(tmp_path: Path) -> None:
    """nas-mode target still composes from the ``nas:`` block (unchanged)."""
    nas_eq = EquipmentConfig(
        id="EQ1",
        label="Eq 1",
        local_root=str(tmp_path),
        nas_root="/nas",
        sync_mode=SyncMode.NAS,
    )
    config = Config(
        paths=PathsConfig(local_root=str(tmp_path)),
        equipment=[nas_eq],
        nas=NasConfig(remote="nas01", base_root="/srv/nas"),
        orchestrator=OrchestratorConfig(
            label="LAB",
            staging_remote="stagepc",
            staging_base_root="/staging",
        ),
    )
    client = _client_for_target_test(config, tmp_path)
    run = tmp_path / "EQ1" / "PROJ-0001" / "Runs" / "Run_2026-05-29"

    target = client._target_for_equipment(nas_eq, run)

    assert target == "nas01:/srv/nas/EQ1/Run_2026-05-29"


def test_driver_for_stage_mode_uses_staging_perf(tmp_path: Path) -> None:
    """stage-mode driver picks up ``orchestrator.staging_perf`` while
    sharing ``nas.rclone_config_path`` as the ``--config`` override."""
    stage_eq = EquipmentConfig(
        id="STAGE_01",
        label="Stage 1",
        local_root=str(tmp_path),
        nas_root="/nas",
        sync_mode=SyncMode.STAGE,
    )
    config = Config(
        paths=PathsConfig(local_root=str(tmp_path)),
        equipment=[stage_eq],
        nas=NasConfig(
            remote="nas01",
            base_root="/srv/nas",
            rclone_config_path="/etc/rclone.conf",
            perf=RclonePerf(transfers=2, checkers=3),
        ),
        orchestrator=OrchestratorConfig(
            label="LAB",
            staging_remote="stagepc",
            staging_base_root="/staging",
            staging_perf=RclonePerf(transfers=7, checkers=9),
        ),
    )
    client = _client_for_target_test(config, tmp_path)

    stage_driver = client._driver_for_equipment(stage_eq)
    nas_driver = client._driver_for_equipment(
        EquipmentConfig(
            id="EQ1",
            label="Eq 1",
            local_root=str(tmp_path),
            nas_root="/nas",
            sync_mode=SyncMode.NAS,
        )
    )

    # stage-mode picks staging_perf; both share the nas rclone.conf path.
    assert (stage_driver._transfers, stage_driver._checkers) == (7, 9)
    assert (nas_driver._transfers, nas_driver._checkers) == (2, 3)
    assert stage_driver._config_path == nas_driver._config_path == "/etc/rclone.conf"
