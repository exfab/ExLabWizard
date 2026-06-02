"""Unit tests for ``exlab_wizard.orchestrator.quiescence_poller``.

The :class:`QuiescenceSyncPoller` is driven synchronously through
:meth:`poll_once` with an injected monotonic clock so the per-file settle
window can be exercised deterministically without real time passing.

Coverage:

* a file becomes quiet only after the settle window has elapsed across
  the poller's own sweeps;
* ``sync.ignore_globs`` files never make a run eligible;
* discovery spans both a staging-mode run and a ``nas``-mode run;
* per-file eligibility: a file matching its recorded ``synced_signature``
  is not re-enqueued, and a file modified after a recorded sync becomes
  eligible again.
"""

from __future__ import annotations

from pathlib import Path

from exlab_wizard.cache.sync_state_writer import SyncStateWriter
from exlab_wizard.config.models import (
    Config,
    EquipmentConfig,
    OrchestratorConfig,
    PathsConfig,
    SyncConfig,
)
from exlab_wizard.constants import RUNS_DIR_NAME, SyncMode
from exlab_wizard.orchestrator.quiescence_poller import QuiescenceSyncPoller

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubNasSync:
    """In-memory NAS-sync stub recording per-file enqueue calls."""

    def __init__(self) -> None:
        self.enqueued: list[tuple[Path, list[str]]] = []

    async def enqueue(self, run_path: Path, files: list[str] | None = None) -> None:
        self.enqueued.append((run_path, list(files or [])))

    @property
    def enqueued_paths(self) -> list[Path]:
        return [run_path for run_path, _ in self.enqueued]


# ---------------------------------------------------------------------------
# Config / fixture helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    staging_root: Path | None = None,
    nas_equipment_root: Path | None = None,
    quiescence_minutes: int = 1,
) -> Config:
    """Build a Config with an optional staging root and a nas-mode equipment."""
    equipment: list[EquipmentConfig] = []
    if nas_equipment_root is not None:
        equipment.append(
            EquipmentConfig(
                id="EQNAS",
                label="Nas Equipment",
                nas_root="/nas",
                sync_mode=SyncMode.NAS,
            ),
        )
    # Nas-mode runs are discovered under ``<config.paths.local_root>/<id>`` where
    # local_root is the derived ``<app_root>/data``. Set app_root to the passed
    # ``nas_equipment_root`` so the discovered data root is
    # ``<nas_equipment_root>/data`` -- the nas-discovery tests seed runs there.
    # Staging-only configs don't use the data root (the staging branch walks
    # ``orchestrator.staging_root`` directly), so any valid app_root suffices.
    app_root = nas_equipment_root or staging_root or Path("/tmp")
    return Config(
        paths=PathsConfig(app_root=str(app_root)),
        equipment=equipment,
        orchestrator=OrchestratorConfig(
            label="ORCH",
            staging_root=str(staging_root) if staging_root is not None else "",
        ),
        sync=SyncConfig(quiescence_minutes=quiescence_minutes),
    )


def _make_run(root: Path, equipment_id: str, *, with_file: bool = True) -> Path:
    """Create a run-leaf directory mirroring the §13.2 staging layout."""
    run_dir = root / equipment_id / "PROJ-0001" / RUNS_DIR_NAME / "Run_2026-05-21T10-00-00"
    run_dir.mkdir(parents=True)
    if with_file:
        (run_dir / "data.bin").write_bytes(b"payload" * 100)
    return run_dir


def _poller(config: Config, nas_sync: _StubNasSync) -> QuiescenceSyncPoller:
    return QuiescenceSyncPoller(
        config=config,
        nas_sync=nas_sync,
        sync_state_writer=SyncStateWriter(),
    )


def _signature(path: Path) -> tuple[int, int]:
    st = path.stat()
    return (st.st_size, st.st_mtime_ns)


# ---------------------------------------------------------------------------
# Settle window
# ---------------------------------------------------------------------------


