"""Tests for the read-side template resolver (Backend Spec §5.0).

:func:`resolve_template_chain` merges a per-project -> per-equipment ->
global search chain, nearest scope winning on a name collision, optionally
narrowing run templates by scope. These tests build real ``copier.yml``
template stores on disk (the same minimal ``_exlab_*`` manifest shape the
template-manager page emits) and assert the merge precedence, the
type-specific search chains, run-scope narrowing, and graceful handling of
missing per-instance directories.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# Prime the api package before importing ui.pages so the pre-existing
# orchestrator <-> api import order resolves cleanly (see test_templates_page).
import exlab_wizard.api.app  # noqa: F401  -- import order matters
from exlab_wizard.config.models import Config, EquipmentConfig, PathsConfig
from exlab_wizard.constants import COPIER_MANIFEST_NAME, RunScope, TemplateType
from exlab_wizard.paths import cache_dir
from exlab_wizard.template.resolution import (
    TemplateChoices,
    instance_template_dir,
    reconcile_selection,
    resolve_template_chain,
    search_dirs,
)

EQUIPMENT_ID = "MICROSCOPE_01"


# ---------------------------------------------------------------------------
# reconcile_selection -- post-re-resolution path refresh (Phase 5)
# ---------------------------------------------------------------------------


def test_reconcile_selection_refreshes_path_for_same_name_override() -> None:
    """A surviving same-named selection re-derives its (possibly new) path.

    The regression guard for the per-equipment-override bug: the operator
    picked ``lab_default`` at the global path, then chose an equipment whose
    own ``lab_default`` shadows it at a different path. The select keeps the
    name, so the stored path must be refreshed to the per-equipment one --
    otherwise submit would render the global template.
    """
    global_path = Path("/global/lab_default")
    per_eq_path = Path("/local/EQ/.exlab-wizard/templates/project/lab_default")
    # Selection was made under the global context...
    assert global_path != per_eq_path
    # ...now re-resolved with the per-equipment override at a new path.
    new_choices = TemplateChoices(names=["lab_default"], paths={"lab_default": per_eq_path})
    name, path, dropped = reconcile_selection(new_choices, "lab_default")
    assert name == "lab_default"
    assert path == per_eq_path
    assert dropped is False


def test_reconcile_selection_drops_vanished_name() -> None:
    """A selection whose name no longer appears is dropped (clears variables)."""
    new_choices = TemplateChoices(names=["other"], paths={"other": Path("/x/other")})
    name, path, dropped = reconcile_selection(new_choices, "gone")
    assert name is None
    assert path is None
    assert dropped is True


def test_reconcile_selection_none_selection_is_dropped() -> None:
    """No prior selection reconciles to a cleared, dropped state."""
    name, path, dropped = reconcile_selection(TemplateChoices(), None)
    assert (name, path, dropped) == (None, None, True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_template(
    parent: Path,
    *,
    name: str,
    template_type: str,
    run_scope: str | None = None,
    description: str = "",
) -> Path:
    """Write a minimal ``copier.yml`` template under ``parent/<name>``."""
    root = parent / name
    root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {
        "_exlab_type": template_type,
        "_exlab_version": "1.0",
        "_exlab_description": description,
    }
    if run_scope is not None:
        manifest["_exlab_run_scope"] = run_scope
    (root / COPIER_MANIFEST_NAME).write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    return root


def _build_config(tmp_path: Path) -> Config:
    """A config whose global templates dir + local_root live under ``tmp_path``."""
    return Config(
        paths=PathsConfig(
            templates_dir=str(tmp_path / "global-templates"),
            plugin_dir=str(tmp_path / "plugins"),
            local_root=str(tmp_path / "local"),
        ),
        equipment=[
            EquipmentConfig(
                id=EQUIPMENT_ID,
                label="Microscope 01",
                local_root=str(tmp_path / "local"),
                nas_root="nas-root",
            )
        ],
    )


def _project_path(tmp_path: Path) -> Path:
    """The absolute project dir under ``local_root/<equipment_id>/``."""
    return tmp_path / "local" / EQUIPMENT_ID / "ProjectA"


# ---------------------------------------------------------------------------
# instance_template_dir
# ---------------------------------------------------------------------------


def test_instance_template_dir_composes_cache_templates_type(tmp_path: Path) -> None:
    result = instance_template_dir(tmp_path, TemplateType.RUN.value)
    assert result == cache_dir(tmp_path) / "templates" / "run"


# ---------------------------------------------------------------------------
# search_dirs -- which scopes each template type searches
# ---------------------------------------------------------------------------


def test_search_dirs_run_orders_project_equipment_global(tmp_path: Path) -> None:
    config = _build_config(tmp_path)
    project = _project_path(tmp_path)
    dirs = search_dirs(
        config,
        template_type=TemplateType.RUN.value,
        equipment_id=EQUIPMENT_ID,
        project_path=project,
    )
    assert dirs == [
        instance_template_dir(project, TemplateType.RUN.value),
        instance_template_dir(tmp_path / "local" / EQUIPMENT_ID, TemplateType.RUN.value),
        Path(config.paths.templates_dir),
    ]


def test_search_dirs_project_orders_equipment_global(tmp_path: Path) -> None:
    config = _build_config(tmp_path)
    dirs = search_dirs(
        config,
        template_type=TemplateType.PROJECT.value,
        equipment_id=EQUIPMENT_ID,
    )
    assert dirs == [
        instance_template_dir(tmp_path / "local" / EQUIPMENT_ID, TemplateType.PROJECT.value),
        Path(config.paths.templates_dir),
    ]


def test_search_dirs_equipment_is_global_only(tmp_path: Path) -> None:
    config = _build_config(tmp_path)
    dirs = search_dirs(
        config,
        template_type=TemplateType.EQUIPMENT.value,
        equipment_id=EQUIPMENT_ID,
    )
    assert dirs == [Path(config.paths.templates_dir)]


def test_search_dirs_skips_empty_global(tmp_path: Path) -> None:
    config = _build_config(tmp_path)
    config = config.model_copy(
        update={"paths": config.paths.model_copy(update={"templates_dir": ""})}
    )
    dirs = search_dirs(
        config,
        template_type=TemplateType.PROJECT.value,
        equipment_id=EQUIPMENT_ID,
    )
    # Only the per-equipment dir survives; the empty global dir is dropped.
    assert dirs == [
        instance_template_dir(tmp_path / "local" / EQUIPMENT_ID, TemplateType.PROJECT.value)
    ]


def test_search_dirs_skips_equipment_when_id_missing(tmp_path: Path) -> None:
    config = _build_config(tmp_path)
    dirs = search_dirs(config, template_type=TemplateType.PROJECT.value, equipment_id=None)
    assert dirs == [Path(config.paths.templates_dir)]


# ---------------------------------------------------------------------------
# resolve_template_chain -- merge precedence
# ---------------------------------------------------------------------------


def test_run_chain_nearest_scope_wins_on_name_collision(tmp_path: Path) -> None:
    config = _build_config(tmp_path)
    project = _project_path(tmp_path)
    global_dir = Path(config.paths.templates_dir)
    equip_run_dir = instance_template_dir(tmp_path / "local" / EQUIPMENT_ID, TemplateType.RUN.value)
    project_run_dir = instance_template_dir(project, TemplateType.RUN.value)

    # Same name "shared" in all three scopes; distinguish by description.
    _write_template(
        global_dir,
        name="shared",
        template_type="run",
        run_scope=RunScope.BOTH.value,
        description="global",
    )
    _write_template(
        equip_run_dir,
        name="shared",
        template_type="run",
        run_scope=RunScope.BOTH.value,
        description="equipment",
    )
    _write_template(
        project_run_dir,
        name="shared",
        template_type="run",
        run_scope=RunScope.BOTH.value,
        description="project",
    )

    result = resolve_template_chain(
        config,
        template_type=TemplateType.RUN.value,
        equipment_id=EQUIPMENT_ID,
        project_path=project,
    )

    names = [s.name for s in result]
    assert names == ["shared"]
    # Nearest scope (project) wins.
    assert result[0].description == "project"


def test_run_chain_distinct_names_ordered_project_equipment_global(tmp_path: Path) -> None:
    config = _build_config(tmp_path)
    project = _project_path(tmp_path)
    global_dir = Path(config.paths.templates_dir)
    equip_run_dir = instance_template_dir(tmp_path / "local" / EQUIPMENT_ID, TemplateType.RUN.value)
    project_run_dir = instance_template_dir(project, TemplateType.RUN.value)

    _write_template(global_dir, name="g-run", template_type="run", run_scope=RunScope.BOTH.value)
    _write_template(equip_run_dir, name="e-run", template_type="run", run_scope=RunScope.BOTH.value)
    _write_template(
        project_run_dir, name="p-run", template_type="run", run_scope=RunScope.BOTH.value
    )

    result = resolve_template_chain(
        config,
        template_type=TemplateType.RUN.value,
        equipment_id=EQUIPMENT_ID,
        project_path=project,
    )

    assert [s.name for s in result] == ["p-run", "e-run", "g-run"]


def test_project_chain_equipment_beats_global_on_collision(tmp_path: Path) -> None:
    config = _build_config(tmp_path)
    global_dir = Path(config.paths.templates_dir)
    equip_proj_dir = instance_template_dir(
        tmp_path / "local" / EQUIPMENT_ID, TemplateType.PROJECT.value
    )

    _write_template(global_dir, name="layout", template_type="project", description="global")
    _write_template(equip_proj_dir, name="layout", template_type="project", description="equipment")
    # A distinct global-only project template still surfaces.
    _write_template(global_dir, name="global-only", template_type="project")

    result = resolve_template_chain(
        config,
        template_type=TemplateType.PROJECT.value,
        equipment_id=EQUIPMENT_ID,
    )

    by_name = {s.name: s for s in result}
    assert by_name["layout"].description == "equipment"
    assert "global-only" in by_name
    # Equipment scope first, then the global-only distinct name.
    assert [s.name for s in result] == ["layout", "global-only"]


def test_equipment_chain_searches_only_global(tmp_path: Path) -> None:
    config = _build_config(tmp_path)
    global_dir = Path(config.paths.templates_dir)
    # A per-equipment "equipment"-type store should be ignored (never searched).
    equip_equip_dir = instance_template_dir(
        tmp_path / "local" / EQUIPMENT_ID, TemplateType.EQUIPMENT.value
    )
    _write_template(global_dir, name="rig", template_type="equipment")
    _write_template(equip_equip_dir, name="ignored", template_type="equipment")

    result = resolve_template_chain(
        config,
        template_type=TemplateType.EQUIPMENT.value,
        equipment_id=EQUIPMENT_ID,
    )

    assert [s.name for s in result] == ["rig"]


# ---------------------------------------------------------------------------
# resolve_template_chain -- run-scope narrowing
# ---------------------------------------------------------------------------


def test_run_scope_excludes_test_when_experimental_requested(tmp_path: Path) -> None:
    config = _build_config(tmp_path)
    global_dir = Path(config.paths.templates_dir)
    _write_template(
        global_dir, name="exp-only", template_type="run", run_scope=RunScope.EXPERIMENTAL.value
    )
    _write_template(
        global_dir, name="test-only", template_type="run", run_scope=RunScope.TEST.value
    )
    _write_template(global_dir, name="both", template_type="run", run_scope=RunScope.BOTH.value)

    result = resolve_template_chain(
        config,
        template_type=TemplateType.RUN.value,
        run_scope=RunScope.EXPERIMENTAL.value,
    )

    names = {s.name for s in result}
    assert names == {"exp-only", "both"}
    assert "test-only" not in names


def test_run_scope_test_keeps_test_and_both(tmp_path: Path) -> None:
    config = _build_config(tmp_path)
    global_dir = Path(config.paths.templates_dir)
    _write_template(
        global_dir, name="exp-only", template_type="run", run_scope=RunScope.EXPERIMENTAL.value
    )
    _write_template(
        global_dir, name="test-only", template_type="run", run_scope=RunScope.TEST.value
    )
    _write_template(global_dir, name="both", template_type="run", run_scope=RunScope.BOTH.value)

    result = resolve_template_chain(
        config,
        template_type=TemplateType.RUN.value,
        run_scope=RunScope.TEST.value,
    )

    assert {s.name for s in result} == {"test-only", "both"}


def test_run_scope_none_keeps_all_run_templates(tmp_path: Path) -> None:
    config = _build_config(tmp_path)
    global_dir = Path(config.paths.templates_dir)
    _write_template(
        global_dir, name="exp-only", template_type="run", run_scope=RunScope.EXPERIMENTAL.value
    )
    _write_template(
        global_dir, name="test-only", template_type="run", run_scope=RunScope.TEST.value
    )

    result = resolve_template_chain(config, template_type=TemplateType.RUN.value)

    assert {s.name for s in result} == {"exp-only", "test-only"}


# ---------------------------------------------------------------------------
# resolve_template_chain -- missing dirs are skipped gracefully
# ---------------------------------------------------------------------------


def test_missing_per_instance_dirs_are_skipped(tmp_path: Path) -> None:
    config = _build_config(tmp_path)
    project = _project_path(tmp_path)
    global_dir = Path(config.paths.templates_dir)
    # Only the global dir has templates; no per-equipment / per-project folders.
    _write_template(global_dir, name="g-run", template_type="run", run_scope=RunScope.BOTH.value)

    result = resolve_template_chain(
        config,
        template_type=TemplateType.RUN.value,
        equipment_id=EQUIPMENT_ID,
        project_path=project,
    )

    assert [s.name for s in result] == ["g-run"]


def test_no_dirs_exist_returns_empty(tmp_path: Path) -> None:
    config = _build_config(tmp_path)
    result = resolve_template_chain(
        config,
        template_type=TemplateType.PROJECT.value,
        equipment_id=EQUIPMENT_ID,
    )
    assert result == []


def test_global_misfiled_type_is_filtered_out(tmp_path: Path) -> None:
    """A project template in the flat global dir is skipped for a run search."""
    config = _build_config(tmp_path)
    global_dir = Path(config.paths.templates_dir)
    _write_template(global_dir, name="a-project", template_type="project")
    _write_template(global_dir, name="a-run", template_type="run", run_scope=RunScope.BOTH.value)

    result = resolve_template_chain(config, template_type=TemplateType.RUN.value)

    assert [s.name for s in result] == ["a-run"]
