"""Unit tests for ``exlab_wizard.template.copier_driver``.

These tests pin the §4.4.2 / §5 contract of :class:`TemplateEngine`:

* ``resolve`` reads a template's ``copier.yml`` via :func:`yaml.safe_load`,
  validates its ``_exlab_*`` metadata, and either returns a fully-typed
  :class:`ResolvedTemplate` or raises a :class:`TemplateLoadError` /
  :class:`TemplateCoreFieldRedeclaredError` per spec.
* ``render`` calls :func:`copier.run_copy` with ``unsafe=False`` (so any
  ``_tasks`` are silently ignored per §5.5) and returns the list of files
  Copier wrote, computed by snapshotting the destination before/after.

The test fixtures live under ``tests/fixtures/templates/`` -- one
directory per scenario referenced by the tests below.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from copier.errors import CopierError

from exlab_wizard.constants import TemplateType
from exlab_wizard.errors import (
    TemplateCoreFieldRedeclaredError,
    TemplateLoadError,
)
from exlab_wizard.template.copier_driver import (
    CORE_README_FIELD_IDS,
    RenderResult,
    ResolvedTemplate,
    TemplateEngine,
)

# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

FIXTURES_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "templates"

PROJECT_BASIC = FIXTURES_ROOT / "project_basic"
RUN_BASIC_EXPERIMENTAL = FIXTURES_ROOT / "run_basic_experimental"
RUN_BASIC_TEST = FIXTURES_ROOT / "run_basic_test"
RUN_BASIC_BOTH = FIXTURES_ROOT / "run_basic_both"
MISSING_VERSION = FIXTURES_ROOT / "missing_version"
WITH_TASKS = FIXTURES_ROOT / "with_tasks"
REDECLARES_CORE = FIXTURES_ROOT / "redeclares_core"


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


def test_core_readme_field_ids_pins_the_three_core_fields() -> None:
    # §10.3 -- label / operator / objective are the backend-managed core
    # fields. Templates may NOT redeclare them under _exlab_readme.fields.
    assert frozenset({"label", "operator", "objective"}) == CORE_README_FIELD_IDS


# ---------------------------------------------------------------------------
# resolve(): happy-path metadata
# ---------------------------------------------------------------------------


def test_resolve_project_basic_returns_resolved_template() -> None:
    engine = TemplateEngine()
    tpl = engine.resolve(PROJECT_BASIC, TemplateType.PROJECT)

    assert isinstance(tpl, ResolvedTemplate)
    assert tpl.name == "project_basic"
    assert tpl.path == PROJECT_BASIC
    assert tpl.exlab_type == "project"
    assert tpl.exlab_version == "0.1.0"
    # Project templates carry no run scope.
    assert tpl.run_scope is None
    assert tpl.description == "Basic project template for tests"
    assert tpl.plugin_order == []
    assert tpl.extra_readme_fields == []
    # The raw manifest is preserved so downstream code can read questions.
    assert tpl.raw_manifest["_exlab_type"] == "project"
    assert tpl.raw_manifest["_exlab_version"] == "0.1.0"
    assert "_exlab_proj" in tpl.raw_manifest


def test_resolve_run_basic_experimental_sets_run_scope_experimental() -> None:
    engine = TemplateEngine()
    tpl = engine.resolve(RUN_BASIC_EXPERIMENTAL, TemplateType.RUN)

    assert tpl.exlab_type == "run"
    assert tpl.exlab_version == "1.0"
    assert tpl.run_scope == "experimental"
    assert tpl.description == "Experimental-only run template"


def test_resolve_run_basic_test_sets_run_scope_test() -> None:
    engine = TemplateEngine()
    tpl = engine.resolve(RUN_BASIC_TEST, TemplateType.RUN)

    assert tpl.exlab_type == "run"
    assert tpl.run_scope == "test"


def test_resolve_run_basic_both_sets_run_scope_both() -> None:
    engine = TemplateEngine()
    tpl = engine.resolve(RUN_BASIC_BOTH, TemplateType.RUN)

    assert tpl.exlab_type == "run"
    assert tpl.run_scope == "both"


# ---------------------------------------------------------------------------
# resolve(): error cases
# ---------------------------------------------------------------------------


def test_resolve_missing_version_raises_template_load_error_mentioning_field(
    tmp_path: Path,
) -> None:
    engine = TemplateEngine()
    with pytest.raises(TemplateLoadError) as info:
        engine.resolve(MISSING_VERSION, TemplateType.PROJECT)
    assert "_exlab_version" in str(info.value)


def test_resolve_redeclares_core_raises_core_field_redeclared_naming_offender() -> None:
    engine = TemplateEngine()
    with pytest.raises(TemplateCoreFieldRedeclaredError) as info:
        engine.resolve(REDECLARES_CORE, TemplateType.PROJECT)
    # The error names the offending field id so the lint output is useful.
    assert "label" in str(info.value)


def test_resolve_redeclares_core_is_also_caught_as_template_load_error() -> None:
    # TemplateCoreFieldRedeclaredError is a subclass of TemplateLoadError
    # (Backend Spec §10.3 -- catching the parent must catch it too).
    engine = TemplateEngine()
    with pytest.raises(TemplateLoadError):
        engine.resolve(REDECLARES_CORE, TemplateType.PROJECT)


def test_resolve_missing_copier_yml_raises_template_load_error(tmp_path: Path) -> None:
    # An empty directory has no copier.yml; resolve must reject it cleanly.
    engine = TemplateEngine()
    empty_template = tmp_path / "empty_template"
    empty_template.mkdir()
    with pytest.raises(TemplateLoadError) as info:
        engine.resolve(empty_template, TemplateType.PROJECT)
    assert "copier.yml" in str(info.value)


def test_resolve_scope_mismatch_raises_template_load_error() -> None:
    # copier.yml says _exlab_type: "run" but the caller asked for "project".
    engine = TemplateEngine()
    with pytest.raises(TemplateLoadError) as info:
        engine.resolve(RUN_BASIC_EXPERIMENTAL, TemplateType.PROJECT)
    msg = str(info.value)
    assert "_exlab_type" in msg or "scope" in msg


def test_resolve_run_template_missing_run_scope_raises(tmp_path: Path) -> None:
    # Hand-craft a run template whose copier.yml omits _exlab_run_scope.
    template_dir = tmp_path / "run_no_scope"
    template_dir.mkdir()
    (template_dir / "copier.yml").write_text(
        '_exlab_type: "run"\n_exlab_version: "1.0"\n',
        encoding="utf-8",
    )
    engine = TemplateEngine()
    with pytest.raises(TemplateLoadError) as info:
        engine.resolve(template_dir, TemplateType.RUN)
    assert "_exlab_run_scope" in str(info.value)


def test_resolve_run_template_invalid_run_scope_raises(tmp_path: Path) -> None:
    # _exlab_run_scope must be one of {experimental, test, both}.
    template_dir = tmp_path / "run_bad_scope"
    template_dir.mkdir()
    (template_dir / "copier.yml").write_text(
        '_exlab_type: "run"\n_exlab_version: "1.0"\n_exlab_run_scope: "invalid"\n',
        encoding="utf-8",
    )
    engine = TemplateEngine()
    with pytest.raises(TemplateLoadError) as info:
        engine.resolve(template_dir, TemplateType.RUN)
    assert "_exlab_run_scope" in str(info.value)


# ---------------------------------------------------------------------------
# resolve(): _tasks is silently ignored but warned about
# ---------------------------------------------------------------------------


def test_resolve_with_tasks_does_not_raise() -> None:
    # §5.5: _tasks is silently ignored (Copier is invoked unsafe=False).
    engine = TemplateEngine()
    tpl = engine.resolve(WITH_TASKS, TemplateType.PROJECT)
    assert tpl.exlab_type == "project"
    # The raw manifest still preserves the _tasks key so callers can
    # introspect it (e.g. the lint command flags it at WARN per §5.5).
    assert "_tasks" in tpl.raw_manifest


def test_resolve_with_tasks_emits_warning_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = TemplateEngine()
    with caplog.at_level(logging.WARNING, logger="exlab_wizard.template.copier_driver"):
        engine.resolve(WITH_TASKS, TemplateType.PROJECT)
    # We're checking that *some* WARNING log was emitted referencing
    # _tasks, without pinning the exact wording.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("_tasks" in r.getMessage() for r in warnings), (
        f"expected a WARNING mentioning _tasks, got {[r.getMessage() for r in warnings]}"
    )


# ---------------------------------------------------------------------------
# render(): writes files; reports them in RenderResult.files_written
# ---------------------------------------------------------------------------


async def test_render_project_basic_writes_readme(tmp_path: Path) -> None:
    engine = TemplateEngine()
    tpl = engine.resolve(PROJECT_BASIC, TemplateType.PROJECT)
    dst = tmp_path / "out"

    variables: dict[str, Any] = {"_exlab_proj": "MyProj"}
    result = await engine.render(tpl, dst, variables)

    assert isinstance(result, RenderResult)
    assert result.dst_path == dst

    # The templated folder name resolves to "MyProj" and the README is
    # rendered with the variable interpolated.
    rendered_readme = dst / "MyProj" / "README.md"
    assert rendered_readme.is_file()
    assert "MyProj" in rendered_readme.read_text(encoding="utf-8")


async def test_render_project_basic_files_written_lists_the_readme(
    tmp_path: Path,
) -> None:
    engine = TemplateEngine()
    tpl = engine.resolve(PROJECT_BASIC, TemplateType.PROJECT)
    dst = tmp_path / "out"

    result = await engine.render(tpl, dst, {"_exlab_proj": "Proj"})

    rendered_readme = (dst / "Proj" / "README.md").resolve()
    # files_written is the absolute path-set Copier produced. The README
    # is the only Jinja-templated file in this template, but Copier also
    # writes a .exlab-answers.yml because _answers_file is configured;
    # we only assert the README is present (not exact equality), so the
    # test stays robust if Copier internals change.
    assert rendered_readme in {p.resolve() for p in result.files_written}


async def test_render_with_tasks_silently_ignores_tasks_and_writes_file(
    tmp_path: Path,
) -> None:
    # The with_tasks fixture has _tasks declared and a single .jinja file.
    # We can't directly observe a NOT-executed shell command, but we can
    # verify that:
    #   1. render returns successfully (no UnsafeTemplateError),
    #   2. the .jinja file is rendered,
    #   3. no extra side-effect file from the _tasks command appears.
    engine = TemplateEngine()
    tpl = engine.resolve(WITH_TASKS, TemplateType.PROJECT)
    dst = tmp_path / "out"

    result = await engine.render(tpl, dst, {"dummy_var": "world"})

    rendered = dst / "file.txt"
    assert rendered.is_file()
    assert rendered.read_text(encoding="utf-8").strip() == "hello world"
    # The dst should contain only the rendered file (and Copier's
    # answers file, possibly); the tasks command, if it had run, would
    # have produced no observable file but also wouldn't crash. So this
    # test mostly pins "did not raise".
    assert isinstance(result, RenderResult)


async def test_render_into_dst_with_conflicting_files_raises(
    tmp_path: Path,
) -> None:
    # overwrite=False is the wired-in default per §4.4.2. When a file
    # already on disk would be overwritten by the render with different
    # content, Copier MUST raise rather than silently overwriting. In
    # the pytest non-TTY context, Copier surfaces this as
    # ``InteractiveSessionError`` (a subclass of ``UserMessageError``,
    # itself a ``CopierError``).
    engine = TemplateEngine()
    tpl = engine.resolve(PROJECT_BASIC, TemplateType.PROJECT)
    dst = tmp_path / "out"

    # First render -- creates dst/First/README.md containing "# First".
    await engine.render(tpl, dst, {"_exlab_proj": "First"})

    # Mutate the rendered file so the second render's output differs
    # from what is on disk and Copier cannot decide non-interactively.
    readme = dst / "First" / "README.md"
    readme.write_text("conflicting content\n", encoding="utf-8")

    # Second render against the same dst -- now an actual conflict.
    # Copier raises ``InteractiveSessionError`` (a ``CopierError``
    # descendant) when ``overwrite=False`` and a pre-existing file's
    # content differs from what the template would produce.
    with pytest.raises(CopierError):
        await engine.render(tpl, dst, {"_exlab_proj": "First"})