async def test_file_becomes_quiet_only_after_settle_window(tmp_path: Path) -> None:
    """A file is enqueued only after its signature settles for the window."""
    config = _make_config(staging_root=tmp_path, quiescence_minutes=1)  # 60s window
    nas_sync = _StubNasSync()
    poller = _poller(config, nas_sync)
    run_dir = _make_run(tmp_path, "EQ1")

    # First sweep: file just observed -- not yet quiet.
    enqueued = await poller.poll_once(now_monotonic=0.0)
    assert enqueued == []
    assert nas_sync.enqueued == []

    # 30s later: still inside the window.
    enqueued = await poller.poll_once(now_monotonic=30.0)
    assert enqueued == []

    # 60s after first observation: window elapsed -> enqueued.
    enqueued = await poller.poll_once(now_monotonic=60.0)
    assert enqueued == [run_dir]
    assert nas_sync.enqueued == [(run_dir, ["data.bin"])]


async def test_modified_file_resets_the_settle_window(tmp_path: Path) -> None:
    """A signature change resets ``first_seen``; the window restarts."""
    config = _make_config(staging_root=tmp_path, quiescence_minutes=1)
    nas_sync = _StubNasSync()
    poller = _poller(config, nas_sync)
    run_dir = _make_run(tmp_path, "EQ1")
    target = run_dir / "data.bin"

    await poller.poll_once(now_monotonic=0.0)
    # Modify the file just before the window would elapse.
    target.write_bytes(b"changed-payload" * 50)
    await poller.poll_once(now_monotonic=50.0)
    # 60s after the *first* observation -- but the file changed at 50s, so
    # it is not yet quiet.
    enqueued = await poller.poll_once(now_monotonic=60.0)
    assert enqueued == []
    # 60s after the modification -> finally quiet.
    enqueued = await poller.poll_once(now_monotonic=110.0)
    assert enqueued == [run_dir]


# ---------------------------------------------------------------------------
# Ignore globs
# ---------------------------------------------------------------------------


async def test_ignore_glob_files_do_not_make_a_run_eligible(tmp_path: Path) -> None:
    """A run holding only ignore-glob files never enqueues."""
    config = _make_config(staging_root=tmp_path, quiescence_minutes=1)
    nas_sync = _StubNasSync()
    poller = _poller(config, nas_sync)
    run_dir = _make_run(tmp_path, "EQ1", with_file=False)
    # Default ignore_globs == ["*.partial", "*.tmp"].
    (run_dir / "upload.partial").write_bytes(b"in-progress")
    (run_dir / "scratch.tmp").write_bytes(b"temp")

    await poller.poll_once(now_monotonic=0.0)
    enqueued = await poller.poll_once(now_monotonic=120.0)
    assert enqueued == []
    assert nas_sync.enqueued == []


async def test_quiet_real_file_enqueues_despite_ignored_sibling(tmp_path: Path) -> None:
    """A quiet non-ignored file enqueues even when ignored files coexist."""
    config = _make_config(staging_root=tmp_path, quiescence_minutes=1)
    nas_sync = _StubNasSync()
    poller = _poller(config, nas_sync)
    run_dir = _make_run(tmp_path, "EQ1")
    (run_dir / "upload.partial").write_bytes(b"in-progress")

    await poller.poll_once(now_monotonic=0.0)
    enqueued = await poller.poll_once(now_monotonic=120.0)
    assert enqueued == [run_dir]
    # Only the real file rides the enqueue; the ignored sibling never does.
    assert nas_sync.enqueued == [(run_dir, ["data.bin"])]


# ---------------------------------------------------------------------------
# Discovery: staging-mode and nas-mode
# ---------------------------------------------------------------------------


