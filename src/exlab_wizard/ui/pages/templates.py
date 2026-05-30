"""Template manager page (Frontend Spec §4 step 2, §5 step 2).

Two operations the wizards depend on:

* :func:`list_templates` -- scan ``config.paths.templates_dir`` for
  Copier templates (directories containing a ``copier.yml``) and return
  a small summary per template. The project / run wizards call this to
  populate their "pick a template" step.
* :func:`create_template` -- scaffold a new minimal Copier template
  under ``templates_dir``: a ``copier.yml`` carrying the ``_exlab_*``
  manifest keys plus one rendered content file. The result is
  immediately loadable by :class:`~exlab_wizard.template.copier_driver.TemplateEngine`.

``list_templates`` / ``create_template`` are pure (no NiceGUI) so they
are unit-testable; :func:`render_template_manager` is the NiceGUI view.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from exlab_wizard.constants import COPIER_MANIFEST_NAME, RunScope, TemplateType
from exlab_wizard.logging import get_logger

# ``TemplateQuestion`` / ``template_questions`` live in ``template.manifest``
# (a non-UI module) so the typed manifest model can reuse them without
# importing this NiceGUI page. Re-exported here so existing callers
# (``from exlab_wizard.ui.pages.templates import TemplateQuestion``) keep
# working unchanged.
from exlab_wizard.template.manifest import TemplateQuestion, template_questions

__all__ = [
    "TemplateQuestion",
    "TemplateSummary",
    "create_template",
    "list_templates",
    "render_question_field",
    "render_template_manager",
    "template_questions",
]

_log = get_logger(__name__)

# Content file every scaffolded template carries. ``.jinja`` so Copier
# renders it; the body has no variables so it renders verbatim.
_SCAFFOLD_CONTENT_NAME = "notes.md.jinja"
_SCAFFOLD_CONTENT_BODY = "# Notes\n\nScaffolded by ExLab-Wizard.\n"


@dataclass(frozen=True)
class TemplateSummary:
    """One row in the template list.

    ``name`` is the template directory name (what the wizards store as
    ``selected_template``); ``path`` is its absolute location;
    ``template_type`` / ``run_scope`` / ``description`` come from the
    ``_exlab_*`` keys in ``copier.yml``.
    """

    name: str
    path: Path
    template_type: str
    run_scope: str | None
    description: str


def list_templates(
    templates_dir: Path,
    *,
    template_type: str | None = None,
) -> list[TemplateSummary]:
    """Return the templates under ``templates_dir``, optionally filtered.

    A template is any immediate sub-directory containing a
    ``copier.yml``. Malformed manifests are skipped with a WARN rather
    than failing the whole scan. When ``template_type`` is given, only
    templates whose ``_exlab_type`` matches are returned.
    """
    root = Path(templates_dir)
    if not root.is_dir():
        return []
    summaries: list[TemplateSummary] = []
    for entry in sorted(root.iterdir()):
        manifest = entry / COPIER_MANIFEST_NAME
        if not entry.is_dir() or not manifest.is_file():
            continue
        try:
            data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            _log.warning("skipping malformed template manifest %s: %s", manifest, exc)
            continue
        if not isinstance(data, dict):
            continue
        t_type = str(data.get("_exlab_type", ""))
        if template_type is not None and t_type != template_type:
            continue
        summaries.append(
            TemplateSummary(
                name=entry.name,
                path=entry,
                template_type=t_type,
                run_scope=(
                    str(data["_exlab_run_scope"])
                    if data.get("_exlab_run_scope") is not None
                    else None
                ),
                description=str(data.get("_exlab_description", "")),
            )
        )
    return summaries


def create_template(
    templates_dir: Path,
    *,
    name: str,
    template_type: str,
    description: str = "",
    run_scope: str | None = None,
) -> Path:
    """Scaffold a new minimal Copier template under ``templates_dir``.

    Writes ``<templates_dir>/<name>/copier.yml`` plus one content file.
    Returns the new template's root directory. Raises ``ValueError`` on
    an empty / duplicate name, an unknown ``template_type``, or a run
    template missing its ``run_scope``.

    The scaffold logic lives in
    :func:`exlab_wizard.template.authoring.create_template_dir` (the
    non-UI authoring service); this thin wrapper preserves the historical
    page-level signature and is imported lazily to avoid an import cycle.
    """
    # Imported lazily so this NiceGUI page module does not pull in the
    # authoring service (and its deps) at import time.
    from exlab_wizard.template.authoring import create_template_dir

    return create_template_dir(
        Path(templates_dir),
        name=name,
        template_type=template_type,
        description=description,
        run_scope=run_scope,
    )


def render_question_field(
    question: TemplateQuestion,
    answers: dict[str, Any],
    *,
    testid_prefix: str,
) -> None:
    """Render one Copier question as a bound NiceGUI widget.

    The widget two-way binds into ``answers[question.key]``; the entry
    is seeded with the question's default so a never-touched field
    still contributes its default to the render. ``testid_prefix``
    namespaces the ``data-testid`` (``f"{prefix}-{key}"``).
    """
    from nicegui import ui

    key = question.key
    answers.setdefault(key, question.default)
    testid = f"{testid_prefix}-{key}"
    label = key.replace("_", " ").strip().title()

    widget: Any
    if question.kind == "bool":
        widget = ui.checkbox(label, value=bool(answers.get(key)))
        widget.props(f'data-testid="{testid}"')
        widget.on_value_change(lambda e: answers.__setitem__(key, bool(e.value)))
        return
    if question.kind in ("int", "float"):
        current = answers.get(key)
        widget = ui.number(label=label, value=current if current is not None else 0)
        widget.props(f'data-testid="{testid}"')
        cast = int if question.kind == "int" else float
        widget.on_value_change(
            lambda e: answers.__setitem__(key, cast(e.value) if e.value is not None else None)
        )
        return
    if question.kind == "choice":
        widget = ui.select(
            list(question.choices),
            value=answers.get(key) if answers.get(key) in question.choices else None,
            label=label,
        )
        widget.props(f'data-testid="{testid}"')
        widget.on_value_change(lambda e: answers.__setitem__(key, e.value))
        return
    # str (and yaml/json, which the wizard treats as free text).
    widget = ui.input(label=label, value=str(answers.get(key) or ""))
    widget.props(f'data-testid="{testid}"')
    if question.secret:
        widget.props("type=password")
    widget.on_value_change(lambda e: answers.__setitem__(key, e.value or ""))


def render_template_manager(
    *,
    templates: list[TemplateSummary],
    on_create: Callable[[str, str, str, str | None], None] | None = None,
    on_back: Callable[[], None] | None = None,
    on_edit: Callable[[str], None] | None = None,
    locations: list[tuple[str, str]] | None = None,
    on_location_change: Callable[[str], None] | None = None,
) -> Any:
    """Render the template manager: existing-template list + create form.

    ``on_create`` is invoked with ``(name, template_type, description,
    run_scope)`` when the operator submits the create form;
    ``run_scope`` is ``None`` for non-run templates.

    ``on_edit`` (optional) is invoked with a template name when the
    operator clicks that row's Edit button -- the caller routes it to the
    template editor page.

    ``locations`` (optional) is a ``[(label, value), ...]`` scope list (at
    minimum ``("Global", ...)`` plus one entry per configured equipment /
    project); when given, a selector is rendered at the top and
    ``on_location_change(value)`` is invoked when the operator switches
    scope (the caller re-renders the manager scoped to that location). All
    three new parameters are optional and default ``None`` so existing
    callers keep working unchanged.
    """
    payload = {
        "templates": [t.name for t in templates],
        "count": len(templates),
    }
    if locations is not None:
        payload["locations"] = list(locations)
    try:
        from nicegui import ui
    except Exception:
        return payload

    card = (
        ui.card()
        .props('data-testid="templates-card"')
        .style(
            "min-width: 720px; margin: 2rem auto; padding: var(--sp-6); "
            "background: var(--color-surface); border-radius: var(--radius-md);"
        )
    )
    with card:
        ui.label("Templates").props('data-testid="templates-title"').style(
            "font-family: var(--font-display); font-size: var(--text-lg); "
            "font-weight: 600; color: var(--color-heading);"
        )

        # Scope / location selector -----------------------------------------
        if locations:
            label_by_value = {value: label for label, value in locations}
            location_select = ui.select(
                {value: label for label, value in locations},
                value=locations[0][1],
                label="Location",
            ).props('data-testid="templates-location"')
            if on_location_change is not None:
                location_select.on_value_change(
                    lambda e: on_location_change(e.value) if e.value in label_by_value else None
                )

        # Existing templates ------------------------------------------------
        if templates:
            for summary in templates:
                scope = f" [{summary.run_scope}]" if summary.run_scope else ""
                with (
                    ui.row()
                    .classes("items-center w-full")
                    .props('data-testid="template-row"')
                    .style("gap: var(--sp-2);")
                ):
                    ui.label(f"{summary.name} -- {summary.template_type}{scope}").style(
                        "color: var(--color-body); flex: 1;"
                    )
                    if on_edit is not None:
                        ui.button(
                            "Edit",
                            on_click=lambda _evt, n=summary.name: on_edit(n),
                        ).props(f'flat dense data-testid="template-edit-{summary.name}"')
        else:
            ui.label("No templates yet. Create one below.").props(
                'data-testid="templates-empty"'
            ).style("color: var(--color-muted);")

        # Create form -------------------------------------------------------
        ui.label("New template").style("font-weight: 600; padding-top: var(--sp-3);")
        name_input = ui.input(label="Template name").props('data-testid="template-name"')
        type_select = ui.select(
            [t.value for t in TemplateType],
            value=TemplateType.PROJECT.value,
            label="Template type",
        ).props('data-testid="template-type"')
        scope_select = ui.select(
            [s.value for s in RunScope],
            value=RunScope.EXPERIMENTAL.value,
            label="Run scope (run templates only)",
        ).props('data-testid="template-run-scope"')
        description_input = ui.input(label="Description").props(
            'data-testid="template-description"'
        )

        def _submit(_evt: Any = None) -> None:
            if on_create is None:
                return
            run_scope = scope_select.value if type_select.value == TemplateType.RUN.value else None
            on_create(
                name_input.value or "",
                type_select.value or TemplateType.PROJECT.value,
                description_input.value or "",
                run_scope,
            )

        with (
            ui.row()
            .classes("items-center w-full justify-end")
            .style("gap: var(--sp-3); padding-top: var(--sp-4);")
        ):
            if on_back is not None:
                ui.button("Back", on_click=lambda _evt: on_back()).props(
                    'flat data-testid="templates-back"'
                )
            ui.button("Create template", on_click=_submit).props(
                'color=primary data-testid="template-create"'
            )
    return card
