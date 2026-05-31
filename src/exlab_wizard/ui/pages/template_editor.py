"""Template authoring editor page (Frontend Spec §5 GUI).

The author-time counterpart to :mod:`exlab_wizard.ui.pages.templates`: where
the manager lists / scaffolds templates, this page opens *one* template
directory and lets the operator edit its ``copier.yml`` (questions), edit its
inline text files, upload binary / rendered files, and delete files. Every
disk mutation lives in the callbacks (wired in :mod:`exlab_wizard.ui.mount`)
that delegate to the :mod:`exlab_wizard.template.authoring` service; this
module only *reads* the template (manifest + file list + lint findings) to
render and routes operator actions back to those callbacks.

:func:`render_template_editor` follows the same pure-view-with-payload
contract as :func:`exlab_wizard.ui.pages.templates.render_template_manager`:
when NiceGUI is unavailable it returns a plain payload dict (so the view is
unit-testable headless); otherwise it builds the NiceGUI widget tree. Every
widget carries a ``data-testid`` for the browser e2e suite.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from exlab_wizard.logging import get_logger
from exlab_wizard.template.authoring import list_files, read_content, read_manifest
from exlab_wizard.template.lint import has_errors, lint_template
from exlab_wizard.template.manifest import TemplateManifest, TemplateQuestion

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["render_template_editor"]

_log = get_logger(__name__)

# The Copier question kinds the editor's type ``ui.select`` offers. ``choice``
# is the editor-level pseudo-kind that ``TemplateQuestion`` normalises to (it
# round-trips through ``copier.yml`` as ``type: str`` + a ``choices`` block).
_QUESTION_KINDS: tuple[str, ...] = ("str", "int", "float", "bool", "choice")


def _build_payload(template_dir: Path) -> dict[str, Any]:
    """Read ``template_dir`` and return the headless render payload.

    Loads the manifest + file list + lint findings via the authoring /
    lint services. Used directly as the NiceGUI-unavailable return value
    and as the data source for the NiceGUI branch, so the two stay in
    lock-step. Tolerant of an unreadable manifest (falls back to empty
    metadata) so a broken template still renders its lint banner.
    """
    try:
        manifest, _stat = read_manifest(template_dir)
    except Exception as exc:  # a broken manifest must still render its lint banner
        _log.warning("template editor: failed to read manifest for %s: %s", template_dir, exc)
        manifest = None

    files = list_files(template_dir)
    findings = lint_template(template_dir)
    return {
        "template": template_dir.name,
        "files": [entry["rel"] for entry in files],
        "questions": [q.key for q in (manifest.questions if manifest else [])],
        "findings": [f.code for f in findings],
        "exlab_type": manifest.exlab_type if manifest else "",
        "run_scope": manifest.exlab_run_scope if manifest else None,
    }


def render_template_editor(
    *,
    template_dir: Path,
    on_save_manifest: Callable[[TemplateManifest], None] | None = None,
    on_save_content: Callable[[str, str], None] | None = None,
    on_upload: Callable[[str, bytes, bool], None] | None = None,
    on_delete: Callable[[str], None] | None = None,
    on_back: Callable[[], None] | None = None,
) -> Any:
    """Render the single-template authoring editor.

    The view loads ``template_dir`` (manifest, file list, lint findings)
    and renders a header + lint banner, a file list with per-row edit /
    delete actions, an inline text editor, a question form, and an upload
    widget. All disk mutation is delegated to the callbacks:

    * ``on_save_manifest(manifest)`` -- persist a rebuilt
      :class:`~exlab_wizard.template.manifest.TemplateManifest` (the
      question form preserves the loaded manifest's non-question
      ``_exlab_*`` fields and replaces only its questions).
    * ``on_save_content(rel, text)`` -- write an edited text file.
    * ``on_upload(filename, data, render_as_template)`` -- store an upload.
    * ``on_delete(rel)`` -- remove a file.
    * ``on_back()`` -- leave the editor.

    Every callback is optional; when ``None`` the corresponding action is
    a no-op so the function still renders (and returns its payload
    headless) without raising.

    Returns:
        The NiceGUI root card, or -- when NiceGUI is unavailable -- the
        headless payload dict from :func:`_build_payload`.
    """
    payload = _build_payload(template_dir)
    try:
        from nicegui import ui
    except Exception:
        return payload

    # Re-read structured data for the rich render (the payload only carries
    # the headless projection). A broken manifest falls back to an empty one
    # so the lint banner + file list still render.
    try:
        manifest, _stat = read_manifest(template_dir)
    except Exception as exc:  # render the banner even if the manifest is unreadable
        _log.warning("template editor: manifest unreadable for render: %s", exc)
        manifest = TemplateManifest(exlab_type="", exlab_version="")
    findings = lint_template(template_dir)
    entries = list_files(template_dir)

    card = (
        ui.card()
        .props('data-testid="te-card"')
        .style(
            "min-width: 820px; margin: 2rem auto; padding: var(--sp-6); "
            "background: var(--color-surface); border-radius: var(--radius-md);"
        )
    )
    with card:
        _render_header(ui, template_dir.name)
        _render_lint_banner(ui, findings)
        _render_file_list(ui, template_dir, entries, on_save_content, on_delete)
        _render_question_form(ui, manifest, on_save_manifest)
        _render_upload(ui, on_upload)
        if on_back is not None:
            with (
                ui.row()
                .classes("items-center w-full justify-end")
                .style("gap: var(--sp-3); padding-top: var(--sp-4);")
            ):
                ui.button("Back", on_click=lambda _evt: on_back()).props(
                    'flat data-testid="te-back"'
                )
    return card


# ---------------------------------------------------------------------------
# NiceGUI section renderers (only reached when NiceGUI imports cleanly)
# ---------------------------------------------------------------------------


def _render_header(ui: Any, name: str) -> None:
    """Render the template-name header."""
    ui.label(f"Editing template: {name}").props('data-testid="te-title"').style(
        "font-family: var(--font-display); font-size: var(--text-lg); "
        "font-weight: 600; color: var(--color-heading);"
    )


def _render_lint_banner(ui: Any, findings: list[Any]) -> None:
    """Render the lint banner: errors red, warnings amber, else a clean note."""
    errors = [f for f in findings if f.severity == "error"]
    warns = [f for f in findings if f.severity == "warn"]
    if has_errors(findings):
        banner = ui.column().props('data-testid="te-lint-errors"')
        with banner:
            ui.label(f"{len(errors)} error(s) -- template will not load:").style(
                "color: var(--color-danger, #d33); font-weight: 600;"
            )
            for finding in errors:
                ui.label(f"• {finding.message}").style("color: var(--color-danger, #d33);")
    if warns:
        banner = ui.column().props('data-testid="te-lint-warns"')
        with banner:
            ui.label(f"{len(warns)} warning(s):").style(
                "color: var(--color-warning, #b8860b); font-weight: 600;"
            )
            for finding in warns:
                ui.label(f"• {finding.message}").style("color: var(--color-warning, #b8860b);")
    if not findings:
        ui.label("No lint findings.").props('data-testid="te-lint-clean"').style(
            "color: var(--color-muted);"
        )


def _render_file_list(
    ui: Any,
    template_dir: Path,
    entries: list[dict],
    on_save_content: Callable[[str, str], None] | None,
    on_delete: Callable[[str], None] | None,
) -> None:
    """Render the file list with per-row edit / delete actions + inline editor.

    Each editable text file gets an Edit button that loads its content into
    a shared inline editor (``ui.codemirror`` if available, else
    ``ui.textarea``); the editor's Save button calls ``on_save_content``.
    Every non-``copier.yml`` row gets a Delete button calling ``on_delete``.
    """
    ui.label("Files").style("font-weight: 600; padding-top: var(--sp-3);")

    # Shared inline editor + its state, declared up front so the per-row
    # Edit handlers can target it. The editor widget is whichever of
    # codemirror / textarea NiceGUI provides.
    editor_state: dict[str, Any] = {"rel": None}
    editor_factory = getattr(ui, "codemirror", None) or ui.textarea
    editor = editor_factory(value="").props('data-testid="te-editor"')
    editor.style("width: 100%; min-height: 12rem; display: none;")

    def _open(rel: str) -> None:
        path = template_dir / rel
        try:
            text, _stat = read_content(path)
        except Exception as exc:  # surface unreadable files instead of crashing the page
            _log.warning("template editor: cannot open %s: %s", rel, exc)
            return
        editor_state["rel"] = rel
        editor.value = text
        editor.style("width: 100%; min-height: 12rem; display: block;")

    def _save() -> None:
        rel = editor_state["rel"]
        if rel is None or on_save_content is None:
            return
        on_save_content(rel, editor.value or "")

    for entry in entries:
        rel = entry["rel"]
        with (
            ui.row()
            .classes("items-center w-full")
            .props(f'data-testid="te-file-{rel}"')
            .style("gap: var(--sp-2);")
        ):
            label = rel + ("/" if entry["is_dir"] else "")
            ui.label(label).style("color: var(--color-body); flex: 1;")
            if entry["editable"]:
                ui.button("Edit", on_click=lambda _evt, r=rel: _open(r)).props(
                    f'flat dense data-testid="te-edit-{rel}"'
                )
            if not entry["is_dir"]:
                ui.button(
                    "Delete",
                    on_click=lambda _evt, r=rel: on_delete and on_delete(r),
                ).props(f'flat dense color=negative data-testid="te-delete-{rel}"')

    ui.button("Save file", on_click=lambda _evt: _save()).props(
        'color=primary data-testid="te-save-content"'
    )


def _render_question_form(
    ui: Any,
    manifest: TemplateManifest,
    on_save_manifest: Callable[[TemplateManifest], None] | None,
) -> None:
    """Render the editable question form bound to the manifest's questions.

    Each question is one row of widgets (key / kind / default / help /
    choices / secret). Add / Remove buttons grow / shrink the row list;
    Save Questions rebuilds a :class:`TemplateManifest` -- preserving every
    non-question ``_exlab_*`` field from ``manifest`` and replacing only its
    questions -- and calls ``on_save_manifest``.
    """
    ui.label("Questions").style("font-weight: 600; padding-top: var(--sp-3);")

    # Each row's widgets are stashed in a dict so Save can read their values
    # back. ``rows`` is the live working set; Remove drops a row's widgets.
    rows: list[dict[str, Any]] = []
    container = ui.column().props('data-testid="te-questions"').style("width: 100%;")

    def _add_row(question: TemplateQuestion | None = None) -> None:
        with container:
            row = ui.row().classes("items-center w-full").style("gap: var(--sp-2);")
        with row:
            key_w = ui.input(label="Key", value=(question.key if question else "")).props(
                'dense data-testid="te-q-key"'
            )
            kind_w = ui.select(
                list(_QUESTION_KINDS),
                value=(question.kind if question else "str"),
                label="Type",
            ).props('dense data-testid="te-q-kind"')
            default_w = ui.input(
                label="Default",
                value=(
                    "" if question is None or question.default is None else str(question.default)
                ),
            ).props('dense data-testid="te-q-default"')
            help_w = ui.input(label="Help", value=(question.help if question else "")).props(
                'dense data-testid="te-q-help"'
            )
            choices_w = ui.input(
                label="Choices (comma-separated)",
                value=(", ".join(str(c) for c in question.choices) if question else ""),
            ).props('dense data-testid="te-q-choices"')
            secret_w = ui.checkbox(
                "Secret", value=(bool(question.secret) if question else False)
            ).props('data-testid="te-q-secret"')
        record = {
            "row": row,
            "key": key_w,
            "kind": kind_w,
            "default": default_w,
            "help": help_w,
            "choices": choices_w,
            "secret": secret_w,
        }

        def _remove(_evt: Any = None, rec: dict[str, Any] = record) -> None:
            rec["row"].delete()
            if rec in rows:
                rows.remove(rec)

        with row:
            ui.button("Remove", on_click=_remove).props(
                'flat dense color=negative data-testid="te-q-remove"'
            )
        rows.append(record)

    for existing in manifest.questions:
        _add_row(existing)

    def _save() -> None:
        if on_save_manifest is None:
            return
        questions = [_question_from_row(rec) for rec in rows]
        questions = [q for q in questions if q.key]
        rebuilt = TemplateManifest(
            exlab_type=manifest.exlab_type,
            exlab_version=manifest.exlab_version,
            exlab_run_scope=manifest.exlab_run_scope,
            description=manifest.description,
            plugins=list(manifest.plugins),
            readme_fields=list(manifest.readme_fields),
            questions=questions,
            min_copier_version=manifest.min_copier_version,
            answers_file=manifest.answers_file,
        )
        on_save_manifest(rebuilt)

    with (
        ui.row().classes("items-center w-full").style("gap: var(--sp-3); padding-top: var(--sp-2);")
    ):
        ui.button("Add question", on_click=lambda _evt: _add_row()).props(
            'flat data-testid="te-q-add"'
        )
        ui.button("Save questions", on_click=lambda _evt: _save()).props(
            'color=primary data-testid="te-save-questions"'
        )


def _question_from_row(rec: dict[str, Any]) -> TemplateQuestion:
    """Build a :class:`TemplateQuestion` from one form row's widget values.

    The default string is coerced toward the selected kind (int / float /
    bool) so a round-trip through ``copier.yml`` keeps the declared type;
    an uncoercible value falls back to the raw string. ``choice`` questions
    split the comma-text choices field; other kinds carry no choices.
    """
    key = (rec["key"].value or "").strip()
    kind = rec["kind"].value or "str"
    raw_default = rec["default"].value or ""
    help_text = rec["help"].value or ""
    secret = bool(rec["secret"].value)

    choices: tuple[Any, ...] = ()
    if kind == "choice":
        choices = tuple(
            piece.strip() for piece in str(rec["choices"].value or "").split(",") if piece.strip()
        )

    default = _coerce_default(raw_default, kind)
    return TemplateQuestion(
        key=key, kind=kind, default=default, choices=choices, help=help_text, secret=secret
    )


def _coerce_default(raw: str, kind: str) -> Any:
    """Coerce a string default toward ``kind`` (``None`` for an empty string)."""
    text = raw.strip()
    if not text:
        return None
    try:
        if kind == "int":
            return int(text)
        if kind == "float":
            return float(text)
        if kind == "bool":
            return text.lower() in {"1", "true", "yes", "on"}
    except ValueError:
        return text
    return text


def _render_upload(ui: Any, on_upload: Callable[[str, bytes, bool], None] | None) -> None:
    """Render the upload widget + a "render as template" checkbox.

    ``ui.upload`` is guarded (not yet a hard dependency in this codebase);
    when absent the section is skipped. On upload the handler reads the
    NiceGUI upload event's bytes and calls ``on_upload(name, data, flag)``.
    """
    ui.label("Upload file").style("font-weight: 600; padding-top: var(--sp-3);")
    render_flag = ui.checkbox("Render as template (.jinja)", value=False).props(
        'data-testid="te-upload-render"'
    )

    upload_widget = getattr(ui, "upload", None)
    if upload_widget is None:
        ui.label("Upload unavailable in this build.").props('data-testid="te-upload-unavailable"')
        return

    def _on_event(event: Any) -> None:
        if on_upload is None:
            return
        name = getattr(event, "name", "") or ""
        content = getattr(event, "content", None)
        data = content.read() if content is not None and hasattr(content, "read") else b""
        on_upload(name, data, bool(render_flag.value))

    upload_widget(on_upload=_on_event, auto_upload=True).props('data-testid="te-upload"')
