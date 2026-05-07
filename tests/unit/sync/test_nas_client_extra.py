"""Extra coverage tests for ``exlab_wizard.sync.nas_client``.

These hit the sub-branches the happy-path tests in ``test_nas_client.py``
don't reach: cleanup retain-cache vs full-delete, hash-mismatch retry +
second-failure terminal, equipment-not-configured worker path, the
default real-binary push factory, and the ``CLEANUP_ELIGIBLE`` interlock
miss.
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
    RsyncSshTransport,
)
from exlab_wizard.constants import (
    CACHE_DIR_NAME,
    CREATION_JSON_NAME,
    CREATION_JSON_VERSION,
)
from exlab_wizard.sync.nas_client import (
    NASSyncClient,
    _build_transport_driver,
)
from exlab_wizard.sync.queue import SyncJobState
from exlab_wizard.sync.transports import TransportErrorKind, TransportResult
from exlab_wizard.validator.engine import Validator


def _build_config(
    local_root: Path,
    *,
    transport: Any | None = None,
    retain_cache: bool = True,
    min_verify_passes: int = 1,
    min_age_hours: int = 0,
    cleanup_enabled: bool = True,
) -> Config:
    transport = transport or RcloneTransport(
        type="rclone",
        rclone_remote="lab-nas",
        rclone_remote_path="/srv",
        bandwidth=BandwidthConfig(),
    )
    return Config(
        paths=PathsConfig(templates_dir="/tpl", plugin_dir="/plg", local_root=str(local_root)),
        equipment=[
            EquipmentConfig(
                id="EQ1",
                label="Eq 1",
                local_root=str(local_root),
                nas_root="/nas",
                completeness_signal="sentinel_file",
                sentinel_filename="DONE",
                transport=transport,
            )
        ],
        nas_cleanup=NASCleanupConfig(
            enabled=cleanup_enabled,
            min_verify_passes=min_verify_passes,
            min_age_hours=min_age_hours,
            retain_cache=retain_cache,
        ),
    )


def _make_creation(local_path: Path) -> CreationJson:
    return CreationJson(
        schema_version=CREATION_JSON_VERSION,
        created_at="2026-04-17T14:32:00Z",
        created_by="x",
        level="run",
        run_kind="experimental",
        lims_project=LimsProjectBlock(uid="abc", short_id="PROJ-0042", name_at_creation="X"),
        template=TemplateBlock(name="t", version="1", source_path="x", run_scope="experimental"),
        variables={},
        paths=PathsBlock(local=str(local_path), nas="/srv/nas/run"),
    )


async def _populate_run(local_root: Path) -> Path:
    run_dir = local_root / "EQ1" / "PROJ-0042" / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    (run_dir / "data.bin").write_bytes(b"payload-bytes")
    (run_dir / "subdir").mkdir()
    (run_dir / "subdir" / "child.txt").write_text("child")
    cache = run_dir / CACHE_DIR_NAME
    cache.mkdir()
    (cache / CREATION_JSON_NAME).write_bytes(msgspec_json.encode(_make_creation(run_dir)))
    return run_dir


def _factory(
    push: Callable[..., Any],
) -> Callable[[EquipmentConfig], Callable[..., Any]]:
    return lambda _eq: push


# ---------------------------------------------------------------------------
# _build_transport_driver
# ---------------------------------------------------------------------------


def test_build_transport_driver_rclone(tmp_path: Path) -> None:
    eq = EquipmentConfig(
        id="EQ1",
        label="Eq 1",
        local_root=str(tmp_path),
        nas_root="/nas",
        completeness_signal="sentinel_file",
        sentinel_filename="DONE",
        transport=RcloneTransport(
            type="rclone",
            rclone_remote="lab-nas",
            rclone_remote_path="/srv",
            bandwidth=BandwidthConfig(),
        ),
    )
    driver, push = _build_transport_driver(eq)
    assert driver is not None
    assert callable(push)


def test_build_transport_driver_rsync_ssh(tmp_path: Path) -> None:
    eq = EquipmentConfig(
        id="EQ1",
        label="Eq 1",
        local_root=str(tmp_path),
        nas_root="/nas",
        completeness_signal="sentinel_file",
        sentinel_filename="DONE",
        transport=RsyncSshTransport(
            type="rsync_ssh",
            ssh_target="user@host",
            ssh_key_path="~/.ssh/id_ed25519",
            remote_path="/srv/nas",
            bandwidth=BandwidthConfig(),
        ),
    )
    driver, push = _build_transport_driver(eq)
    assert driver is not None
    assert callable(push)


# ---------------------------------------------------------------------------
# Hash-mismatch retry semantics
# ---------------------------------------------------------------------------


async def test_hash_mismatch_first_failure_retries(tmp_path: Path) -> None:
    """A first hash-mismatch from the transport re-queues without backoff.

    The first call returns hash_mismatch; the second call returns ok so
    we can observe the in-between state where ``last_error`` was set
    without the row terminating FAILED.
    """
    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)
    writer = CreationWriter(lock_timeout_seconds=10.0)
    call_count = {"n": 0}

    async def _push(_local: Path, *, bwlimit_kibps: int | None) -> TransportResult:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return TransportResult(
                ok=False, error_kind=TransportErrorKind.HASH_MISMATCH, returncode=1
            )
        return TransportResult(ok=True, returncode=0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_factory(_push),
        worker_poll_interval_s=0.005,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir)
        # Wait for the second pass to land on a non-error state.
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
            pytest.fail("expected eventual VERIFIED after retry")
        # Two transport calls: one mismatch, one success.
        assert call_count["n"] >= 2
    finally:
        await client.close()


async def test_hash_mismatch_second_failure_terminal(tmp_path: Path) -> None:
    """A second consecutive hash-mismatch terminates FAILED."""
    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)
    writer = CreationWriter(lock_timeout_seconds=10.0)

    async def _push(_local: Path, *, bwlimit_kibps: int | None) -> TransportResult:
        return TransportResult(ok=False, error_kind=TransportErrorKind.HASH_MISMATCH, returncode=1)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_factory(_push),
        worker_poll_interval_s=0.005,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir)
        for _ in range(400):
            row = await client._queue.get_by_id(handle.job_id)
            if row is not None and row.state is SyncJobState.FAILED:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("expected terminal FAILED on second hash mismatch")
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Cleanup branches
# ---------------------------------------------------------------------------


async def test_cleanup_full_delete_when_retain_cache_false(tmp_path: Path) -> None:
    """``retain_cache=False`` removes the entire run directory after CLEANED."""
    cfg = _build_config(tmp_path, retain_cache=False, min_verify_passes=1, min_age_hours=0)
    run_dir = await _populate_run(tmp_path)
    writer = CreationWriter(lock_timeout_seconds=10.0)

    async def _push(_local: Path, *, bwlimit_kibps: int | None) -> TransportResult:
        return TransportResult(ok=True, returncode=0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_factory(_push),
        worker_poll_interval_s=0.005,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir)
        for _ in range(400):
            row = await client._queue.get_by_id(handle.job_id)
            if row is not None and row.state is SyncJobState.CLEANED:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("expected CLEANED state")
        # Full directory removal: the run directory itself is gone.
        assert not run_dir.exists()
    finally:
        await client.close()


async def test_cleanup_retain_cache_keeps_metadata(tmp_path: Path) -> None:
    """``retain_cache=True`` deletes data files but keeps ``.exlab-wizard/``."""
    cfg = _build_config(tmp_path, retain_cache=True, min_verify_passes=1, min_age_hours=0)
    run_dir = await _populate_run(tmp_path)
    writer = CreationWriter(lock_timeout_seconds=10.0)

    async def _push(_local: Path, *, bwlimit_kibps: int | None) -> TransportResult:
        return TransportResult(ok=True, returncode=0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_factory(_push),
        worker_poll_interval_s=0.005,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir)
        for _ in range(400):
            row = await client._queue.get_by_id(handle.job_id)
            if row is not None and row.state is SyncJobState.CLEANED:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("expected CLEANED state")
        # Data files removed; cache subtree retained.
        assert (run_dir / CACHE_DIR_NAME).exists()
        assert not (run_dir / "data.bin").exists()
        assert not (run_dir / "subdir").exists()
    finally:
        await client.close()


async def test_cleanup_disabled_keeps_files(tmp_path: Path) -> None:
    """``nas_cleanup.enabled=False`` -> job stays VERIFIED, no cleanup."""
    cfg = _build_config(tmp_path, cleanup_enabled=False)
    run_dir = await _populate_run(tmp_path)
    writer = CreationWriter(lock_timeout_seconds=10.0)

    async def _push(_local: Path, *, bwlimit_kibps: int | None) -> TransportResult:
        return TransportResult(ok=True, returncode=0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_factory(_push),
        worker_poll_interval_s=0.005,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir)
        # Wait for VERIFIED.
        for _ in range(400):
            row = await client._queue.get_by_id(handle.job_id)
            if row is not None and row.state is SyncJobState.VERIFIED:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("expected VERIFIED")
        # The run dir is still intact.
        assert run_dir.exists()
        assert (run_dir / "data.bin").exists()
    finally:
        await client.close()


async def test_cleanup_eligible_when_min_verify_passes_unmet(tmp_path: Path) -> None:
    """Job lands in CLEANUP_ELIGIBLE when min_verify_passes > current passes."""
    cfg = _build_config(tmp_path, retain_cache=True, min_verify_passes=2, min_age_hours=0)
    run_dir = await _populate_run(tmp_path)
    writer = CreationWriter(lock_timeout_seconds=10.0)

    async def _push(_local: Path, *, bwlimit_kibps: int | None) -> TransportResult:
        return TransportResult(ok=True, returncode=0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_factory(_push),
        worker_poll_interval_s=0.005,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir)
        for _ in range(400):
            row = await client._queue.get_by_id(handle.job_id)
            if row is not None and row.state is SyncJobState.CLEANUP_ELIGIBLE:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("expected CLEANUP_ELIGIBLE")
        # Files retained because min_verify_passes wasn't met.
        assert (run_dir / "data.bin").exists()
    finally:
        await client.close()


async def test_cleanup_blocked_by_remote_stat(tmp_path: Path) -> None:
    """A failing remote_stat keeps the job in CLEANUP_ELIGIBLE, not CLEANED."""
    cfg = _build_config(tmp_path, retain_cache=True, min_verify_passes=1, min_age_hours=0)
    run_dir = await _populate_run(tmp_path)
    writer = CreationWriter(lock_timeout_seconds=10.0)

    async def _push(_local: Path, *, bwlimit_kibps: int | None) -> TransportResult:
        return TransportResult(ok=True, returncode=0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_factory(_push),
        remote_stat_callable=lambda _row: False,
        worker_poll_interval_s=0.005,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir)
        for _ in range(400):
            row = await client._queue.get_by_id(handle.job_id)
            if row is not None and row.state is SyncJobState.CLEANUP_ELIGIBLE:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("expected CLEANUP_ELIGIBLE on remote stat fail")
        # Files retained because remote_stat failed.
        assert (run_dir / "data.bin").exists()
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Worker error handling: equipment-not-configured / vanished local
# ---------------------------------------------------------------------------


async def test_worker_marks_failed_when_local_run_vanished(tmp_path: Path) -> None:
    """A run dir deleted between enqueue and worker pick -> FAILED."""
    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)
    writer = CreationWriter(lock_timeout_seconds=10.0)

    # Use a slow stub so we have time to delete the directory before the
    # worker picks the row.

    async def _slow(_local: Path, *, bwlimit_kibps: int | None) -> TransportResult:
        await asyncio.sleep(2.0)
        return TransportResult(ok=True)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_factory(_slow),
        worker_poll_interval_s=0.005,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir)
        # Delete the directory before the worker can pick it.
        import shutil

        shutil.rmtree(run_dir)
        for _ in range(400):
            row = await client._queue.get_by_id(handle.job_id)
            if row is not None and row.state is SyncJobState.FAILED:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("expected FAILED on vanished local")
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Network error -> backoff retry path
# ---------------------------------------------------------------------------


async def test_verifier_mismatch_first_failure_then_pass(tmp_path: Path) -> None:
    """The verifier-side hash mismatch retries once, then passes."""
    from exlab_wizard.sync.verifier import Verifier, VerifyResult

    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)
    writer = CreationWriter(lock_timeout_seconds=10.0)

    class _StubVerifier(Verifier):
        """First verify fails, second succeeds."""

        def __init__(self) -> None:
            self.calls = 0

        async def compute_local_manifest(self, run_path: Path) -> dict[str, str]:
            return await Verifier.compute_local_manifest(self, run_path)

        async def verify_against_local(
            self, run_path: Path, manifest: dict[str, str]
        ) -> VerifyResult:
            self.calls += 1
            if self.calls == 1:
                return VerifyResult(ok=False, mismatched=("data.bin",), manifest=manifest)
            return VerifyResult(ok=True, manifest=manifest)

    async def _push(_local: Path, *, bwlimit_kibps: int | None) -> TransportResult:
        return TransportResult(ok=True, returncode=0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        verifier=_StubVerifier(),
        push_callable_factory=_factory(_push),
        worker_poll_interval_s=0.005,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir)
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
            pytest.fail("expected eventual VERIFIED after verify retry")
    finally:
        await client.close()


async def test_verifier_mismatch_second_failure_terminal(tmp_path: Path) -> None:
    """Two consecutive verifier mismatches terminate FAILED."""
    from exlab_wizard.sync.verifier import Verifier, VerifyResult

    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)
    writer = CreationWriter(lock_timeout_seconds=10.0)

    class _AlwaysMismatch(Verifier):
        async def verify_against_local(
            self, run_path: Path, manifest: dict[str, str]
        ) -> VerifyResult:
            return VerifyResult(ok=False, mismatched=("x",), manifest=manifest)

    async def _push(_local: Path, *, bwlimit_kibps: int | None) -> TransportResult:
        return TransportResult(ok=True, returncode=0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        verifier=_AlwaysMismatch(),
        push_callable_factory=_factory(_push),
        worker_poll_interval_s=0.005,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir)
        for _ in range(400):
            row = await client._queue.get_by_id(handle.job_id)
            if row is not None and row.state is SyncJobState.FAILED:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("expected FAILED after two verifier mismatches")
    finally:
        await client.close()


async def test_default_push_factory_uses_real_driver(tmp_path: Path) -> None:
    """When no factory is injected, the client builds the per-equipment driver.

    We don't actually push (no rclone binary), but we check that the
    public ``_build_push`` method dispatches to ``_build_transport_driver``
    for a configured equipment.
    """
    cfg = _build_config(tmp_path)
    writer = CreationWriter(lock_timeout_seconds=10.0)
    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        # No push_callable_factory -- the default code path is used.
    )
    await client.init()
    try:
        push = client._build_push(cfg.equipment[0])
        assert callable(push)
    finally:
        await client.close()


async def test_network_error_records_backoff_retry(tmp_path: Path) -> None:
    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)
    writer = CreationWriter(lock_timeout_seconds=10.0)

    async def _push(_local: Path, *, bwlimit_kibps: int | None) -> TransportResult:
        return TransportResult(ok=False, error_kind=TransportErrorKind.NETWORK, returncode=1)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_factory(_push),
        worker_poll_interval_s=0.005,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir)
        for _ in range(200):
            row = await client._queue.get_by_id(handle.job_id)
            if row is not None and row.attempts >= 1:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("network error never increased attempts")
        assert row.last_error == "network"
        assert row.next_attempt_at  # backoff scheduled
    finally:
        await client.close()


def test_compute_nas_path_returns_none_when_empty() -> None:
    """Helper returns ``None`` when ``creation.paths.nas`` is the empty string."""
    creation = CreationJson(
        schema_version=CREATION_JSON_VERSION,
        created_at="2026-04-17T14:32:00Z",
        created_by="x",
        level="run",
        run_kind="experimental",
        lims_project=LimsProjectBlock(uid="x", short_id="PROJ-0042", name_at_creation="x"),
        template=TemplateBlock(name="x", version="1", source_path="x", run_scope="experimental"),
        variables={},
        paths=PathsBlock(local="/x", nas=""),
    )
    assert NASSyncClient._compute_nas_path(creation) is None


async def test_build_transport_driver_rejects_unknown_type(tmp_path: Path) -> None:
    """An unknown transport type raises ValueError from the helper."""

    class _BogusTransport:
        type = "bogus"
        bandwidth = BandwidthConfig()

    eq = EquipmentConfig.model_construct(
        id="EQ1",
        label="Eq",
        local_root=str(tmp_path),
        nas_root="/nas",
        completeness_signal="sentinel_file",
        sentinel_filename="DONE",
        transport=_BogusTransport(),  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="unsupported transport"):
        _build_transport_driver(eq)


async def test_mark_synced_no_op_when_creation_missing(tmp_path: Path) -> None:
    """``_mark_synced`` is a no-op when ``creation.json`` doesn't exist."""
    cfg = _build_config(tmp_path)
    writer = CreationWriter(lock_timeout_seconds=10.0)
    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
    )
    await client.init()
    try:
        # Should not raise even though there's no creation.json under tmp_path.
        await client._mark_synced(tmp_path / "non" / "existent")
    finally:
        await client.close()


