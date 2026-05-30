"""Expand the declarative ``SAMPLES`` list into an on-disk demo tree.

Design spec §6 (generation flow), §7 (guardrails), §8 (module layout).

The generator turns each :class:`~exlab_wizard.sample_data.spec.SampleEquipment`
into a real folder tree under the ``-test`` sandbox, writing every folder's
metadata through the **production producers** (``ReadmeGenerator``,
``CreationWriter``, ``EquipmentCacheWriter``) and the Phase 1 shared value
helpers (``build_readme_context`` / ``build_creation_json``). Because the same
code paths the real wizard uses produce the bytes, the seeded metadata can
never drift from what creation writes.

Sourcing mirrors ``controller/creation.py`` exactly (``_compose_destination_path``,
``_write_cache``, ``_write_equipment_json``):

- folder composition uses ``config.paths.local_root`` (the base) with the
  **prefixed** equipment id (e.g. ``TEST_TESTRIG``);
- ``equipment.json`` lives under ``config.paths.local_root / <prefixed_id>``
  with ``configured_local_root = str(config.paths.local_root)`` and
  ``configured_nas_root`` taken from the equipment entry's ``nas_root``;
- ``creation.json``'s ``paths.nas`` is composed from the equipment entry's
  ``nas_root`` and the prefixed id.

Determinism: a fixed ``base_time`` is the base instant; each run is stamped at
``base_time + minutes_offset`` (or a per-project running index when the sample
leaves ``minutes_offset`` unset). The only non-deterministic bytes are
``equipment.json``'s ``first_seen_at`` / ``last_modified_at``, which the writer
stamps with wall-clock (documented and excluded from determinism assertions).

Safety: the destructive wipe is fenced behind the §7 guardrails -- it refuses
to run unless the resolved app name ends in ``-test`` (which holds only when
``EXLAB_WIZARD_TEST_MODE == "1"``), only ever removes ``local_root/<prefixed_id>``
for ids in ``SAMPLES``, and asserts each target is a strict subpath of the sandbox.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from exlab_wizard.api.schemas import (
    EquipmentJson,
    LimsProjectBlock,
    TestRunsJson,
)
from exlab_wizard.cache.creation_writer import CreationWriter
from exlab_wizard.cache.equipment import EquipmentCacheWriter
from exlab_wizard.config.loader import load_config, save_config
from exlab_wizard.config.models import (
    Config,
    EquipmentConfig,
    OperatorsConfig,
    READMEConfig,
    READMEDefaultField,
)
from exlab_wizard.constants import (
    EQUIPMENT_JSON_VERSION,
    TEST_MODE_ENV,
    TEST_RUNS_JSON_NAME,
    TEST_RUNS_JSON_VERSION,
    CreationLevel,
    FieldType,
    LIMSProjectSource,
    RunKind,
    RunScope,
    SyncStatus,
)
from exlab_wizard.controller.metadata_assembly import (
    TemplateDesc,
    build_creation_json,
    build_readme_context,
)
from exlab_wizard.logging import get_logger
from exlab_wizard.paths import (
    _app_name,
    cache_dir,
    compose_project_path,
    compose_run_path,
    creation_json_path,
    ensure_dir,
    equipment_json_path,
)
from exlab_wizard.readme import ReadmeGenerator
from exlab_wizard.sample_data.spec import SAMPLES, SampleEquipment, SampleProject, SampleRun
from exlab_wizard.utils.time import dt_to_iso

__all__ = ["SampleDataGenerator", "generate_samples"]

_log = get_logger(__name__)

# The single non-required README config default the seeder injects so a run's
# ``readme_extra`` can populate the config_fields layer (design spec §6 step 1).
_SAMPLE_TYPE_DEFAULT = READMEDefaultField(
    id="sample_type",
    label="Sample Type",
    type=FieldType.CHOICE,
    required=False,
    options=["control", "treatment"],
)

# Sentinel template provenance: the seeder never resolves a Copier template, so
# it passes a fixed stand-in (design spec §5/§6 step 4).
_SEED_TEMPLATE = TemplateDesc(
    name="seed",
    version="0",
    source_path="",
    run_scope=RunScope.BOTH,
    extra_readme_fields=[],
    plugin_order=[],
)

# Default payload pair written under a run dir when ``SampleRun.files`` is None.
_DEFAULT_FILES: tuple[tuple[str, str], ...] = (
    ("data/acq_001.csv", "t,value\n0,0.0\n1,1.0\n"),
    ("notes.txt", "Seeded demo run.\n"),
)


def generate_samples(
    config_path: Path,
    *,
    wipe: bool,
    base_time: datetime = datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
) -> None:
    """Expand ``SAMPLES`` into an on-disk demo tree under the sandbox.

    Synchronous facade over the async core (the producers are async). Loads
    the config at ``config_path``, merges the sample equipment + the one
    seeded README default into it, reloads so the ``TEST_`` prefix is stamped,
    optionally wipes the seeded subtrees (guardrailed), then writes every
    folder's metadata through the real producers.

    Args:
        config_path: Path to ``config.yaml`` in the sandbox. The sandbox dir
            is ``config_path.parent``.
        wipe: When True, remove ``local_root/<prefixed_id>`` for each seeded
            equipment before regenerating (after the §7 guardrail check). When
            False, the existing tree is left in place and never deleted.
        base_time: The fixed base instant; per-run timestamps are
            ``base_time + minutes_offset``.
    """
    SampleDataGenerator(config_path=config_path, base_time=base_time).generate(wipe=wipe)


class SampleDataGenerator:
    """Expands :data:`SAMPLES` into an on-disk tree (design spec §6)."""

    def __init__(
        self,
        *,
        config_path: Path,
        base_time: datetime = datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
    ) -> None:
        self._config_path = Path(config_path)
        self._sandbox = self._config_path.parent
        self._base_time = base_time
        self._readme = ReadmeGenerator()
        self._creation = CreationWriter()
        self._equipment = EquipmentCacheWriter()

    def generate(self, *, wipe: bool) -> None:
        """Synchronous wrapper running the async core on a fresh loop."""
        asyncio.run(self._generate_async(wipe=wipe))

    # ------------------------------------------------------------------
    # Async core
    # ------------------------------------------------------------------

    async def _generate_async(self, *, wipe: bool) -> None:
        config = self._build_and_reload_config()
        local_root = Path(config.paths.local_root)

        # The loaded config carries the prefixed ids in SAMPLES order, so we
        # can pair each SampleEquipment with its prefixed EquipmentConfig.
        if len(config.equipment) != len(SAMPLES):  # pragma: no cover - defensive
            msg = (
                "loaded equipment count does not match SAMPLES; "
                f"expected {len(SAMPLES)}, got {len(config.equipment)}"
            )
            raise RuntimeError(msg)

        if wipe:
            self._wipe(config)

        for sample_eq, eq_cfg in zip(SAMPLES, config.equipment, strict=True):
            await self._write_equipment(config, sample_eq, eq_cfg, local_root)

    # ------------------------------------------------------------------
    # Step 1 -- build & reload config
    # ------------------------------------------------------------------

    def _build_and_reload_config(self) -> Config:
        """Merge sample equipment + the seeded README default; reload prefixed.

        Converts each :class:`SampleEquipment` into an :class:`EquipmentConfig`
        with **raw** ids and sandbox-derived roots (the loader stamps the
        ``TEST_`` prefix on reload), seeds the single ``sample_type`` README
        default, and clears the operators allowlist. Mirrors the field shape
        of today's ``tray/main.py:_bootstrap_test_config`` literal.
        """
        config = load_config(self._config_path)

        equipment = [
            EquipmentConfig(
                id=sample.id,  # raw; the loader prefixes on reload
                label=sample.label,
                # ``local_root`` / ``nas_root`` are the BASE roots: consumers
                # (orchestrator quiescence poller, validator) compose
                # ``Path(local_root) / equipment.id``, and ``build_creation_json``
                # composes ``Path(nas_root) / equipment_id`` -- so the id is
                # appended downstream, never baked in here (matches a real
                # operator config and ``config.paths.local_root``).
                local_root=str(self._sandbox / "local"),
                nas_root=str(self._sandbox / "nas"),
                sync_mode=sample.sync_mode,
            )
            for sample in SAMPLES
        ]

        merged = config.model_copy(
            update={
                "equipment": equipment,
                "readme": READMEConfig(defaults=[_SAMPLE_TYPE_DEFAULT]),
                "operators": OperatorsConfig(allowlist=[]),
            }
        )
        save_config(self._config_path, merged)

        # Reload so apply_test_mode_prefix stamps the TEST_ prefix; from here
        # the loaded (prefixed) ids drive every path.
        return load_config(self._config_path)

    # ------------------------------------------------------------------
    # Step 2 -- guardrailed wipe (design spec §7)
    # ------------------------------------------------------------------

    def _wipe(self, config: Config) -> None:
        """Remove ``local_root/<prefixed_id>`` for each seeded equipment.

        Enforces every §7 guardrail before touching disk and prints the exact
        list of directories it will remove (visibility requirement). Raises
        loudly -- never a silent no-op -- when a precondition fails.
        """
        # The wipe only fires inside the test sandbox. ``_app_name()`` returns
        # the ``-test`` suffix exactly when EXLAB_WIZARD_TEST_MODE == "1", so
        # this single check is both the test-mode gate and the sandbox gate.
        app_name = _app_name()
        if not app_name.endswith("-test"):
            msg = (
                f"refusing to wipe: resolved app name {app_name!r} does not end "
                f"in '-test'. The destructive wipe only runs inside the test "
                f"sandbox (set {TEST_MODE_ENV}=1); a real local_root must never "
                "be deletable here."
            )
            raise RuntimeError(msg)

        local_root = Path(config.paths.local_root)
        sandbox = self._sandbox.resolve()

        # Only ids present in SAMPLES (carried through to the loaded, prefixed
        # config.equipment) are eligible. Resolve + containment-check each.
        targets: list[Path] = []
        for entry in config.equipment:
            target = (local_root / entry.id).resolve()
            if target == sandbox or sandbox not in target.parents:
                msg = f"refusing to wipe {target}: not a strict subpath of the sandbox {sandbox}."
                raise RuntimeError(msg)
            if target == local_root.resolve():
                msg = f"refusing to wipe {target}: that is local_root itself."
                raise RuntimeError(msg)
            targets.append(target)

        print("sample-data wipe: removing the following directories:")
        for target in targets:
            print(f"  {target}")
        for target in targets:
            if target.exists():
                shutil.rmtree(target)

    # ------------------------------------------------------------------
    # Steps 3-4 -- create tree + write metadata
    # ------------------------------------------------------------------

    async def _write_equipment(
        self,
        config: Config,
        sample_eq: SampleEquipment,
        eq_cfg: EquipmentConfig,
        local_root: Path,
    ) -> None:
        prefixed_id = eq_cfg.id
        nas_root = eq_cfg.nas_root

        for project in sample_eq.projects:
            await self._write_project(config, prefixed_id, nas_root, local_root, project)

        # equipment.json once per equipment, under local_root/<prefixed_id>.
        equipment_dir = local_root / prefixed_id
        payload = EquipmentJson(
            schema_version=EQUIPMENT_JSON_VERSION,
            id=prefixed_id,
            label=sample_eq.label,
            configured_local_root=str(local_root),
            configured_nas_root=nas_root,
            # Placeholders; the writer overwrites both with wall-clock time.
            first_seen_at=dt_to_iso(self._base_time),
            last_modified_at=dt_to_iso(self._base_time),
        )
        await self._equipment.write_equipment(equipment_json_path(equipment_dir), payload)

    async def _write_project(
        self,
        config: Config,
        prefixed_id: str,
        nas_root: str,
        local_root: Path,
        project: SampleProject,
    ) -> None:
        project_dir = compose_project_path(
            local_root=local_root,
            equipment_id=prefixed_id,
            project_name=project.name,
        )
        ensure_dir(project_dir)

        lims_block = LimsProjectBlock(
            uid="",
            short_id=project.short_id,
            name_at_creation=project.name,
            source=LIMSProjectSource.LIVE,
        )

        # Project-level metadata (base instant; PENDING -- the controller
        # always writes PENDING at the project level).
        await self._write_metadata(
            config=config,
            prefixed_id=prefixed_id,
            nas_root=nas_root,
            dst=project_dir,
            level=CreationLevel.PROJECT,
            label=project.label,
            operator=project.operator,
            objective=project.objective,
            readme_extra={},
            short_id=project.short_id,
            run_name=None,
            run_kind_value="",
            lims_block=lims_block,
            sync_status=SyncStatus.PENDING,
            instant=self._base_time,
        )

        marker_written = False
        for index, run in enumerate(project.runs):
            run_date = self._run_date(run, index)
            run_dir = compose_run_path(
                local_root=local_root,
                equipment_id=prefixed_id,
                project_name=project.name,
                run_kind=run.kind,
                run_date=run_date,
            )
            ensure_dir(run_dir)
            self._write_payload(run_dir, run)

            await self._write_metadata(
                config=config,
                prefixed_id=prefixed_id,
                nas_root=nas_root,
                dst=run_dir,
                level=CreationLevel.RUN,
                label=run.label,
                operator=run.operator,
                objective=run.objective,
                readme_extra=dict(run.readme_extra),
                short_id=project.short_id,
                run_name=run_dir.name,
                run_kind_value=run.kind.value,
                lims_block=lims_block,
                sync_status=run.sync_status,
                instant=run_date,
            )

            # test_runs.json marker: written the FIRST time a TestRuns/ run is
            # written under this project; the writer is idempotent regardless.
            if run.kind is RunKind.TEST and not marker_written:
                marker = TestRunsJson(
                    schema_version=TEST_RUNS_JSON_VERSION,
                    created_at=dt_to_iso(self._base_time),
                    project=project.short_id,
                    equipment=prefixed_id,
                    run_kind=RunKind.TEST,
                )
                await self._equipment.write_test_runs_marker(
                    cache_dir(project_dir) / TEST_RUNS_JSON_NAME, marker
                )
                marker_written = True

    async def _write_metadata(
        self,
        *,
        config: Config,
        prefixed_id: str,
        nas_root: str,
        dst: Path,
        level: CreationLevel,
        label: str,
        operator: str,
        objective: str,
        readme_extra: dict[str, object],
        short_id: str,
        run_name: str | None,
        run_kind_value: str,
        lims_block: LimsProjectBlock,
        sync_status: SyncStatus,
        instant: datetime,
    ) -> None:
        """Write README + readme_fields.json + creation.json for one folder.

        Uses the Phase 1 shared helpers so the bytes match production exactly;
        ``created`` / ``created_at`` flow from the injected ``instant`` and
        ``created_by`` is the experiment operator (deterministic).
        """
        # Parity with the controller (creation.py): create the ``.exlab-wizard/``
        # cache dir explicitly rather than relying on the README generator's
        # side-effecting mkdir, so ``write_creation`` never races a missing dir.
        ensure_dir(cache_dir(dst))

        ctx = build_readme_context(
            config=config,
            equipment_id=prefixed_id,
            level=level,
            label=label,
            operator=operator,
            objective=objective,
            readme_extra=readme_extra,
            template=_SEED_TEMPLATE,
            short_id=short_id,
            run_name=run_name,
            run_kind_value=run_kind_value,
            created=instant,
            created_by=operator,
        )
        await self._readme.generate(dst, ctx)

        payload = build_creation_json(
            config=config,
            equipment_id=prefixed_id,
            operator=operator,
            level=level,
            run_kind_value=run_kind_value or RunKind.EXPERIMENTAL.value,
            lims_block=lims_block,
            template=_SEED_TEMPLATE,
            variables={},
            dst=dst,
            nas_root=nas_root,
            plugins_applied=[],
            sync_status=sync_status,
            created_at_iso=dt_to_iso(instant),
        )
        await self._creation.write_creation(creation_json_path(dst), payload)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run_date(self, run: SampleRun, index: int) -> datetime:
        """Deterministic run instant: ``base_time + (minutes_offset or index+1)``.

        Auto offsets start at 1 (not 0) so a run never collides with the
        project's base-instant metadata, and increment per run so minute-
        precision run paths within a project are unique and reproducible.
        """
        offset = run.minutes_offset if run.minutes_offset is not None else index + 1
        return self._base_time + timedelta(minutes=offset)

    def _write_payload(self, run_dir: Path, run: SampleRun) -> None:
        """Write the run's payload files (or the default pair when None)."""
        files = (
            [(f.relpath, f.content) for f in run.files]
            if run.files is not None
            else list(_DEFAULT_FILES)
        )
        for relpath, content in files:
            target = run_dir / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
