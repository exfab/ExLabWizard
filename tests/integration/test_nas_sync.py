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
    NasConfig,
    PathsConfig,
    RcloneSftpTransport,
)
from exlab_wizard.constants import (
    CACHE_DIR_NAME,
    CREATION_JSON_NAME,
    CREATION_JSON_VERSION,
)
from exlab_wizard.constants import SyncHandleState as HandleState
from exlab_wizard.sync.nas_client import NASSyncClient
from exlab_wizard.sync.queue import SyncJobRow, SyncJobState
from exlab_wizard.validator.engine import Validator


class _StubKeyring:
    """Minimal keyring stub returning a fixed password for every username.

    Retained for additive compatibility: the rclone-named-remote
    migration moved credentials into the operator's rclone.conf, so
    ``NASSyncClient`` no longer resolves passwords from the keyring. The
    ``keyring_store`` constructor arg still exists (it is accepted and
    ignored); these tests keep passing this stub so the call sites match
    the not-yet-migrated tray surface.
    """

    def __init__(self, password: str = "testpw") -> None:
        self._password = password

    def get_password(self, *, username: str) -> str:
        del username
        return self._password


@pytest.fixture()
def stub_binaries_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Install the rclone stub on PATH for the test."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fixtures = Path(__file__).parent.parent / "fixtures"
    target = bin_dir / "rclone"
    shutil.copy(fixtures / "stub_rclone.py", target)
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
                transport=RcloneSftpTransport(
                    type="rclone_sftp",
                    host="nas.lab.example",
                    user="testuser",
                    remote_path="/srv/nas",
                    bandwidth=BandwidthConfig(),
                ),
            )
        ],
        # rclone-named-remote migration: the sync target is composed from
        # the ``nas:`` block (named remote + base root). ``mtime_tolerance_s``
        # is generous so the size+modtime reconcile credits stub-copied
        # files (the stub preserves modtime, but filesystems round).
        nas=NasConfig(remote="nas01", base_root="/srv/nas", mtime_tolerance_s=5),
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
    run_dir = local_root / "EQ1" / "PROJ-0042" / "Runs" / "Run_2026-04-17T14-32-00"
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

    The test config sets ``min_verify_passes=1`` and ``min_age_hours=0``
    so the cleanup reaper runs in the same worker pass that promotes
    the job to ``VERIFIED``. Per spec §7.1.10 (metadata-only retention),
    the post-cleanup ``creation.json`` ``sync_status`` is ``"cleaned"``,
    not ``"synced"``: ``"synced"`` is the transient state set by
    ``_mark_synced`` between ``VERIFIED`` and ``CLEANED``, and
    ``_mark_cleaned`` flips it to ``"cleaned"`` immediately after the
    local data files are removed. Either value is correct for a
    successful happy-path run; we accept both so the test is robust to
    the worker scheduling jitter that decides which one we observe.
    """
    local_root = tmp_path / "local"
    local_root.mkdir()
    nas_root = tmp_path / "nas"
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "success")
    monkeypatch.setenv("STUB_RCLONE_DEST_ROOT", str(nas_root))
    # rclone-named-remote migration: credentials live in the operator's
    # rclone.conf named remote, so the production push/lsjson path injects
    # NO inline backend env -- the previous STUB_RCLONE_REQUIRE_ENV
    # assertion no longer applies.

    cfg = _build_config(local_root)
    run_dir = await _populate_run(local_root)
    writer = CreationWriter(lock_timeout_seconds=10.0)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        keyring_store=_StubKeyring(),
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

        # ``creation.json`` reflects the post-sync status. Per §7.1.10
        # the post-cleanup status is ``"cleaned"``; ``"synced"`` is the
        # transient state set by ``_mark_synced`` before cleanup runs.
        # Both are valid happy-path outcomes; cleanup may have already
        # fired by the time we read the file.
        creation_path = run_dir / CACHE_DIR_NAME / CREATION_JSON_NAME
        decoded = msgspec_json.decode(creation_path.read_bytes(), type=CreationJson)
        assert decoded.sync_status in {"synced", "cleaned"}
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

    bad_dir = local_root / "EQ1" / "PROJ-0042" / "Runs" / "Run_<run_date>"
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
        keyring_store=_StubKeyring(),
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
        keyring_store=_StubKeyring(),
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
    stub_binaries_on_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
        keyring_store=_StubKeyring(),
    )
    await client.init()
    # force_verify needs an equipment match in the run path; populate
    # sync_state so the verifier has a file set to scope against.
    from exlab_wizard.cache.sync_state_writer import SyncStateWriter

    sync_state = SyncStateWriter()
    await sync_state.upsert_file(run_dir, "data.bin", synced_signature=(7, 7))
    # Tell stub_rclone to report every files-from entry as ``=`` so the
    # check pass returns ok=True.
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "check_success")
    try:
        result = await client.force_verify(run_dir)
        assert result.ok is True
        # The verifier no longer writes a durable on-disk manifest --
        # sync_state.json is the audit-trail surface now (Slot A).
        assert not (run_dir / CACHE_DIR_NAME / "checksums.sha256").exists()
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Routine reconcile + cleanup integrity gate (rclone-named-remote migration)
# ---------------------------------------------------------------------------


async def test_routine_reconcile_retries_until_remote_listing_settles(
    stub_binaries_on_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reconcile that initially finds nothing remotely re-queues, then verifies.

    The injected ``lsjson_callable_factory`` returns an empty manifest on
    the first pass (nothing on the remote yet) so the routine reconcile is
    incomplete and the job re-queues; the second pass returns a perfect
    manifest and the job reaches VERIFIED.
    """
    from exlab_wizard.sync.manifest import RemoteEntry, RemoteManifest

    local_root = tmp_path / "local"
    local_root.mkdir()
    nas_root = tmp_path / "nas"
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "success")
    monkeypatch.setenv("STUB_RCLONE_DEST_ROOT", str(nas_root))

    cfg = _build_config(local_root)
    run_dir = await _populate_run(local_root)
    writer = CreationWriter(lock_timeout_seconds=10.0)

    counter = [0]

    def _factory(_equipment):
        async def _lsjson(run: Path) -> RemoteManifest:
            counter[0] += 1
            if counter[0] == 1:
                return RemoteManifest(entries={})
            st = (run / "data.bin").stat()
            import datetime as _dt

            mod = (
                _dt.datetime.fromtimestamp(st.st_mtime, tz=_dt.UTC)
                .isoformat()
                .replace("+00:00", "Z")
            )
            return RemoteManifest(
                entries={"data.bin": RemoteEntry(size=st.st_size, mod_time=mod, is_dir=False)}
            )

        return _lsjson

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        keyring_store=_StubKeyring(),
        worker_poll_interval_s=0.01,
        lsjson_callable_factory=_factory,
        # min_verify_passes default (1) + min_age (0) would run cleanup; the
        # cleanup hash-gate uses the real stub check, which succeeds. Either
        # VERIFIED or a cleanup state is an acceptable terminal-of-success.
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir)
        assert handle.state == HandleState.QUEUED
        row = await _wait_for_state(
            client._queue.get_by_id,
            handle.job_id,
            {SyncJobState.VERIFIED, SyncJobState.CLEANUP_ELIGIBLE, SyncJobState.CLEANED},
        )
        assert row.state in {
            SyncJobState.VERIFIED,
            SyncJobState.CLEANUP_ELIGIBLE,
            SyncJobState.CLEANED,
        }
        # The reconcile ran at least twice: the empty listing forced a
        # re-queue before the settled listing credited the file.
        assert counter[0] >= 2
    finally:
        await client.close()