async def test_discovers_both_staging_and_nas_mode_runs(tmp_path: Path) -> None:
    """A sweep finds runs under staging_root AND under nas-mode local_root."""
    staging_root = tmp_path / "staging"
    nas_root = tmp_path / "nas-local"
    staging_root.mkdir()
    nas_root.mkdir()
    config = _make_config(
        staging_root=staging_root,
        nas_equipment_root=nas_root,
        quiescence_minutes=1,
    )
    nas_sync = _StubNasSync()
    poller = _poller(config, nas_sync)
    staging_run = _make_run(staging_root, "EQ1")
    # Nas-mode runs live under the derived data root ``<app_root>/data``.
    nas_run = _make_run(nas_root / "data", "EQNAS")

    await poller.poll_once(now_monotonic=0.0)
    enqueued = await poller.poll_once(now_monotonic=120.0)
    assert set(enqueued) == {staging_run, nas_run}
    assert set(nas_sync.enqueued_paths) == {staging_run, nas_run}


async def test_co_rooted_stage_mode_equipment_run_is_not_enqueued(tmp_path: Path) -> None:
    """A ``stage``-mode equipment sharing one ``local_root`` with a
    ``nas``-mode equipment must NOT have its runs swept into NAS sync --
    only the ``nas``-mode equipment's own subtree is walked."""
    app_root = tmp_path / "lab-data"
    # Both equipment share the single derived data root ``<app_root>/data``;
    # the nas- and stage-mode subtrees are co-rooted there.
    shared_data_root = app_root / "data"
    shared_data_root.mkdir(parents=True)
    config = Config(
        paths=PathsConfig(app_root=str(app_root)),
        equipment=[
            EquipmentConfig(
                id="EQNAS",
                label="Nas Equipment",
                nas_root="/nas",
                sync_mode=SyncMode.NAS,
            ),
            EquipmentConfig(
                id="EQSTAGE",
                label="Stage Equipment",
                nas_root="/nas",
                sync_mode=SyncMode.STAGE,
            ),
        ],
        orchestrator=OrchestratorConfig(label="ORCH", staging_root=""),
        sync=SyncConfig(quiescence_minutes=1),
    )
    nas_sync = _StubNasSync()
    poller = _poller(config, nas_sync)
    nas_run = _make_run(shared_data_root, "EQNAS")
    stage_run = _make_run(shared_data_root, "EQSTAGE")

    await poller.poll_once(now_monotonic=0.0)
    enqueued = await poller.poll_once(now_monotonic=120.0)
    # Only the nas-mode equipment's run is enqueued; the co-rooted
    # stage-mode run reaches the NAS via the orchestrator staging area.
    assert enqueued == [nas_run]
    assert stage_run not in nas_sync.enqueued_paths


# ---------------------------------------------------------------------------
# Per-file eligibility against sync_state.json
# ---------------------------------------------------------------------------


async def test_file_matching_synced_signature_is_not_re_enqueued(tmp_path: Path) -> None:
    """A quiet file already synced at its current signature is skipped."""
    config = _make_config(staging_root=tmp_path, quiescence_minutes=1)
    nas_sync = _StubNasSync()
    writer = SyncStateWriter()
    poller = QuiescenceSyncPoller(config=config, nas_sync=nas_sync, sync_state_writer=writer)
    run_dir = _make_run(tmp_path, "EQ1")
    target = run_dir / "data.bin"

    # Record the file as already synced at its current (size, mtime).
    await writer.upsert_file(
        run_dir,
        "data.bin",
        synced_signature=_signature(target),
        verified_at="2026-05-21T10:00:00Z",
    )

    await poller.poll_once(now_monotonic=0.0)
    enqueued = await poller.poll_once(now_monotonic=120.0)
    # The only file matches its recorded signature -> nothing eligible.
    assert enqueued == []
    assert nas_sync.enqueued == []


