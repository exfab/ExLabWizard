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
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

# Prime the api package before importing ui.pages so the pre-existing
# orchestrator <-> api import order resolves cleanly (see test_mount.py).
import exlab_wizard.api.app  # noqa: F401  -- import order matters
from exlab_wizard.constants import COPIER_MANIFEST_NAME, RunScope
from exlab_wizard.template.authoring import create_template_dir, write_content_file
from exlab_wizard.template.manifest import TemplateManifest
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


# ---------------------------------------------------------------------------
# render_template_editor -- NiceGUI render branch (fake ``ui`` surface)
# ---------------------------------------------------------------------------
#
# The headless payload tests above cover ``_build_payload``; the rich render
# branch (header / lint banner / file list + inline editor / question form /
# upload) only runs when ``from nicegui import ui`` succeeds. We inject a fake
# ``ui`` module that records created widgets and the ``on_click`` / ``on_upload``
# handlers keyed by their ``data-testid`` so the tests can both render the tree
# and *invoke* the action handlers (edit / save / delete / add-question /
# upload), mirroring the ``_Fluent`` pattern in ``test_mount.py``.


class _FakeWidget:
    """Chainable NiceGUI element stand-in that records props + handlers."""

    def __init__(self, registry: _Registry, kind: str, **attrs: Any) -> None:
        self._registry = registry
        self.kind = kind
        self.attrs = attrs
        self.value = attrs.get("value", "")
        self.testid: str | None = None
        self.on_click_handler: Callable[..., Any] | None = None
        self.deleted = False

    def props(self, spec: str = "", **_kw: Any) -> _FakeWidget:
        # Extract data-testid="..." so handlers can be looked up by it.
        marker = 'data-testid="'
        if marker in spec:
            start = spec.index(marker) + len(marker)
            self.testid = spec[start : spec.index('"', start)]
            self._registry.by_testid[self.testid] = self
        return self

    def style(self, *_a: Any, **_k: Any) -> _FakeWidget:
        return self

    def classes(self, *_a: Any, **_k: Any) -> _FakeWidget:
        return self

    def bind_value(self, *_a: Any, **_k: Any) -> _FakeWidget:
        return self

    def on_value_change(self, *_a: Any, **_k: Any) -> _FakeWidget:
        return self

    def delete(self) -> None:
        self.deleted = True

    def __enter__(self) -> _FakeWidget:
        return self

    def __exit__(self, *_a: Any) -> bool:
        return False


class _Registry:
    """Records every widget the fake ``ui`` creates, indexed by data-testid."""

    def __init__(self) -> None:
        self.widgets: list[_FakeWidget] = []
        self.by_testid: dict[str, _FakeWidget] = {}


class _FakeUI:
    """Minimal NiceGUI ``ui`` surface covering the editor's widget calls.

    Exposes ``codemirror`` and ``upload`` so the editor takes its primary
    branches (rich code editor + available upload widget).
    """

    def __init__(self, registry: _Registry) -> None:
        self._registry = registry

    def _make(self, kind: str, on_click: Callable[..., Any] | None = None, **attrs: Any) -> Any:
        w = _FakeWidget(self._registry, kind, **attrs)
        w.on_click_handler = on_click
        self._registry.widgets.append(w)
        return w

    def card(self, *_a: Any, **_k: Any) -> Any:
        return self._make("card")

    def column(self, *_a: Any, **_k: Any) -> Any:
        return self._make("column")

    def row(self, *_a: Any, **_k: Any) -> Any:
        return self._make("row")

    def label(self, text: str = "", *_a: Any, **_k: Any) -> Any:
        return self._make("label", text=text)

    def input(self, *_a: Any, **kw: Any) -> Any:
        return self._make("input", **kw)

    def textarea(self, *_a: Any, **kw: Any) -> Any:
        return self._make("textarea", **kw)

    def codemirror(self, *_a: Any, **kw: Any) -> Any:
        return self._make("codemirror", **kw)

    def select(self, *_a: Any, **kw: Any) -> Any:
        return self._make("select", **kw)

    def checkbox(self, *_a: Any, **kw: Any) -> Any:
        return self._make("checkbox", **kw)

    def button(
        self, _text: str = "", *, on_click: Callable[..., Any] | None = None, **kw: Any
    ) -> Any:
        return self._make("button", on_click=on_click, **kw)

    def upload(self, *, on_upload: Callable[..., Any] | None = None, **_kw: Any) -> Any:
        w = self._make("upload")
        w.on_click_handler = on_upload  # reuse the handler slot for the upload cb
        return w