def test_delete_local_no_op_when_path_missing(tmp_path: Path) -> None:
    """``_delete_local`` is a no-op when the run directory is already gone."""
    cfg = _build_config(tmp_path)
    writer = CreationWriter(lock_timeout_seconds=10.0)
    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
    )
    # Don't even need to call init() -- helper is purely synchronous.
    client._delete_local(tmp_path / "missing")  # must not raise


def test_infer_equipment_id_falls_back_to_first(tmp_path: Path) -> None:
    """If neither ``creation.paths.local`` nor ``run_path`` contains a
    configured equipment id, the helper falls back to the first id."""
    cfg = _build_config(tmp_path)
    writer = CreationWriter(lock_timeout_seconds=10.0)
    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
    )
    creation = CreationJson(
        schema_version=CREATION_JSON_VERSION,
        created_at="2026-04-17T14:32:00Z",
        created_by="x",
        level="run",
        run_kind="experimental",
        lims_project=LimsProjectBlock(uid="x", short_id="PROJ-0042", name_at_creation="x"),
        template=TemplateBlock(name="x", version="1", source_path="x", run_scope="experimental"),
        variables={},
        paths=PathsBlock(local="", nas=""),
    )
    inferred = client._infer_equipment_id(Path("/no/match/here"), creation)
    assert inferred == "EQ1"