async def test_cleanup_hash_gate_defers_on_remote_mismatch(
    stub_binaries_on_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-deletion hash-gate defers cleanup when the stub check differs.

    The routine reconcile uses the real stub ``lsjson`` (a perfect
    listing) so the job promotes to VERIFIED, but the cleanup integrity
    gate runs a ``check`` callable that reports every file as differing --
    so cleanup is deferred (CLEANUP_ELIGIBLE) and the local data survives
    rather than being deleted. (Push + lsjson use the real stub at
    ``success``; only the hash-gate check is overridden, because the stub
    uses one behavior var across all verbs.)
    """
    from exlab_wizard.sync.transports.rclone import CheckResult

    local_root = tmp_path / "local"
    local_root.mkdir()
    nas_root = tmp_path / "nas"
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "success")
    monkeypatch.setenv("STUB_RCLONE_DEST_ROOT", str(nas_root))

    cfg = _build_config(local_root)
    run_dir = await _populate_run(local_root)
    writer = CreationWriter(lock_timeout_seconds=10.0)

    def _check_factory(_equipment):
        async def _check(local: Path, *, files_from: Path) -> CheckResult:
            del local
            files = tuple(
                line.strip()
                for line in files_from.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
            return CheckResult(differ=files)

        return _check

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        keyring_store=_StubKeyring(),
        worker_poll_interval_s=0.01,
        check_callable_factory=_check_factory,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir)
        row = await _wait_for_state(
            client._queue.get_by_id,
            handle.job_id,
            {SyncJobState.CLEANUP_ELIGIBLE},
        )
        assert row.state is SyncJobState.CLEANUP_ELIGIBLE
        # The integrity gate failed -> local data must survive.
        assert (run_dir / "data.bin").exists()
    finally:
        await client.close()


async def test_poller_per_file_enqueue_drives_to_synced_state(
    stub_binaries_on_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Poller sweep -> per-file enqueue -> drive -> verify -> sync_state.json
    records ``synced_signature`` + ``verified_at`` for the synced files.

    Exercises the full operator-free per-file NAS sync path end-to-end:
    the :class:`QuiescenceSyncPoller` discovers the run, computes the
    eligible file list, and feeds it to a real :class:`NASSyncClient` that
    drives the job through the stub rclone transport + verifier.
    """
    from exlab_wizard.cache.sync_state_writer import SyncStateWriter
    from exlab_wizard.orchestrator.quiescence_poller import QuiescenceSyncPoller

    local_root = tmp_path / "local"
    local_root.mkdir()
    nas_root = tmp_path / "nas"
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "success")
    monkeypatch.setenv("STUB_RCLONE_DEST_ROOT", str(nas_root))

    cfg = _build_config(local_root)
    run_dir = await _populate_run(local_root)
    writer = CreationWriter(lock_timeout_seconds=10.0)
    sync_state = SyncStateWriter()

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        keyring_store=_StubKeyring(),
        sync_state_writer=sync_state,
        worker_poll_interval_s=0.01,
    )
    await client.init()
    poller = QuiescenceSyncPoller(
        config=cfg,
        nas_sync=client,
        sync_state_writer=sync_state,
    )
    try:
        # First sweep observes the file; second sweep (past the settle
        # window) finds it quiet and enqueues the per-file subset.
        assert await poller.poll_once(now_monotonic=0.0) == []
        enqueued = await poller.poll_once(now_monotonic=cfg.sync.quiescence_minutes * 60 + 1.0)
        assert enqueued == [run_dir]

        # The worker drives the per-file job through to VERIFIED.
        async def _by_run_path(_ignored: str) -> SyncJobRow | None:
            return await client._queue.get_by_run_path(run_dir)

        row = await _wait_for_state(
            _by_run_path,
            "",
            {SyncJobState.VERIFIED, SyncJobState.CLEANUP_ELIGIBLE, SyncJobState.CLEANED},
        )
        assert row.state in {
            SyncJobState.VERIFIED,
            SyncJobState.CLEANUP_ELIGIBLE,
            SyncJobState.CLEANED,
        }
        assert row.files == ("data.bin",)

        # sync_state.json records the verified file.
        state = await sync_state.read(run_dir)
        assert "data.bin" in state.files
        assert state.files["data.bin"].synced_signature is not None
        assert state.files["data.bin"].verified_at is not None
    finally:
        await client.close()


