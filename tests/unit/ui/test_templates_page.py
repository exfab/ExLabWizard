"""Tests for the template-manager page helpers.

``render_template_manager`` renders NiceGUI widgets, so the unit-testable
surface is the two pure functions the wizards depend on:
:func:`list_templates` (scan a directory for Copier templates) and
:func:`create_template` (scaffold a new minimal Copier template). The
most important assertion here is that a scaffolded template is a *valid*
template -- i.e. the real :class:`TemplateEngine` resolves it without
raising.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# Prime the api package before importing ui.pages so the pre-existing
# orchestrator <-> api import order resolves cleanly (see test_mount.py).
import exlab_wizard.api.app  # noqa: F401  -- import order matters
from exlab_wizard.constants import COPIER_MANIFEST_NAME, RunScope, TemplateType
from exlab_wizard.template.copier_driver import TemplateEngine
from exlab_wizard.ui.pages.templates import (
    TemplateSummary,
    create_template,
    list_templates,
)

# ---------------------------------------------------------------------------
# create_template -- scaffolding
# ---------------------------------------------------------------------------


def test_create_template_scaffolds_dir_with_manifest_and_content(
    tmp_path: Path,
) -> None:
    root = create_template(tmp_path, name="my-project", template_type="project")

    assert root == tmp_path / "my-project"
    assert root.is_dir()
    manifest = root / COPIER_MANIFEST_NAME
    assert manifest.is_file()
    # Exactly one content file is scaffolded alongside copier.yml.
    content_files = [p for p in root.iterdir() if p.name != COPIER_MANIFEST_NAME]
    assert len(content_files) == 1
    assert content_files[0].read_text(encoding="utf-8")


def test_create_template_strips_whitespace_from_name(tmp_path: Path) -> None:
    root = create_template(tmp_path, name="  spaced  ", template_type="project")
    assert root.name == "spaced"


def test_create_template_writes_description_into_manifest(
    tmp_path: Path,
) -> None:
    root = create_template(
        tmp_path,
        name="described",
        template_type="project",
        description="  a microscopy layout  ",
    )
    data = yaml.safe_load((root / COPIER_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert data["_exlab_type"] == TemplateType.PROJECT.value
    assert data["_exlab_description"] == "a microscopy layout"


def test_create_run_template_records_run_scope(tmp_path: Path) -> None:
    root = create_template(
        tmp_path,
        name="run-tpl",
        template_type="run",
        run_scope=RunScope.EXPERIMENTAL.value,
    )
    data = yaml.safe_load((root / COPIER_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert data["_exlab_type"] == TemplateType.RUN.value
    assert data["_exlab_run_scope"] == RunScope.EXPERIMENTAL.value


def test_create_project_template_omits_run_scope_key(tmp_path: Path) -> None:
    root = create_template(tmp_path, name="proj", template_type="project")
    data = yaml.safe_load((root / COPIER_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert "_exlab_run_scope" not in data


# ---------------------------------------------------------------------------
# create_template -- validation
# ---------------------------------------------------------------------------


def test_create_template_rejects_empty_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty"):
        create_template(tmp_path, name="   ", template_type="project")


def test_create_template_rejects_unknown_type(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown template type"):
        create_template(tmp_path, name="x", template_type="bogus")


def test_create_run_template_requires_run_scope(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="run_scope"):
        create_template(tmp_path, name="x", template_type="run")


def test_create_run_template_rejects_invalid_run_scope(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="run_scope"):
        create_template(tmp_path, name="x", template_type="run", run_scope="nonsense")


def test_create_template_rejects_duplicate_name(tmp_path: Path) -> None:
    create_template(tmp_path, name="dup", template_type="project")
    with pytest.raises(ValueError, match="already exists"):
        create_template(tmp_path, name="dup", template_type="project")


# ---------------------------------------------------------------------------
# create_template -- the scaffold is a VALID template
# ---------------------------------------------------------------------------


def test_scaffolded_project_template_resolves_with_engine(
    tmp_path: Path,
) -> None:
    root = create_template(
        tmp_path,
        name="valid-project",
        template_type="project",
        description="resolvable",
    )
    resolved = TemplateEngine().resolve(root, TemplateType.PROJECT)
    assert resolved.exlab_type is TemplateType.PROJECT
    assert resolved.run_scope is None
    assert resolved.exlab_version
    assert resolved.description == "resolvable"


def test_scaffolded_run_template_resolves_with_engine(tmp_path: Path) -> None:
    root = create_template(
        tmp_path,
        name="valid-run",
        template_type="run",
        run_scope=RunScope.TEST.value,
    )
    resolved = TemplateEngine().resolve(root, TemplateType.RUN)
    assert resolved.exlab_type is TemplateType.RUN
    assert resolved.run_scope is RunScope.TEST


# ---------------------------------------------------------------------------
# list_templates
# ---------------------------------------------------------------------------


def test_list_templates_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    assert list_templates(tmp_path / "does-not-exist") == []


def test_list_templates_returns_empty_for_dir_with_no_templates(
    tmp_path: Path,
) -> None:
    assert list_templates(tmp_path) == []


def test_list_templates_summarizes_each_template(tmp_path: Path) -> None:
    create_template(tmp_path, name="alpha", template_type="project", description="A")
    create_template(
        tmp_path,
        name="beta",
        template_type="run",
        run_scope=RunScope.BOTH.value,
        description="B",
    )

    summaries = list_templates(tmp_path)

    assert [s.name for s in summaries] == ["alpha", "beta"]
    assert all(isinstance(s, TemplateSummary) for s in summaries)
    by_name = {s.name: s for s in summaries}
    assert by_name["alpha"].template_type == TemplateType.PROJECT.value
    assert by_name["alpha"].run_scope is None
    assert by_name["alpha"].description == "A"
    assert by_name["beta"].template_type == TemplateType.RUN.value
    assert by_name["beta"].run_scope == RunScope.BOTH.value
    assert by_name["beta"].path == tmp_path / "beta"


def test_list_templates_is_sorted(tmp_path: Path) -> None:
    for name in ("zulu", "mike", "alpha"):
        create_template(tmp_path, name=name, template_type="project")
    assert [s.name for s in list_templates(tmp_path)] == [
        "alpha",
        "mike",
        "zulu",
    ]


def test_list_templates_skips_dirs_without_manifest(tmp_path: Path) -> None:
    create_template(tmp_path, name="real", template_type="project")
    (tmp_path / "not-a-template").mkdir()
    (tmp_path / "not-a-template" / "readme.txt").write_text("hi", encoding="utf-8")

    summaries = list_templates(tmp_path)

    assert [s.name for s in summaries] == ["real"]


def test_list_templates_filters_by_template_type(tmp_path: Path) -> None:
    create_template(tmp_path, name="proj", template_type="project")
    create_template(
        tmp_path,
        name="run",
        template_type="run",
        run_scope=RunScope.EXPERIMENTAL.value,
    )

    only_runs = list_templates(tmp_path, template_type=TemplateType.RUN.value)
    only_projects = list_templates(tmp_path, template_type=TemplateType.PROJECT.value)

    assert [s.name for s in only_runs] == ["run"]
    assert [s.name for s in only_projects] == ["proj"]


def test_list_templates_skips_malformed_manifest(tmp_path: Path) -> None:
    create_template(tmp_path, name="good", template_type="project")
    bad = tmp_path / "bad"
    bad.mkdir()
    # Unparseable YAML -- must be skipped, not raised.
    (bad / COPIER_MANIFEST_NAME).write_text("key: [unclosed", encoding="utf-8")

    summaries = list_templates(tmp_path)

    assert [s.name for s in summaries] == ["good"]


def test_list_templates_skips_non_mapping_manifest(tmp_path: Path) -> None:
    create_template(tmp_path, name="good", template_type="project")
    scalar = tmp_path / "scalar"
    scalar.mkdir()
    # Valid YAML, but not a mapping -- skipped.
    (scalar / COPIER_MANIFEST_NAME).write_text("just a string", encoding="utf-8")

    summaries = list_templates(tmp_path)

    assert [s.name for s in summaries] == ["good"]
