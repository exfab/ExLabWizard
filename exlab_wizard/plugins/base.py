"""Plugin contract base class. Backend Spec §6.1.

Defines the public surface that every plugin author subclasses:

- :class:`Plugin` -- the abstract contract: required methods (``can_handle``,
  ``transform``), optional lifecycle hooks (``pre_transform_all``,
  ``post_transform_all``, ``describe_changes``, ``on_plugin_failure``,
  ``validate_variables``), and the class attributes the host's registry
  cross-checks against ``manifest.yml`` (``name``, ``version``,
  ``supported_extensions``, ``api_version``, ``required_variables``,
  ``optional_variables``).
- :class:`PluginContext` -- the frozen dataclass the host hands the plugin on
  every per-file call (variable map, destination root, answers file, run
  identity, dry-run flag, and the structured log shim).
- :class:`FileChange` -- the per-mutation report shape used by
  :meth:`Plugin.describe_changes` for dry-run previews.
- :class:`PluginError` and :class:`PluginInputRequired` -- re-exports of the
  canonical hierarchy from :mod:`exlab_wizard.errors`. Plugin authors import
  the names from this module for convenience; the host catches them by their
  canonical class so the two views point at the same exception object.

Note (v0.7): the legacy ``transform_readme`` hook is intentionally omitted.
README mutation is post-plugin and non-pluggable -- see Backend Spec §6.1.5
("What plugins must not touch") and §10.8.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

# Re-export the canonical error classes so plugin authors can write
# ``from exlab_wizard.plugins.base import PluginError`` (or, more commonly,
# ``from exlab_wizard.plugins import PluginError`` once the package
# ``__init__`` re-exports them) without reaching into the errors module.
from exlab_wizard.errors import PluginError, PluginInputRequired

if TYPE_CHECKING:
    from exlab_wizard.plugins.logger import PluginLogger

__all__ = [
    "FileChange",
    "Plugin",
    "PluginContext",
    "PluginError",
    "PluginInputRequired",
]


@dataclass(frozen=True)
class FileChange:
    """A single mutation a plugin would make. Used by ``describe_changes``.

    Backend Spec §6.1.3.

    Attributes:
        path: Absolute path under the rendered destination directory.
        kind: One of ``"modify"``, ``"create"``, ``"rename"``, ``"delete"``.
        summary: One-line human-readable description for UI display.
        detail: Optional structured payload for richer dry-run previews
            (e.g. ``{"writes": [{"cell": "B7", "value": "asmith"}]}``).
    """

    path: Path
    kind: str
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PluginContext:
    """Read-only context handed to every plugin lifecycle hook.

    Constructed once per creation session by the host and passed in across
    the IPC boundary. Plugins read from it but MUST NOT mutate it (the
    dataclass is frozen as a defensive measure -- mutation attempts raise
    :class:`dataclasses.FrozenInstanceError`).

    Backend Spec §6.1.4.
    """

    variables: dict[str, Any]
    dst_root: Path
    answers_file: Path
    template_name: str
    template_version: str
    run_kind: str
    equipment_id: str
    project: str
    dry_run: bool
    log: PluginLogger


class Plugin(ABC):
    """Abstract base class for all ExLab-Wizard plugins.

    Backend Spec §6.1.3.

    Lifecycle (one instance per creation session, all in the worker
    subprocess):

    - ``__init__()`` -- cheap construction; no I/O.
    - ``validate_variables(variables)`` -- called once at registration in
      a short-lived validation worker.
    - ``pre_transform_all(ctx)`` -- called once before the file loop.
    - For each matched file:

      * ``can_handle(file, variables)`` -- cheap predicate.
      * ``describe_changes(file, ctx)`` -- only invoked in dry-run mode.
      * ``transform(file, ctx)`` -- the actual mutation.

    - ``post_transform_all(ctx)`` -- called once after the file loop.
    - ``on_plugin_failure(exc, ctx)`` -- called only if any other hook
      raised.
    """

    # --- Required class attributes (mirror manifest.yml; the host
    # cross-checks). The ``ClassVar`` annotation tells type-checkers and
    # ruff that these are class-level configuration shared across
    # instances, not per-instance state with a mutable default. ----------
    name: ClassVar[str]
    version: ClassVar[str]
    supported_extensions: ClassVar[list[str]]
    api_version: ClassVar[str] = "1"

    # --- Optional class attributes. --------------------------------------
    required_variables: ClassVar[list[str]] = []
    optional_variables: ClassVar[list[str]] = []

    # --- Required methods. -----------------------------------------------

    @abstractmethod
    def can_handle(self, file_path: Path, variables: dict[str, Any]) -> bool:
        """Secondary filter, called after the extension match.

        Cheap and side-effect-free. Returning ``False`` means this file is
        skipped for this plugin only -- other plugins still get a chance.
        """

    @abstractmethod
    def transform(self, file_path: Path, ctx: PluginContext) -> None:
        """Mutate ``file_path`` in place.

        On unrecoverable failure raise :class:`PluginError` with a
        human-readable message. On a discovered need for additional input,
        raise :class:`PluginInputRequired`. Return value is ignored.
        """

    # --- Optional lifecycle hooks (default to no-ops). -------------------

    def validate_variables(self, variables: dict[str, Any]) -> list[str]:
        """Return a list of error strings; empty list means "valid".

        Default implementation reports any of ``self.required_variables``
        that are missing from ``variables`` or are present but empty.
        Plugin authors override only to add bespoke checks (date-format,
        equipment-allowlist, etc.); the override should call ``super()``
        first to preserve the missing-required check.

        Backend Spec §6.1.3 (variable validation in worker, not host).
        """
        return [
            f"required variable '{v}' is missing or empty"
            for v in self.required_variables
            if not variables.get(v)
        ]

    def pre_transform_all(self, ctx: PluginContext) -> None:  # noqa: B027 -- intentional optional hook
        """Called once before the file loop. Default: no-op.

        Use for batch setup that should be paid once per session (e.g.
        opening a workbook, opening a DB connection). State stored on
        ``self`` is preserved through the loop because the worker holds
        one instance for the whole session.
        """

    def post_transform_all(self, ctx: PluginContext) -> None:  # noqa: B027 -- intentional optional hook
        """Called once after the file loop. Default: no-op.

        Symmetric to :meth:`pre_transform_all` -- close handles, flush
        buffers, etc. Not called if ``pre_transform_all`` itself raised.
        """

    def describe_changes(self, file_path: Path, ctx: PluginContext) -> list[FileChange]:
        """Return the dry-run preview for what ``transform`` would do.

        Default returns a single ``"modify"`` :class:`FileChange` with no
        detail; plugins should override when the user-facing preview
        matters (e.g. listing the cells that would be written).
        """
        return [
            FileChange(
                path=file_path,
                kind="modify",
                summary=f"{self.name} would modify {file_path.name}",
            )
        ]

    def on_plugin_failure(self, exc: Exception, ctx: PluginContext) -> None:  # noqa: B027 -- intentional optional hook
        """Called if any prior hook raised. Default: no-op.

        Use to roll back partial state (delete a half-written sidecar
        file, restore a backup, close a leaked handle). The exception
        that caused the failure is passed in; the plugin MUST NOT
        re-raise. Returning normally means cleanup succeeded; raising
        means the cleanup itself failed and will be logged separately.
        """