@pytest.fixture
def _fake_ui(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Registry]:
    """Inject a fake ``nicegui`` module so the rich render branch executes."""
    import types

    registry = _Registry()
    fake_module = types.ModuleType("nicegui")
    fake_module.ui = _FakeUI(registry)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nicegui", fake_module)
    yield registry


def _click(registry: _Registry, testid: str, *args: Any) -> None:
    """Invoke the recorded ``on_click`` handler for a widget by data-testid.

    The editor's button handlers are ``lambda _evt, ...`` (NiceGUI passes a
    click event), so when the caller supplies no explicit args we pass a
    single ``None`` event. Callers that drive a real payload (e.g. the upload
    event) pass it explicitly.
    """
    widget = registry.by_testid[testid]
    assert widget.on_click_handler is not None, f"{testid} has no handler"
    widget.on_click_handler(*(args or (None,)))


def test_editor_render_builds_widget_tree(_fake_ui: _Registry, tmp_path: Path) -> None:
    """The NiceGUI branch renders header, file rows, question form, and upload."""
    root = create_template_dir(tmp_path, name="proj", template_type="project")
    write_content_file(root, "notes2.md", "# hi\n")

    card = render_template_editor(template_dir=root)

    assert card is not None  # returns the root card, not the headless dict
    ids = _fake_ui.by_testid
    assert "te-title" in ids
    assert "te-questions" in ids
    assert "te-save-questions" in ids
    assert "te-upload" in ids  # upload widget present (fake ui exposes it)
    # A per-file row + edit/delete controls for the editable content file.
    assert "te-file-notes2.md" in ids
    assert "te-edit-notes2.md" in ids
    assert "te-delete-notes2.md" in ids


def test_editor_lint_banner_renders_errors(_fake_ui: _Registry, tmp_path: Path) -> None:
    """A template with a broken .jinja renders the error lint banner branch."""
    root = create_template_dir(tmp_path, name="broken", template_type="project")
    (root / "x.txt.jinja").write_text("{% if %}", encoding="utf-8")

    render_template_editor(template_dir=root)

    assert "te-lint-errors" in _fake_ui.by_testid


def test_editor_edit_then_save_content_invokes_callback(
    _fake_ui: _Registry, tmp_path: Path
) -> None:
    """Clicking Edit loads the file; Save file calls on_save_content(rel, text)."""
    root = create_template_dir(tmp_path, name="proj", template_type="project")
    write_content_file(root, "data.csv", "a,b\n1,2\n")
    saved: list[tuple[str, str]] = []

    render_template_editor(
        template_dir=root,
        on_save_content=lambda rel, text: saved.append((rel, text)),
    )

    _click(_fake_ui, "te-edit-data.csv")  # loads content into the shared editor
    _click(_fake_ui, "te-save-content")
    assert saved == [("data.csv", "a,b\n1,2\n")]


def test_editor_delete_invokes_callback(_fake_ui: _Registry, tmp_path: Path) -> None:
    """The per-row Delete button calls on_delete(rel)."""
    root = create_template_dir(tmp_path, name="proj", template_type="project")
    write_content_file(root, "drop.txt", "x")
    deleted: list[str] = []

    render_template_editor(template_dir=root, on_delete=deleted.append)

    _click(_fake_ui, "te-delete-drop.txt")
    assert deleted == ["drop.txt"]


def test_editor_add_and_save_questions_rebuilds_manifest(
    _fake_ui: _Registry, tmp_path: Path
) -> None:
    """Add question -> set its widgets -> Save questions calls on_save_manifest."""
    root = create_template_dir(tmp_path, name="proj", template_type="project")
    saved: list[TemplateManifest] = []

    render_template_editor(template_dir=root, on_save_manifest=saved.append)

    _click(_fake_ui, "te-q-add")  # appends a blank question row
    # Fill the freshly-added row's key/kind so it survives the empty-key filter.
    _fake_ui.by_testid["te-q-key"].value = "specimen_id"
    _fake_ui.by_testid["te-q-kind"].value = "int"
    _fake_ui.by_testid["te-q-default"].value = "5"
    _click(_fake_ui, "te-save-questions")

    assert len(saved) == 1
    manifest = saved[0]
    assert manifest.exlab_type == "project"
    assert [q.key for q in manifest.questions] == ["specimen_id"]
    assert manifest.questions[0].kind == "int"
    assert manifest.questions[0].default == 5  # coerced toward the int kind


def test_editor_remove_question_drops_row(_fake_ui: _Registry, tmp_path: Path) -> None:
    """Adding then removing a question leaves no questions on save."""
    root = create_template_dir(tmp_path, name="proj", template_type="project")
    saved: list[TemplateManifest] = []

    render_template_editor(template_dir=root, on_save_manifest=saved.append)

    _click(_fake_ui, "te-q-add")
    _fake_ui.by_testid["te-q-key"].value = "temp"
    _click(_fake_ui, "te-q-remove")
    _click(_fake_ui, "te-save-questions")

    assert saved[0].questions == []


