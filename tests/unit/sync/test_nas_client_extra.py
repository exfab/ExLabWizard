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
    RcloneSftpTransport,
    RcloneSmbTransport,
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
from tests.unit.sync._helpers import corrupt_one_check_factory, local_check_factory


def _build_config(
    local_root: Path,
    *,
    transport: Any | None = None,
    retain_cache: bool = True,
    min_verify_passes: int = 1,
    min_age_hours: int = 0,
    cleanup_enabled: bool = True,
) -> Config:
    transport = transport or RcloneSftpTransport(
        type="rclone_sftp",
        host="nas.lab.example",
        user="testuser",
        remote_path="/srv",
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
    run_dir = local_root / "EQ1" / "PROJ-0042" / "Runs" / "Run_2026-04-17T14-32-00"
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


class _StubKeyring:
    """Minimal keyring stub returning a fixed password by username."""

    def __init__(self, password: str = "topsecret") -> None:
        self._password = password

    def get_password(self, *, username: str) -> str:
        del username
        return self._password


def test_build_transport_driver_sftp(tmp_path: Path) -> None:
    eq = EquipmentConfig(
        id="EQ1",
        label="Eq 1",
        local_root=str(tmp_path),
        nas_root="/nas",
        transport=RcloneSftpTransport(
            type="rclone_sftp",
            host="nas.lab.example",
            user="testuser",
            remote_path="/srv",
            bandwidth=BandwidthConfig(),
        ),
    )
    driver, push, _check = _build_transport_driver(eq, _StubKeyring())
    assert driver is not None
    assert callable(push)


def test_build_transport_driver_smb(tmp_path: Path) -> None:
    eq = EquipmentConfig(
        id="EQ1",
        label="Eq 1",
        local_root=str(tmp_path),
        nas_root="/nas",
        transport=RcloneSmbTransport(
            type="rclone_smb",
            host="nas.lab.example",
            share="lab",
            user="testuser",
            domain="LAB",
            remote_path="EQ1",
            bandwidth=BandwidthConfig(),
        ),
    )
    driver, push, _check = _build_transport_driver(eq, _StubKeyring())
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

    async def _push(
        _local: Path, *, bwlimit_kibps: int | None, files_from: object = None
    ) -> TransportResult:
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
        # Inject a synthetic remote-hash probe so the §7.1.4 step-2 walk
        # finds matching digests. Removing this would surface as a
        # spurious HASH_MISMATCH (the no-op stub push doesn't actually
        # transfer files, so a real hashsum probe would see nothing on
        # the "remote" side). See ``local_hashsum_factory`` docstring.
        check_callable_factory=local_check_factory(),
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

    async def _push(
        _local: Path, *, bwlimit_kibps: int | None, files_from: object = None
    ) -> TransportResult:
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

    async def _push(
        _local: Path, *, bwlimit_kibps: int | None, files_from: object = None
    ) -> TransportResult:
        return TransportResult(ok=True, returncode=0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_factory(_push),
        check_callable_factory=local_check_factory(),
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

    async def _push(
        _local: Path, *, bwlimit_kibps: int | None, files_from: object = None
    ) -> TransportResult:
        return TransportResult(ok=True, returncode=0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_factory(_push),
        check_callable_factory=local_check_factory(),
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

    async def _push(
        _local: Path, *, bwlimit_kibps: int | None, files_from: object = None
    ) -> TransportResult:
        return TransportResult(ok=True, returncode=0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_factory(_push),
        check_callable_factory=local_check_factory(),
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

    async def _push(
        _local: Path, *, bwlimit_kibps: int | None, files_from: object = None
    ) -> TransportResult:
        return TransportResult(ok=True, returncode=0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_factory(_push),
        check_callable_factory=local_check_factory(),
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

    async def _push(
        _local: Path, *, bwlimit_kibps: int | None, files_from: object = None
    ) -> TransportResult:
        return TransportResult(ok=True, returncode=0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_factory(_push),
        check_callable_factory=local_check_factory(),
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
# Phase 5: _delete_local honors keep_local; cleanup gates on the SYNCED rollup
# ---------------------------------------------------------------------------


def _client(cfg: Config, tmp_path: Path) -> NASSyncClient:
    """Build an un-init'd client for direct ``_delete_local`` calls."""
    return NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=CreationWriter(lock_timeout_seconds=10.0),
    )


def test_delete_local_skips_keep_local_files_incl_nested(tmp_path: Path) -> None:
    """``_delete_local`` keeps ``keep_local`` files, incl. a nested one."""
    run_dir = tmp_path / "EQ1" / "PROJ-0042" / "Runs" / "Run_x"
    run_dir.mkdir(parents=True)
    (run_dir / "keep.bin").write_bytes(b"keep")
    (run_dir / "drop.bin").write_bytes(b"drop")
    nested = run_dir / "sub" / "deep"
    nested.mkdir(parents=True)
    (nested / "kept_nested.txt").write_text("nested-keep")
    (run_dir / "sub" / "other.txt").write_text("nested-drop")
    cache = run_dir / CACHE_DIR_NAME
    cache.mkdir()
    (cache / "meta.json").write_text("{}")

    cfg = _build_config(tmp_path, retain_cache=True)
    client = _client(cfg, tmp_path)
    client._delete_local(run_dir, {"keep.bin", "sub/deep/kept_nested.txt"})

    # Retained files survive; the run dir + cache are intact.
    assert (run_dir / "keep.bin").exists()
    assert (nested / "kept_nested.txt").exists()
    assert (cache / "meta.json").exists()
    # Non-retained files are gone.
    assert not (run_dir / "drop.bin").exists()
    assert not (run_dir / "sub" / "other.txt").exists()
    # The directory holding the kept nested file survives.
    assert (run_dir / "sub" / "deep").exists()


def test_delete_local_prunes_emptied_dirs_keeps_dir_with_kept_file(tmp_path: Path) -> None:
    """A directory holding only deleted files is pruned (deepest-first);
    a parent still holding a kept file survives."""
    run_dir = tmp_path / "EQ1" / "PROJ-0042" / "Runs" / "Run_p"
    run_dir.mkdir(parents=True)
    # ``branch/`` keeps a file directly + has a fully-emptied descendant.
    branch = run_dir / "branch"
    (branch / "leaf").mkdir(parents=True)
    (branch / "kept.txt").write_text("keep")
    (branch / "leaf" / "drop_a.txt").write_text("a")
    (branch / "leaf" / "drop_b.txt").write_text("b")
    # ``gone/`` and its nested ``gone/inner/`` hold only droppable files ->
    # the whole ``gone`` subtree must be pruned away (deepest-first).
    inner = run_dir / "gone" / "inner"
    inner.mkdir(parents=True)
    (run_dir / "gone" / "drop_c.txt").write_text("c")
    (inner / "drop_d.txt").write_text("d")

    cfg = _build_config(tmp_path, retain_cache=True)
    client = _client(cfg, tmp_path)
    client._delete_local(run_dir, {"branch/kept.txt"})

    # The kept file and its directory survive.
    assert (branch / "kept.txt").exists()
    assert branch.is_dir()
    # The fully-emptied descendant directory is pruned.
    assert not (branch / "leaf").exists()
    # The entire ``gone`` subtree (parent + nested) is pruned deepest-first.
    assert not (run_dir / "gone").exists()
    # The run directory itself is never removed.
    assert run_dir.is_dir()


def test_delete_local_does_not_descend_or_remove_directory_symlink(tmp_path: Path) -> None:
    """A directory symlink inside the run is left untouched -- its target's
    contents are not deleted and the link itself is not removed."""
    # An external directory the run will symlink to.
    external = tmp_path / "external"
    external.mkdir()
    (external / "precious.txt").write_text("do-not-delete")

    run_dir = tmp_path / "EQ1" / "PROJ-0042" / "Runs" / "Run_s"
    run_dir.mkdir(parents=True)
    (run_dir / "drop.bin").write_bytes(b"drop")
    link = run_dir / "linked"
    link.symlink_to(external, target_is_directory=True)

    cfg = _build_config(tmp_path, retain_cache=True)
    client = _client(cfg, tmp_path)
    client._delete_local(run_dir, set())

    # The run's own file is gone.
    assert not (run_dir / "drop.bin").exists()
    # The symlink itself and the external target's contents are untouched.
    assert link.is_symlink()
    assert external.is_dir()
    assert (external / "precious.txt").read_text() == "do-not-delete"


def test_delete_local_keep_local_survives_retain_cache_false(tmp_path: Path) -> None:
    """A ``keep_local`` file survives even when ``retain_cache=False``."""
    run_dir = tmp_path / "EQ1" / "PROJ-0042" / "Runs" / "Run_y"
    run_dir.mkdir(parents=True)
    (run_dir / "keep.bin").write_bytes(b"keep")
    (run_dir / "drop.bin").write_bytes(b"drop")

    cfg = _build_config(tmp_path, retain_cache=False)
    client = _client(cfg, tmp_path)
    client._delete_local(run_dir, {"keep.bin"})

    # The whole-run rmtree is skipped because a keep_local file exists.
    assert run_dir.exists()
    assert (run_dir / "keep.bin").exists()
    assert not (run_dir / "drop.bin").exists()


def test_delete_local_retain_cache_false_drops_whole_run_without_keep_local(
    tmp_path: Path,
) -> None:
    """With ``retain_cache=False`` and no kept files the run dir is removed."""
    run_dir = tmp_path / "EQ1" / "PROJ-0042" / "Runs" / "Run_z"
    run_dir.mkdir(parents=True)
    (run_dir / "drop.bin").write_bytes(b"drop")

    cfg = _build_config(tmp_path, retain_cache=False)
    client = _client(cfg, tmp_path)
    client._delete_local(run_dir, set())
    assert not run_dir.exists()


async def test_cleanup_marks_cleared_in_sync_state(tmp_path: Path) -> None:
    """A full cleanup pass stamps ``cleared_at`` in ``sync_state.json``."""
    from exlab_wizard.cache.sync_state_writer import SyncStateWriter

    cfg = _build_config(tmp_path, retain_cache=True, min_verify_passes=1, min_age_hours=0)
    run_dir = await _populate_run(tmp_path)
    writer = CreationWriter(lock_timeout_seconds=10.0)

    async def _push(
        _local: Path, *, bwlimit_kibps: int | None, files_from: object = None
    ) -> TransportResult:
        return TransportResult(ok=True, returncode=0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_factory(_push),
        check_callable_factory=local_check_factory(),
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
    finally:
        await client.close()

    state = await SyncStateWriter().read(run_dir)
    assert state.cleared_at is not None
    assert SyncStateWriter.rollup_state(state).value == "cleared"


async def test_cleanup_keeps_keep_local_file(tmp_path: Path) -> None:
    """A file flagged ``keep_local`` survives the cleanup sweep."""
    from exlab_wizard.cache.sync_state_writer import SyncStateWriter

    cfg = _build_config(tmp_path, retain_cache=True, min_verify_passes=1, min_age_hours=0)
    run_dir = await _populate_run(tmp_path)
    writer = CreationWriter(lock_timeout_seconds=10.0)
    sync_writer = SyncStateWriter()
    # Operator flags the top-level data file as keep-local before cleanup.
    await sync_writer.set_keep_local(run_dir, "data.bin", True)

    async def _push(
        _local: Path, *, bwlimit_kibps: int | None, files_from: object = None
    ) -> TransportResult:
        return TransportResult(ok=True, returncode=0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        sync_state_writer=sync_writer,
        push_callable_factory=_factory(_push),
        check_callable_factory=local_check_factory(),
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
        # The keep_local file survives; the other data file is removed.
        assert (run_dir / "data.bin").exists()
        assert not (run_dir / "subdir" / "child.txt").exists()
    finally:
        await client.close()


async def test_cleanup_deferred_when_run_only_partially_synced(tmp_path: Path) -> None:
    """Cleanup does not run while a tracked file remains unverified.

    A pre-existing ``sync_state.json`` records an extra file that never
    verifies, so the whole-run rollup stays ``SYNCING`` even after this
    job's subset verifies -- the job promotes to VERIFIED but cleanup is
    deferred and the local files survive.
    """
    from exlab_wizard.cache.sync_state_writer import SyncStateWriter

    cfg = _build_config(tmp_path, retain_cache=True, min_verify_passes=1, min_age_hours=0)
    run_dir = await _populate_run(tmp_path)
    writer = CreationWriter(lock_timeout_seconds=10.0)
    sync_writer = SyncStateWriter()
    # A later-sweep file that has never verified -> run is not fully SYNCED.
    await sync_writer.upsert_file(run_dir, "pending.bin", synced_signature=None, verified_at=None)

    async def _push(
        _local: Path, *, bwlimit_kibps: int | None, files_from: object = None
    ) -> TransportResult:
        return TransportResult(ok=True, returncode=0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        sync_state_writer=sync_writer,
        push_callable_factory=_factory(_push),
        check_callable_factory=local_check_factory(),
        worker_poll_interval_s=0.005,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir, files=["data.bin"])
        for _ in range(400):
            row = await client._queue.get_by_id(handle.job_id)
            if row is not None and row.state is SyncJobState.VERIFIED:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("expected VERIFIED")
        # Give the worker a beat -- cleanup must NOT advance the job.
        await asyncio.sleep(0.1)
        row = await client._queue.get_by_id(handle.job_id)
        assert row is not None and row.state is SyncJobState.VERIFIED
        # Local data is retained because the run is not fully SYNCED.
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

    async def _slow(
        _local: Path, *, bwlimit_kibps: int | None, files_from: object = None
    ) -> TransportResult:
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
    """A rclone-check mismatch retries once, then passes."""
    from exlab_wizard.sync.transports.rclone import CheckResult

    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)
    writer = CreationWriter(lock_timeout_seconds=10.0)

    call_count = {"n": 0}

    async def _check(local: Path, *, files_from: Path) -> CheckResult:
        del local
        text = files_from.read_text(encoding="utf-8")
        files = tuple(line.strip() for line in text.splitlines() if line.strip())
        call_count["n"] += 1
        if call_count["n"] == 1:
            return CheckResult(differ=("data.bin",), equal=())
        return CheckResult(equal=files)

    async def _push(
        _local: Path, *, bwlimit_kibps: int | None, files_from: object = None
    ) -> TransportResult:
        return TransportResult(ok=True, returncode=0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_factory(_push),
        check_callable_factory=lambda _eq: _check,
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
    from exlab_wizard.sync.transports.rclone import CheckResult

    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)
    writer = CreationWriter(lock_timeout_seconds=10.0)

    async def _always_differ(local: Path, *, files_from: Path) -> CheckResult:
        del local, files_from
        return CheckResult(differ=("x",))

    async def _push(
        _local: Path, *, bwlimit_kibps: int | None, files_from: object = None
    ) -> TransportResult:
        return TransportResult(ok=True, returncode=0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        push_callable_factory=_factory(_push),
        check_callable_factory=lambda _eq: _always_differ,
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

    async def _push(
        _local: Path, *, bwlimit_kibps: int | None, files_from: object = None
    ) -> TransportResult:
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
        transport=_BogusTransport(),  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="unsupported transport"):
        _build_transport_driver(eq, _StubKeyring())


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


# ---------------------------------------------------------------------------
# Phase 4: per-file verify reconciliation
# ---------------------------------------------------------------------------


async def test_partial_batch_credits_verified_files_in_sync_state(tmp_path: Path) -> None:
    """A batch where one file fails verification still credits the others
    in ``sync_state.json`` -- operator-free per-file NAS sync 'Failure
    handling': a single bad file must not block the good ones."""
    import hashlib

    from exlab_wizard.cache.sync_state_writer import SyncStateWriter

    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)  # data.bin + subdir/child.txt
    writer = CreationWriter(lock_timeout_seconds=10.0)
    sync_state = SyncStateWriter()

    async def _push(_local: Path, *, bwlimit_kibps: int | None, files_from: object = None):
        return TransportResult(ok=True, returncode=0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        sync_state_writer=sync_state,
        push_callable_factory=_factory(_push),
        check_callable_factory=corrupt_one_check_factory("subdir/child.txt"),
        worker_poll_interval_s=0.005,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir, ["data.bin", "subdir/child.txt"])
        # child.txt mismatches on every probe -> the hash-mismatch path
        # retries once then terminates the whole batch job at FAILED.
        for _ in range(600):
            row = await client._queue.get_by_id(handle.job_id)
            if row is not None and row.state is SyncJobState.FAILED:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("expected the batch job to terminate FAILED")

        # Even though the batch job is terminal FAILED, per-file
        # reconciliation must still have credited data.bin: it verified,
        # so it carries a synced_signature + verified_at. child.txt did
        # NOT verify -> uncredited.
        state = await sync_state.read(run_dir)
        assert "data.bin" in state.files
        assert state.files["data.bin"].synced_signature is not None
        assert state.files["data.bin"].verified_at is not None
        assert "subdir/child.txt" not in state.files
    finally:
        await client.close()


async def test_full_batch_credits_every_file_in_sync_state(tmp_path: Path) -> None:
    """A fully-successful batch credits every verified file in sync_state.json."""
    from exlab_wizard.cache.sync_state_writer import SyncStateWriter

    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)
    writer = CreationWriter(lock_timeout_seconds=10.0)
    sync_state = SyncStateWriter()

    async def _push(_local: Path, *, bwlimit_kibps: int | None, files_from: object = None):
        return TransportResult(ok=True, returncode=0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        sync_state_writer=sync_state,
        push_callable_factory=_factory(_push),
        check_callable_factory=local_check_factory(),
        worker_poll_interval_s=0.005,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir, ["data.bin", "subdir/child.txt"])
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
            pytest.fail("expected eventual VERIFIED")
        state = await sync_state.read(run_dir)
        assert {"data.bin", "subdir/child.txt"} <= set(state.files)
        for rec in state.files.values():
            assert rec.synced_signature is not None
            assert rec.verified_at is not None
    finally:
        await client.close()
