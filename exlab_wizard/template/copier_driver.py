"""Copier template-engine wrapper. Backend Spec §4.4.2 / §5.

This module wraps Copier's ``run_copy`` Python API behind the
:class:`TemplateEngine` contract documented in Backend Spec §4.4.2 so
that the rest of the app does not depend on Copier-specific types.

Two operations:

* :meth:`TemplateEngine.resolve` -- read and validate a template's
  ``copier.yml``. Returns a :class:`ResolvedTemplate`. Raises
  :class:`~exlab_wizard.errors.TemplateLoadError` on missing /
  malformed manifests, missing ``_exlab_version``, type mismatch
  against the caller-supplied scope, or invalid ``_exlab_run_scope``.
  Raises :class:`~exlab_wizard.errors.TemplateCoreFieldRedeclaredError`
  when ``_exlab_readme.fields`` declares one of the backend-managed
  core fields (``label`` / ``operator`` / ``objective``) -- see §10.3.

* :meth:`TemplateEngine.render` -- render the template into ``dst``
  by calling Copier under :func:`asyncio.to_thread` (Copier is sync).
  Always passes ``unsafe=False`` per §5.5: any ``_tasks`` in the
  template are silently ignored. Returns a :class:`RenderResult`
  carrying ``dst`` and the list of files Copier created (computed by
  walking ``dst`` before and after the call).

YAML reads use ``yaml.safe_load`` (Backend §4.3 docstring: PyYAML is
reserved for read-only YAML files like ``copier.yml`` where
round-trip preservation is not required).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import copier
import yaml

from exlab_wizard.constants import COPIER_MANIFEST_NAME, RunScope, TemplateType
from exlab_wizard.errors import TemplateCoreFieldRedeclaredError, TemplateLoadError
from exlab_wizard.logging import get_logger

__all__ = [
    "CORE_README_FIELD_IDS",
    "RenderResult",
    "ResolvedTemplate",
    "TemplateEngine",
]

# The three core README field IDs that templates may NOT redeclare.
# Backend Spec §10.3.
CORE_README_FIELD_IDS: frozenset[str] = frozenset({"label", "operator", "objective"})

_log = get_logger(__name__)


@dataclass(frozen=True)
class ResolvedTemplate:
    """A loaded template's metadata. Backend Spec §5.2.

    Attributes:
        name: Template directory name (the ``Path.name`` of the
            template root). Used for display in the wizard.
        path: Absolute path to the template root (the directory
            containing ``copier.yml``).
        exlab_type: One of ``"project"``, ``"equipment"``, or
            ``"run"``. Mirrors ``_exlab_type`` from ``copier.yml``.
        exlab_version: Required non-empty string per §5.7.
        run_scope: One of ``"experimental"``, ``"test"``, ``"both"``;
            populated only for run templates (None otherwise).
        description: Free-form description from
            ``_exlab_description``; defaults to empty string.
        plugin_order: Plugin slug list from ``_exlab_plugins``.
        extra_readme_fields: Field-extension list declared under
            ``_exlab_readme.fields`` (free-form per §10.3).
        raw_manifest: The raw, fully-parsed ``copier.yml`` body.
            Useful for callers that need access to question
            definitions that this dataclass does not normalize.
    """

    name: str
    path: Path
    exlab_type: str
    exlab_version: str
    run_scope: str | None = None
    description: str = ""
    plugin_order: list[str] = field(default_factory=list)
    extra_readme_fields: list[dict[str, Any]] = field(default_factory=list)
    raw_manifest: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RenderResult:
    """Outcome of a render call.

    Attributes:
        dst_path: The absolute destination path Copier wrote into.
        files_written: Files Copier created during the render,
            computed by snapshotting ``dst`` before and after the
            call (so we capture the exact set Copier produced).
    """

    dst_path: Path
    files_written: list[Path] = field(default_factory=list)


class TemplateEngine:
    """Wraps the Copier Python API behind the §4.4.2 contract."""

    def resolve(self, template_path: Path, scope: TemplateType) -> ResolvedTemplate:
        """Load + validate a template's ``copier.yml``.

        Args:
            template_path: Path to the template directory (containing
                ``copier.yml``).
            scope: The caller-asserted template scope. The loaded
                ``_exlab_type`` must match.

        Returns:
            A :class:`ResolvedTemplate` describing the manifest.

        Raises:
            TemplateLoadError: ``copier.yml`` is missing, unreadable,
                or malformed; ``_exlab_version`` is missing or empty;
                ``_exlab_type`` is missing, invalid, or does not match
                ``scope``; or scope is ``run`` and ``_exlab_run_scope``
                is missing or not one of
                ``{"experimental", "test", "both"}``.
            TemplateCoreFieldRedeclaredError: ``_exlab_readme.fields``
                declares any of ``label`` / ``operator`` / ``objective``.
        """
        manifest_path = template_path / COPIER_MANIFEST_NAME

        if not manifest_path.is_file():
            raise TemplateLoadError(
                f"copier.yml not found at {manifest_path}",
            )

        try:
            raw_text = manifest_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise TemplateLoadError(
                f"failed to read {manifest_path}: {exc}",
            ) from exc

        try:
            manifest = yaml.safe_load(raw_text)
        except yaml.YAMLError as exc:
            raise TemplateLoadError(
                f"failed to parse {manifest_path}: {exc}",
            ) from exc

        if manifest is None:
            manifest = {}
        if not isinstance(manifest, dict):
            raise TemplateLoadError(
                f"{manifest_path} did not parse to a mapping",
            )

        # _exlab_type: must be present, valid, and match the caller scope.
        raw_type = manifest.get("_exlab_type")
        if not isinstance(raw_type, str) or not raw_type:
            raise TemplateLoadError(
                f"{manifest_path}: _exlab_type missing or empty",
            )
        try:
            parsed_type = TemplateType(raw_type)
        except ValueError as exc:
            raise TemplateLoadError(
                f"{manifest_path}: _exlab_type must be one of "
                f"{sorted(t.value for t in TemplateType)}, got {raw_type!r}",
            ) from exc
        if parsed_type is not scope:
            raise TemplateLoadError(
                f"{manifest_path}: _exlab_type {parsed_type.value!r} does not "
                f"match requested scope {scope.value!r}",
            )

        # _exlab_version: required non-empty string per §5.7.
        exlab_version = manifest.get("_exlab_version")
        if not isinstance(exlab_version, str) or not exlab_version.strip():
            raise TemplateLoadError(
                f"{manifest_path}: _exlab_version is required and must be a "
                f"non-empty string (§5.7)",
            )

        # _exlab_run_scope: required for run templates, optional otherwise.
        run_scope: str | None = None
        if parsed_type is TemplateType.RUN:
            raw_scope = manifest.get("_exlab_run_scope")
            if not isinstance(raw_scope, str) or not raw_scope:
                raise TemplateLoadError(
                    f"{manifest_path}: _exlab_run_scope is required for run "
                    f"templates and must be one of "
                    f"{sorted(s.value for s in RunScope)}",
                )
            try:
                run_scope = RunScope(raw_scope).value
            except ValueError as exc:
                raise TemplateLoadError(
                    f"{manifest_path}: _exlab_run_scope must be one of "
                    f"{sorted(s.value for s in RunScope)}, got {raw_scope!r}",
                ) from exc

        # _exlab_readme.fields: reject redeclaration of core fields.
        extra_fields = self._extract_readme_fields(manifest, manifest_path)

        # _tasks: silently ignored per §5.5; warn so authors know.
        if "_tasks" in manifest:
            _log.warning(
                "template %s declares _tasks; silently ignored "
                "(unsafe=False, see Backend Spec §5.5)",
                template_path,
            )

        # _exlab_plugins: optional ordered list (§6.2.3).
        raw_plugins = manifest.get("_exlab_plugins")
        plugin_order = list(raw_plugins) if isinstance(raw_plugins, list) else []

        raw_description = manifest.get("_exlab_description")
        description = raw_description if isinstance(raw_description, str) else ""

        return ResolvedTemplate(
            name=template_path.name,
            path=template_path,
            exlab_type=parsed_type.value,
            exlab_version=exlab_version,
            run_scope=run_scope,
            description=description,
            plugin_order=plugin_order,
            extra_readme_fields=extra_fields,
            raw_manifest=manifest,
        )

    @staticmethod
    def _extract_readme_fields(
        manifest: dict[str, Any], manifest_path: Path
    ) -> list[dict[str, Any]]:
        """Pull ``_exlab_readme.fields`` and reject core-field collisions.

        Tolerates malformed shapes by returning an empty list when the
        ``_exlab_readme`` block or its ``fields`` key is not the expected
        shape. Raises :class:`TemplateCoreFieldRedeclaredError` only on
        the one fatal case: a field entry whose ``id`` is one of the
        backend-managed core fields (§10.3).
        """
        readme_block = manifest.get("_exlab_readme")
        if not isinstance(readme_block, dict):
            return []
        raw_fields = readme_block.get("fields")
        if not isinstance(raw_fields, list):
            return []

        dict_entries = [entry for entry in raw_fields if isinstance(entry, dict)]
        for entry in dict_entries:
            field_id = entry.get("id")
            if isinstance(field_id, str) and field_id in CORE_README_FIELD_IDS:
                raise TemplateCoreFieldRedeclaredError(
                    f"{manifest_path}: _exlab_readme.fields redeclares core "
                    f"field {field_id!r}; core fields (label / operator / "
                    f"objective) are backend-managed (§10.3)",
                )
        return dict_entries

    async def render(
        self,
        tpl: ResolvedTemplate,
        dst: Path,
        variables: dict[str, Any],
    ) -> RenderResult:
        """Render the template into ``dst`` via Copier.

        Args:
            tpl: A previously-resolved template.
            dst: Destination directory. Copier creates it if it does
                not exist; it will not silently overwrite existing
                files (``overwrite=False``).
            variables: The fully-resolved answer map. Bypasses
                Copier's interactive prompts (§5.3).

        Returns:
            A :class:`RenderResult` naming ``dst`` and listing the
            files Copier wrote.

        Raises:
            Whatever Copier raises -- e.g. ``copier.errors.UserMessageError``
            when ``overwrite=False`` and a generated file already
            exists. The caller is expected to surface these via the
            §4.6.3 error envelope.
        """
        before = _snapshot_files(dst)

        # §5.5: ExLab-Wizard never executes ``_tasks``. We pass both
        # ``unsafe=False`` (the spec invariant) and ``skip_tasks=True``
        # so Copier silently skips any tasks it finds rather than
        # raising :class:`copier.errors.UnsafeTemplateError`. Together
        # these implement the spec's "silently ignored" contract on
        # current Copier (>=9.x).
        await asyncio.to_thread(
            copier.run_copy,
            src_path=str(tpl.path),
            dst_path=str(dst),
            data=variables,
            overwrite=False,
            unsafe=False,
            skip_tasks=True,
            quiet=True,
        )

        after = _snapshot_files(dst)
        files_written = sorted(after - before)
        return RenderResult(dst_path=dst, files_written=files_written)


def _snapshot_files(root: Path) -> set[Path]:
    """Return the set of regular files under ``root`` (recursively).

    Returns the empty set if ``root`` does not exist. Paths are
    absolute so callers can compare snapshots taken at different
    times without worrying about ``cwd`` drift.
    """
    if not root.exists():
        return set()
    if not root.is_dir():
        # A file at the dst path; treat as a single-entry snapshot.
        return {root.resolve()}
    return {p.resolve() for p in root.rglob("*") if p.is_file()}
