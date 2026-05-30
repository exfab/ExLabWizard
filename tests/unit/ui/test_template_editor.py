"""Tests for the template-editor page (headless payload contract).

``render_template_editor`` builds NiceGUI widgets when NiceGUI is
importable, but -- like ``render_template_manager`` -- falls back to a
plain payload dict when the ``from nicegui import ui`` inside the function
raises. That headless payload carries the data-shaping logic, so these
tests force the fallback (``sys.modules["nicegui"] = None``) and assert on
the dict: the template's files, question keys, exlab_type, run_scope, and
the lint finding codes for the directory. A companion test exercises the
new manager parameters (``on_edit`` / ``locations``) through the same
headless path.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Prime the api package before importing ui.pages so the pre-existing
# orchestrator <-> api import order resolves cleanly (see test_mount.py).
import exlab_wizard.api.app  # noqa: F401  -- import order matters
from exlab_wizard.constants import COPIER_MANIFEST_NAME, RunScope
from exlab_wizard.template.authoring import create_template_dir, write_content_file
from exlab_wizard.ui.pages.template_editor import render_template_editor
from exlab_wizard.ui.pages.templates import TemplateSummary, render_template_manager


@pytest.fixture
def _headless(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force the ``from nicegui import ui`` inside the views to fail.

    Setting ``sys.modules["nicegui"]`` to ``None`` makes the import raise
    ``ImportError``, so the render functions return their headless payload
    dict regardless of whether NiceGUI is installed in the test env.
    """
    monkeypatch.setitem(sys.modules, "nicegui", None)
    yield


# ---------------------------------------------------------------------------
# render_template_editor -- headless payload
# ---------------------------------------------------------------------------


def test_editor_payload_lists_files_and_metadata(_headless: None, tmp_path: Path) -> None:
    root = create_template_dir(
        tmp_path,
        name="proj",
        template_type="project",
        description="layout",
    )

    payload = render_template_editor(template_dir=root)

    assert isinstance(payload, dict)
    assert payload["template"] == "proj"
    assert payload["exlab_type"] == "project"
    assert payload["run_scope"] is None
    # copier.yml + the scaffolded notes.md.jinja are listed.
    assert COPIER_MANIFEST_NAME in payload["files"]
    assert any(rel.endswith(".jinja") for rel in payload["files"])
    # A clean scaffold has no jinja-syntax ERROR findings.
    assert "jinja_syntax_error" not in payload["findings"]


def test_editor_payload_reports_run_scope(_headless: None, tmp_path: Path) -> None:
    root = create_template_dir(
        tmp_path,
        name="runtpl",
        template_type="run",
        run_scope=RunScope.TEST.value,
    )

    payload = render_template_editor(template_dir=root)

    assert payload["exlab_type"] == "run"
    assert payload["run_scope"] == RunScope.TEST.value


def test_editor_payload_lists_question_keys(_headless: None, tmp_path: Path) -> None:
    root = create_template_dir(tmp_path, name="q", template_type="project")
    # Append a question to the scaffolded manifest.
    manifest_path = root / COPIER_MANIFEST_NAME
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8")
        + "\nspecimen_id:\n  type: str\n  help: Specimen identifier\n",
        encoding="utf-8",
    )

    payload = render_template_editor(template_dir=root)

    assert "specimen_id" in payload["questions"]


def test_editor_payload_surfaces_jinja_syntax_error(_headless: None, tmp_path: Path) -> None:
    root = create_template_dir(tmp_path, name="broken", template_type="project")
    # Write a syntactically broken Jinja file directly (bypassing the
    # authoring Jinja gate, which would refuse it).
    (root / "broken.txt.jinja").write_text("{% if %}", encoding="utf-8")

    payload = render_template_editor(template_dir=root)

    assert "jinja_syntax_error" in payload["findings"]


def test_editor_callbacks_are_optional(_headless: None, tmp_path: Path) -> None:
    root = create_template_dir(tmp_path, name="nocallbacks", template_type="project")
    # All callbacks default to None -- the function must still return a
    # payload without raising.
    payload = render_template_editor(template_dir=root)
    assert payload["template"] == "nocallbacks"


def test_editor_payload_includes_uploaded_content_file(_headless: None, tmp_path: Path) -> None:
    root = create_template_dir(tmp_path, name="extra", template_type="project")
    write_content_file(root, "data/values.csv", "a,b\n1,2\n")

    payload = render_template_editor(template_dir=root)

    assert "data/values.csv" in payload["files"]


# ---------------------------------------------------------------------------
# render_template_manager -- new on_edit / locations params
# ---------------------------------------------------------------------------


def test_manager_payload_includes_new_and_old_keys(_headless: None, tmp_path: Path) -> None:
    summaries = [
        TemplateSummary(
            name="alpha",
            path=tmp_path / "alpha",
            template_type="project",
            run_scope=None,
            description="",
        )
    ]

    payload = render_template_manager(
        templates=summaries,
        on_edit=lambda _name: None,
        locations=[("Global", "global")],
        on_location_change=lambda _value: None,
    )

    assert isinstance(payload, dict)
    # New key present.
    assert payload["locations"] == [("Global", "global")]
    # Old keys still present (regression: test_templates_page.py contract).
    assert payload["templates"] == ["alpha"]
    assert payload["count"] == 1


def test_manager_payload_omits_locations_when_not_given(_headless: None) -> None:
    payload = render_template_manager(templates=[])
    assert "locations" not in payload
    assert payload["templates"] == []
    assert payload["count"] == 0