def test_editor_upload_handler_invokes_callback(_fake_ui: _Registry, tmp_path: Path) -> None:
    """The upload widget's event handler forwards (name, bytes, render_flag)."""
    root = create_template_dir(tmp_path, name="proj", template_type="project")
    uploads: list[tuple[str, bytes, bool]] = []

    render_template_editor(
        template_dir=root,
        on_upload=lambda name, data, flag: uploads.append((name, data, flag)),
    )

    _fake_ui.by_testid["te-upload-render"].value = True

    class _Content:
        def read(self) -> bytes:
            return b"payload"

    event = type("Evt", (), {"name": "proto.docx", "content": _Content()})()
    _click(_fake_ui, "te-upload", event)
    assert uploads == [("proto.docx", b"payload", True)]


def test_editor_back_button_invokes_callback(_fake_ui: _Registry, tmp_path: Path) -> None:
    """The Back button calls on_back."""
    root = create_template_dir(tmp_path, name="proj", template_type="project")
    backs: list[bool] = []

    render_template_editor(template_dir=root, on_back=lambda: backs.append(True))

    _click(_fake_ui, "te-back", None)
    assert backs == [True]


def test_editor_renders_warning_lint_banner(_fake_ui: _Registry, tmp_path: Path) -> None:
    """A manifest that only trips WARN lint rules renders the warn banner."""
    root = create_template_dir(tmp_path, name="warn", template_type="project")
    # A non-conforming question id is a WARN (not an ERROR), so the template
    # still loads but the warn banner must render.
    manifest_path = root / COPIER_MANIFEST_NAME
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8") + "\nBadKey:\n  type: str\n",
        encoding="utf-8",
    )

    render_template_editor(template_dir=root)

    assert "te-lint-warns" in _fake_ui.by_testid
    assert "te-lint-errors" not in _fake_ui.by_testid


def test_editor_save_content_without_open_is_noop(_fake_ui: _Registry, tmp_path: Path) -> None:
    """Clicking Save file before opening any file does not call the callback."""
    root = create_template_dir(tmp_path, name="proj", template_type="project")
    saved: list[tuple[str, str]] = []

    render_template_editor(
        template_dir=root, on_save_content=lambda rel, text: saved.append((rel, text))
    )

    _click(_fake_ui, "te-save-content")  # no file opened -> editor_state["rel"] is None
    assert saved == []


def test_editor_edit_unreadable_file_does_not_crash(_fake_ui: _Registry, tmp_path: Path) -> None:
    """Opening a file that disappears between listing and edit is swallowed."""
    root = create_template_dir(tmp_path, name="proj", template_type="project")
    write_content_file(root, "gone.txt", "x")
    saved: list[tuple[str, str]] = []
    render_template_editor(
        template_dir=root, on_save_content=lambda rel, text: saved.append((rel, text))
    )
    # Remove the file after render, then click its Edit button: _open hits the
    # read failure branch and returns without setting editor state.
    (root / "gone.txt").unlink()
    _click(_fake_ui, "te-edit-gone.txt")
    _click(_fake_ui, "te-save-content")
    assert saved == []  # nothing was loaded, so save is a no-op


def test_editor_upload_event_noop_when_callback_missing(
    _fake_ui: _Registry, tmp_path: Path
) -> None:
    """The upload handler is safe when on_upload is None (no crash)."""
    root = create_template_dir(tmp_path, name="proj", template_type="project")
    render_template_editor(template_dir=root)  # on_upload defaults to None
    event = type("Evt", (), {"name": "x.txt", "content": None})()
    _click(_fake_ui, "te-upload", event)  # must not raise


# A fake ``ui`` WITHOUT codemirror / upload to drive the fallback branches.
class _FakeUIMinimal(_FakeUI):
    """Like ``_FakeUI`` but lacks ``codemirror`` and ``upload`` attributes."""

    codemirror = None  # type: ignore[assignment]
    upload = None  # type: ignore[assignment]


@pytest.fixture
def _fake_ui_minimal(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Registry]:
    import types

    registry = _Registry()
    fake_module = types.ModuleType("nicegui")
    fake_module.ui = _FakeUIMinimal(registry)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nicegui", fake_module)
    yield registry


