"""Read-side template resolver (generalizes Backend Spec §5.0).

The project / run wizards do not offer a single flat template list: a
template defined nearer the work (per-project, then per-equipment) should
override a same-named template defined further out (the global
``paths.templates_dir``). This module turns a config plus a wizard context
(``template_type`` + optional ``equipment_id`` / ``project_path``) into the
ordered, de-duplicated list of templates the wizard should offer, nearest
scope first, nearest scope winning on a name collision.

Directory layout searched (design spec §2):

* **Global** -- ``paths.templates_dir`` (flat: templates sit directly
  inside, all types mixed, filtered by ``_exlab_type``).
* **Per-equipment** -- ``<local_root>/<equipment_id>/.exlab-wizard/templates/<type>/``
  (type-segregated).
* **Per-project** -- ``<project_path>/.exlab-wizard/templates/<type>/``
  (type-segregated).

The per-instance directory scanning + manifest parsing is delegated to
:func:`exlab_wizard.ui.pages.templates.list_templates`; this module only
composes the search chain and merges the results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from exlab_wizard.constants import TEMPLATES_SUBDIR, RunScope, TemplateType
from exlab_wizard.paths import cache_dir

if TYPE_CHECKING:
    from pathlib import Path

    from exlab_wizard.config.models import Config
    from exlab_wizard.ui.pages.templates import TemplateQuestion, TemplateSummary

__all__ = [
    "TemplateChoices",
    "instance_template_dir",
    "project_dir",
    "reconcile_selection",
    "resolve_template_chain",
    "search_dirs",
]


@dataclass(frozen=True)
class TemplateChoices:
    """The templates a wizard offers for one resolution context.

    Bundles the three parallel views the wizard's template + variables
    steps need so a single ``on_resolve(equipment_id, project_name)`` call
    returns everything for the current selection:

    Attributes:
        names: Offered template names, nearest scope first.
        questions: Per-template parsed ``copier.yml`` questions (drives the
            dynamic Variables step). Missing entry == no variables.
        paths: Per-template absolute source path of the *resolved* template,
            so ``on_submit`` renders the exact file the wizard listed rather
            than re-deriving ``templates_dir / name`` (which would ignore a
            per-instance override). Keyed by template name.
    """

    names: list[str] = field(default_factory=list)
    questions: dict[str, list[TemplateQuestion]] = field(default_factory=dict)
    paths: dict[str, Path] = field(default_factory=dict)


def instance_template_dir(instance_dir: Path, template_type: str) -> Path:
    """Return the per-instance template directory for one scope.

    A per-instance (per-equipment / per-project) template store is
    ``<instance_dir>/.exlab-wizard/templates/<template_type>/`` -- the
    ``.exlab-wizard`` cache dir, a ``templates`` sub-dir, then a child
    sub-dir per template type so the project / run stores never collide.

    Args:
        instance_dir: The equipment or project root directory.
        template_type: The child template type (``"project"`` / ``"run"``)
            whose sub-dir is returned.

    Returns:
        The (possibly non-existent) per-instance template directory.
    """
    return cache_dir(instance_dir) / TEMPLATES_SUBDIR / template_type


def search_dirs(
    config: Config,
    *,
    template_type: str,
    equipment_id: str | None = None,
    project_path: Path | None = None,
) -> list[Path]:
    """Return the template search directories, highest precedence first.

    The precedence chain narrows from the work outwards: a per-project
    store beats a per-equipment store, which beats the global
    ``paths.templates_dir``. Which scopes apply depends on the template
    type the wizard is offering:

    * ``"run"`` -- per-project, then per-equipment, then global.
    * ``"project"`` -- per-equipment, then global (a project has no
      children of its own to scope project templates by).
    * ``"equipment"`` -- global only.

    A scope is included only when its backing config value is present:
    the global dir is skipped when ``paths.templates_dir`` is empty, the
    per-equipment dir when ``equipment_id`` is ``None`` (or ``local_root``
    is empty), and the per-project dir when ``project_path`` is ``None``.
    Returned directories need not exist -- :func:`list_templates`
    tolerates a missing directory by returning no templates.

    Args:
        config: The loaded config (supplies ``paths.templates_dir`` and
            ``paths.local_root``).
        template_type: One of ``"project"`` / ``"run"`` / ``"equipment"``.
        equipment_id: The equipment the wizard runs under, if any. Gates
            the per-equipment scope.
        project_path: The absolute project directory, if any. Gates the
            per-project scope (run wizard only).

    Returns:
        The ordered list of search directories, nearest scope first.
    """
    dirs: list[Path] = []

    if template_type == TemplateType.RUN.value:
        if project_path is not None:
            dirs.append(instance_template_dir(project_path, template_type))
        equipment_dir = _equipment_dir(config, equipment_id)
        if equipment_dir is not None:
            dirs.append(instance_template_dir(equipment_dir, template_type))
    elif template_type == TemplateType.PROJECT.value:
        equipment_dir = _equipment_dir(config, equipment_id)
        if equipment_dir is not None:
            dirs.append(instance_template_dir(equipment_dir, template_type))

    global_dir = _global_dir(config)
    if global_dir is not None:
        dirs.append(global_dir)
    return dirs


def resolve_template_chain(
    config: Config,
    *,
    template_type: str,
    equipment_id: str | None = None,
    project_path: Path | None = None,
    run_scope: str | None = None,
) -> list[TemplateSummary]:
    """Resolve the merged template list a wizard should offer.

    Walks :func:`search_dirs` nearest-first, scanning each directory with
    :func:`list_templates`, and merges the results by template name keeping
    the **first** (nearest-scope) occurrence -- so a per-project template
    shadows a same-named per-equipment or global one. Nearest-first order is
    preserved in the result.

    ``template_type`` is always passed to :func:`list_templates`: the global
    dir is flat (mixed types) so the filter is required, and per-instance
    ``<type>/`` dirs pass it defensively so a misfiled template of the wrong
    type is skipped rather than offered.

    For run templates, ``run_scope`` additionally narrows by the
    template's declared scope: when given (``"experimental"`` / ``"test"``)
    a run template is kept only when its ``run_scope`` equals that scope or
    is ``"both"``. ``run_scope=None`` keeps every run template. The
    parameter is ignored for non-run template types.

    Args:
        config: The loaded config.
        template_type: One of ``"project"`` / ``"run"`` / ``"equipment"``.
        equipment_id: The equipment the wizard runs under, if any.
        project_path: The absolute project directory, if any (run wizard).
        run_scope: Optional run-scope filter (run templates only).

    Returns:
        The merged, de-duplicated list of templates, nearest scope first.
    """
    # Lazy import to avoid an import cycle: ``ui.pages.templates`` is part of
    # the UI package whose ``mount`` indirectly imports this resolver.
    from exlab_wizard.ui.pages.templates import list_templates

    merged: list[TemplateSummary] = []
    seen: set[str] = set()
    for directory in search_dirs(
        config,
        template_type=template_type,
        equipment_id=equipment_id,
        project_path=project_path,
    ):
        for summary in list_templates(directory, template_type=template_type):
            if summary.name in seen:
                continue
            if not _run_scope_matches(template_type, run_scope, summary.run_scope):
                continue
            seen.add(summary.name)
            merged.append(summary)
    return merged


def reconcile_selection(
    choices: TemplateChoices, selected_name: str | None
) -> tuple[str | None, Path | None, bool]:
    """Reconcile a prior template selection against freshly-resolved choices.

    Called after a wizard re-resolves its template chain (the operator
    changed equipment / project). Returns ``(name, path, dropped)``:

    * The selected name **survives** the new context -> its resolved path is
      **re-derived** from ``choices`` and returned with ``dropped=False``.
      This is the critical case: a same-named per-instance template shadows
      the global one at a *different* path, and a wizard ``ui.select`` that
      keeps its value does not re-fire its change handler -- so the stored
      path must be refreshed here or submit would render the stale source.
    * The selected name is **gone** (or was ``None``) -> returns
      ``(None, None, True)`` so the caller clears the selection and its
      now-orphaned variables.

    Args:
        choices: The freshly-resolved templates for the new context.
        selected_name: The template name selected under the old context.

    Returns:
        ``(name, path, dropped)`` -- the reconciled selection name, its
        re-derived absolute path (or ``None``), and whether the prior
        selection was dropped.
    """
    if selected_name is not None and selected_name in choices.names:
        return selected_name, choices.paths.get(selected_name), False
    return None, None, True


def project_dir(config: Config, equipment_id: str | None, project_name: str | None) -> Path | None:
    """Return the absolute project directory, or ``None`` when not derivable.

    A run's per-project template store lives under
    ``<local_root>/<equipment_id>/<project_name>/`` (Backend Spec §3.2). The
    wizard knows the equipment id and the parent project's folder name, so
    this composes the path the run-wizard resolver passes as
    ``project_path``. Returns ``None`` when ``local_root``, ``equipment_id``,
    or ``project_name`` is missing -- the caller then resolves without the
    per-project scope (per-equipment + global only).
    """
    from pathlib import Path

    local_root = config.paths.local_root
    if not local_root or not equipment_id or not project_name:
        return None
    return Path(local_root) / equipment_id / project_name


def _global_dir(config: Config) -> Path | None:
    """Return the global ``paths.templates_dir`` as a ``Path``, or ``None``.

    ``None`` when the config value is empty, so the caller skips the scope.
    """
    from pathlib import Path

    templates_dir = config.paths.templates_dir
    return Path(templates_dir) if templates_dir else None


def _equipment_dir(config: Config, equipment_id: str | None) -> Path | None:
    """Return ``<local_root>/<equipment_id>`` as a ``Path``, or ``None``.

    ``None`` when ``equipment_id`` is unset or ``local_root`` is empty, so
    the caller skips the per-equipment scope.
    """
    from pathlib import Path

    local_root = config.paths.local_root
    if not equipment_id or not local_root:
        return None
    return Path(local_root) / equipment_id


def _run_scope_matches(
    template_type: str,
    requested_scope: str | None,
    template_scope: str | None,
) -> bool:
    """Return whether a template passes the run-scope filter.

    Only run templates with a requested scope are narrowed. A run
    template is kept when its scope equals the requested scope or is
    :attr:`RunScope.BOTH`. Non-run types and an unset ``requested_scope``
    always pass.
    """
    if template_type != TemplateType.RUN.value or requested_scope is None:
        return True
    return template_scope in (requested_scope, RunScope.BOTH.value)
