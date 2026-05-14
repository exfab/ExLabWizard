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

__all__ = [
    "TemplateSummary",
    "create_template",
    "list_templates",
    "render_template_manager",
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
        except Exception as exc:  # noqa: BLE001 -- skip malformed, keep scanning
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
    """
    clean_name = name.strip()
    if not clean_name:
        msg = "template name must not be empty"
        raise ValueError(msg)
    if template_type not in {t.value for t in TemplateType}:
        msg = f"unknown template type {template_type!r}"
        raise ValueError(msg)
    if template_type == TemplateType.RUN.value:
        if run_scope is None:
            msg = "run templates require a run_scope"
            raise ValueError(msg)
        if run_scope not in {s.value for s in RunScope}:
            msg = f"unknown run_scope {run_scope!r}"
            raise ValueError(msg)

    root = Path(templates_dir) / clean_name
    if root.exists():
        msg = f"a template named {clean_name!r} already exists"
        raise ValueError(msg)
    root.mkdir(parents=True)

    manifest: dict[str, Any] = {
        "_min_copier_version": "9.0",
        "_exlab_type": template_type,
        "_exlab_version": "1.0",
        "_exlab_description": description.strip(),
    }
    if template_type == TemplateType.RUN.value:
        manifest["_exlab_run_scope"] = run_scope
    (root / COPIER_MANIFEST_NAME).write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    (root / _SCAFFOLD_CONTENT_NAME).write_text(_SCAFFOLD_CONTENT_BODY, encoding="utf-8")
    _log.info("scaffolded %s template %r at %s", template_type, clean_name, root)
    return root


def render_template_manager(
    *,
    templates: list[TemplateSummary],
    on_create: Callable[[str, str, str, str | None], None] | None = None,
    on_back: Callable[[], None] | None = None,
) -> Any:
    """Render the template manager: existing-template list + create form.

    ``on_create`` is invoked with ``(name, template_type, description,
    run_scope)`` when the operator submits the create form;
    ``run_scope`` is ``None`` for non-run templates.
    """
    payload = {
        "templates": [t.name for t in templates],
        "count": len(templates),
    }
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

        # Existing templates ------------------------------------------------
        if templates:
            for summary in templates:
                scope = f" [{summary.run_scope}]" if summary.run_scope else ""
                ui.label(
                    f"{summary.name} -- {summary.template_type}{scope}"
                ).props('data-testid="template-row"').style("color: var(--color-body);")
        else:
            ui.label("No templates yet. Create one below.").props(
                'data-testid="templates-empty"'
            ).style("color: var(--color-muted);")

        # Create form -------------------------------------------------------
        ui.label("New template").style(
            "font-weight: 600; padding-top: var(--sp-3);"
        )
        name_input = ui.input(label="Template name").props(
            'data-testid="template-name"'
        )
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
            run_scope = (
                scope_select.value
                if type_select.value == TemplateType.RUN.value
                else None
            )
            on_create(
                name_input.value or "",
                type_select.value or TemplateType.PROJECT.value,
                description_input.value or "",
                run_scope,
            )

        with ui.row().classes("items-center w-full justify-end").style(
            "gap: var(--sp-3); padding-top: var(--sp-4);"
        ):
            if on_back is not None:
                ui.button("Back", on_click=lambda _evt: on_back()).props(
                    'flat data-testid="templates-back"'
                )
            ui.button("Create template", on_click=_submit).props(
                'color=primary data-testid="template-create"'
            )
    return card