def test_editor_falls_back_to_textarea_and_marks_upload_unavailable(
    _fake_ui_minimal: _Registry, tmp_path: Path
) -> None:
    """Without codemirror/upload, the editor uses textarea + an unavailable note."""
    root = create_template_dir(tmp_path, name="proj", template_type="project")
    write_content_file(root, "n.md", "# n\n")

    card = render_template_editor(template_dir=root)

    assert card is not None
    ids = _fake_ui_minimal.by_testid
    # Inline editor still present (textarea fallback), upload flagged unavailable.
    assert "te-editor" in ids
    assert ids["te-editor"].kind == "textarea"
    assert "te-upload-unavailable" in ids
    assert "te-upload" not in ids


def test_editor_save_questions_noop_without_callback(_fake_ui: _Registry, tmp_path: Path) -> None:
    """Save questions with no on_save_manifest is a no-op (early return)."""
    root = create_template_dir(tmp_path, name="proj", template_type="project")
    render_template_editor(template_dir=root)  # on_save_manifest defaults to None
    _click(_fake_ui, "te-q-add")
    _fake_ui.by_testid["te-q-key"].value = "k"
    _click(_fake_ui, "te-save-questions")  # must not raise


def test_editor_question_kinds_coerce_defaults(_fake_ui: _Registry, tmp_path: Path) -> None:
    """choice/float/bool rows round-trip through _question_from_row + _coerce_default."""
    root = create_template_dir(tmp_path, name="proj", template_type="project")
    saved: list[TemplateManifest] = []
    render_template_editor(template_dir=root, on_save_manifest=saved.append)

    # Row 1: a choice question with a comma-separated choices field.
    _click(_fake_ui, "te-q-add")
    _fake_ui.by_testid["te-q-key"].value = "stain"
    _fake_ui.by_testid["te-q-kind"].value = "choice"
    _fake_ui.by_testid["te-q-choices"].value = "DAPI, GFP , RFP"
    _click(_fake_ui, "te-save-questions")

    q = saved[-1].questions[0]
    assert q.kind == "choice"
    assert q.choices == ("DAPI", "GFP", "RFP")  # split + stripped


def test_editor_bool_and_float_defaults_coerced(_fake_ui: _Registry, tmp_path: Path) -> None:
    """A float row coerces its default to float; a bool row to bool."""
    root = create_template_dir(tmp_path, name="proj", template_type="project")
    saved: list[TemplateManifest] = []
    render_template_editor(template_dir=root, on_save_manifest=saved.append)

    _click(_fake_ui, "te-q-add")
    _fake_ui.by_testid["te-q-key"].value = "exposure"
    _fake_ui.by_testid["te-q-kind"].value = "float"
    _fake_ui.by_testid["te-q-default"].value = "1.5"
    _click(_fake_ui, "te-save-questions")
    q = saved[-1].questions[0]
    assert q.default == 1.5
    assert isinstance(q.default, float)


def test_editor_bool_default_and_uncoercible_int_fallback(
    _fake_ui: _Registry, tmp_path: Path
) -> None:
    """bool default coerces to True; an int kind with non-numeric default
    falls back to the raw string (the _coerce_default ValueError branch)."""
    root = create_template_dir(tmp_path, name="proj", template_type="project")
    saved: list[TemplateManifest] = []
    render_template_editor(template_dir=root, on_save_manifest=saved.append)

    _click(_fake_ui, "te-q-add")
    _fake_ui.by_testid["te-q-key"].value = "enabled"
    _fake_ui.by_testid["te-q-kind"].value = "bool"
    _fake_ui.by_testid["te-q-default"].value = "yes"
    _click(_fake_ui, "te-save-questions")
    assert saved[-1].questions[0].default is True

    _click(_fake_ui, "te-q-add")
    # Second row added; its widgets are the latest te-q-* bound by the form.
    _fake_ui.by_testid["te-q-key"].value = "count"
    _fake_ui.by_testid["te-q-kind"].value = "int"
    _fake_ui.by_testid["te-q-default"].value = "not-a-number"
    _click(_fake_ui, "te-save-questions")
    # The uncoercible int default falls back to the raw string, not a crash.
    count_q = next(q for q in saved[-1].questions if q.key == "count")
    assert count_q.default == "not-a-number"


def test_editor_renders_with_unreadable_manifest(_fake_ui: _Registry, tmp_path: Path) -> None:
    """A corrupt copier.yml still renders (empty manifest + error lint banner)."""
    root = create_template_dir(tmp_path, name="corrupt", template_type="project")
    # Invalid YAML -> read_manifest raises -> both manifest-read branches fall back.
    (root / COPIER_MANIFEST_NAME).write_text("{ this: is: not: yaml", encoding="utf-8")

    card = render_template_editor(template_dir=root)

    assert card is not None
    # The error banner renders from the copier.yml parse failure.
    assert "te-lint-errors" in _fake_ui.by_testid