async def test_poller_to_cleanup_honors_keep_local_and_stamps_cleared(
    stub_binaries_on_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full Phase 5 path: poller sweep -> enqueue -> verify -> SYNCED rollup
    -> cleanup runs, keeping a ``keep_local`` file and stamping ``cleared_at``.

    Exercises the operator-free per-file NAS sync cleanup contract
    end-to-end with a real :class:`NASSyncClient` over the stub rclone
    transport: the run carries two data files, one flagged ``keep_local``;
    after cleanup the kept file survives, the other is removed, the
    ``.exlab-wizard/`` metadata subtree is retained, and the run's
    ``sync_state.json`` rolls up to ``CLEARED``.
    """
    from exlab_wizard.cache.sync_state_writer import SyncStateWriter
    from exlab_wizard.constants import RunSyncState
    from exlab_wizard.orchestrator.quiescence_poller import QuiescenceSyncPoller

    local_root = tmp_path / "local"
    local_root.mkdir()
    nas_root = tmp_path / "nas"
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "success")
    monkeypatch.setenv("STUB_RCLONE_DEST_ROOT", str(nas_root))

    # ``min_verify_passes=1`` + ``min_age_hours=0`` so cleanup runs in the
    # same worker pass that promotes the job to VERIFIED.
    cfg = _build_config(local_root)
    run_dir = local_root / "EQ1" / "PROJ-0042" / "Runs" / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    (run_dir / "data.bin").write_bytes(b"payload-bytes")
    (run_dir / "keep.bin").write_bytes(b"keep-me-local")
    cache = run_dir / CACHE_DIR_NAME
    cache.mkdir()
    (cache / CREATION_JSON_NAME).write_bytes(msgspec_json.encode(_make_creation(run_dir)))

    writer = CreationWriter(lock_timeout_seconds=10.0)
    sync_state = SyncStateWriter()
    # Operator flags one file keep-local before the sync runs.
    await sync_state.set_keep_local(run_dir, "keep.bin", True)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        keyring_store=_StubKeyring(),
        sync_state_writer=sync_state,
        worker_poll_interval_s=0.01,
    )
    await client.init()
    poller = QuiescenceSyncPoller(
        config=cfg,
        nas_sync=client,
        sync_state_writer=sync_state,
    )
    try:
        # Poller discovers the run and enqueues its quiet files past the
        # settle window.
        assert await poller.poll_once(now_monotonic=0.0) == []
        enqueued = await poller.poll_once(now_monotonic=cfg.sync.quiescence_minutes * 60 + 1.0)
        assert enqueued == [run_dir]

        async def _by_run_path(_ignored: str) -> SyncJobRow | None:
            return await client._queue.get_by_run_path(run_dir)

        # The worker drives the job through verify into the cleanup states.
        await _wait_for_state(
            _by_run_path,
            "",
            {SyncJobState.CLEANED},
        )

        # The keep_local file survives; the other data file is removed.
        assert (run_dir / "keep.bin").exists()
        assert not (run_dir / "data.bin").exists()
        # The metadata subtree is retained so tombstones still render.
        assert cache.exists()

        # sync_state.json rolled up to CLEARED (cleared_at stamped).
        state = await sync_state.read(run_dir)
        assert state.cleared_at is not None
        assert SyncStateWriter.rollup_state(state) is RunSyncState.CLEARED
        # Both files were credited as verified before cleanup ran.
        assert state.files["data.bin"].verified_at is not None
        assert state.files["keep.bin"].verified_at is not None
        assert state.files["keep.bin"].keep_local is True
    finally:
        await client.close()