async def test_file_modified_after_recorded_sync_becomes_eligible(tmp_path: Path) -> None:
    """A file modified after its recorded sync re-enters the eligible set."""
    config = _make_config(staging_root=tmp_path, quiescence_minutes=1)
    nas_sync = _StubNasSync()
    writer = SyncStateWriter()
    poller = QuiescenceSyncPoller(config=config, nas_sync=nas_sync, sync_state_writer=writer)
    run_dir = _make_run(tmp_path, "EQ1")

    # Record a stale signature (a sync that predates the current content).
    await writer.upsert_file(
        run_dir,
        "data.bin",
        synced_signature=(1, 1),
        verified_at="2026-05-21T10:00:00Z",
    )

    await poller.poll_once(now_monotonic=0.0)
    enqueued = await poller.poll_once(now_monotonic=120.0)
    # Current signature differs from the recorded one -> eligible again.
    assert enqueued == [run_dir]
    assert nas_sync.enqueued == [(run_dir, ["data.bin"])]


async def test_only_changed_file_in_a_run_is_enqueued(tmp_path: Path) -> None:
    """A run with a mix of synced + modified files enqueues only the dirty one."""
    config = _make_config(staging_root=tmp_path, quiescence_minutes=1)
    nas_sync = _StubNasSync()
    writer = SyncStateWriter()
    poller = QuiescenceSyncPoller(config=config, nas_sync=nas_sync, sync_state_writer=writer)
    run_dir = _make_run(tmp_path, "EQ1", with_file=False)
    clean = run_dir / "clean.bin"
    dirty = run_dir / "dirty.bin"
    clean.write_bytes(b"clean-data")
    dirty.write_bytes(b"dirty-data")

    # clean.bin is recorded at its current signature; dirty.bin at a stale one.
    await writer.upsert_file(
        run_dir, "clean.bin", synced_signature=_signature(clean), verified_at="2026-05-21T10:00:00Z"
    )
    await writer.upsert_file(
        run_dir, "dirty.bin", synced_signature=(9, 9), verified_at="2026-05-21T10:00:00Z"
    )

    await poller.poll_once(now_monotonic=0.0)
    enqueued = await poller.poll_once(now_monotonic=120.0)
    assert enqueued == [run_dir]
    assert nas_sync.enqueued == [(run_dir, ["dirty.bin"])]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_start_then_stop_is_idempotent(tmp_path: Path) -> None:
    """start()/stop() can be called repeatedly without error."""
    config = _make_config(staging_root=tmp_path, quiescence_minutes=1)
    poller = _poller(config, _StubNasSync())
    await poller.start()
    await poller.start()  # idempotent
    await poller.stop()
    await poller.stop()  # idempotent


# ---------------------------------------------------------------------------
# apply_config: live reload (no tray relaunch)
# ---------------------------------------------------------------------------


def test_apply_config_swaps_config_reference(tmp_path: Path) -> None:
    nas_sync = _StubNasSync()
    poller = _poller(_make_config(staging_root=tmp_path, quiescence_minutes=2), nas_sync)
    new = _make_config(staging_root=tmp_path, quiescence_minutes=5)
    poller.apply_config(new)
    assert poller._config is new


async def test_apply_config_quiescence_change_is_live(tmp_path: Path) -> None:
    """A live quiescence-window change takes effect on the next sweep.

    poll_once re-reads ``quiescence_minutes`` from ``self._config`` each
    sweep, so shortening the window via ``apply_config`` lets an
    already-observed run quiesce sooner -- no tray relaunch.
    """
    nas_sync = _StubNasSync()
    poller = _poller(_make_config(staging_root=tmp_path, quiescence_minutes=2), nas_sync)  # 120s
    run_dir = _make_run(tmp_path, "EQ1")

    # Observe at t=0; at t=60 the run is still inside the 120s window.
    await poller.poll_once(now_monotonic=0.0)
    assert await poller.poll_once(now_monotonic=60.0) == []

    # Shorten the window to 60s live; the same run is now quiesced at t=60.
    poller.apply_config(_make_config(staging_root=tmp_path, quiescence_minutes=1))
    assert await poller.poll_once(now_monotonic=60.0) == [run_dir]
