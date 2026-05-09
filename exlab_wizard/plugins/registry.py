"""Plugin registry -- manifest-driven discovery + dispatch resolution.

Backend Spec §6.2.

The registry scans two roots (bundled + lab) for plugin directories,
parses each ``manifest.yml`` without importing any plugin Python code,
validates the manifest against the spec's schema (§6.1.2), enforces the
``api_version`` gate (§6.2.1), enforces the network-opt-in gate
(§6.3.3), and exposes a dispatch surface (``candidates_for``) that
matches files to plugins by extension.

Lab plugins win on name collision with bundled plugins (§6.2.1.4); the
collision is logged so operators can audit which copy is in effect.

This module is host-only: it does not import the plugin's ``Plugin``
class -- that import is deferred to the worker subprocess to preserve
the §6.3 crash-isolation guarantee. ``PluginRecord.plugin_class`` is
therefore typed as ``type | None`` and is only populated by the
``--no-isolation`` test path; production code reads the plugin via the
manifest + source path and lets the worker do the import.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from exlab_wizard.constants import (
    PLUGIN_MANIFEST_NAME,
    PLUGIN_MEMORY_MAX_MB,
    PLUGIN_NAME_PATTERN,
    PLUGIN_SUPPORTED_API_VERSIONS,
    PLUGIN_TIMEOUT_MAX_SECONDS,
    PluginSourceRoot,
)
from exlab_wizard.logging import get_logger

__all__ = [
    "PluginManifest",
    "PluginPlan",
    "PluginRecord",
    "PluginRegistry",
    "RegistryReport",
]

_log = get_logger(__name__)

# Required manifest fields per Backend Spec §6.1.2.
_REQUIRED_MANIFEST_FIELDS: tuple[str, ...] = (
    "name",
    "version",
    "supported_extensions",
    "api_version",
)


@dataclass(frozen=True)
class PluginManifest:
    """Parsed ``manifest.yml`` contents. Backend Spec §6.1.2.

    Mirrors the schema documented in the spec, with default values for
    optional blocks. ``isolation`` is a normalized dict containing
    ``timeout_seconds``, ``memory_mb``, and ``network`` -- the raw YAML
    is rejected if any of these exceed their hard caps.
    """

    name: str
    version: str
    author: str
    description: str
    supported_extensions: list[str]
    api_version: str
    required_variables: list[str] = field(default_factory=list)
    optional_variables: list[str] = field(default_factory=list)
    isolation: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PluginRecord:
    """One plugin in the registry.

    Carries the parsed manifest, the source-on-disk plugin directory, and
    the source root identifier (``BUNDLED`` or ``LAB``) so the
    Settings UI can show where each plugin came from.

    ``plugin_class`` is ``None`` in the production path (the host doesn't
    import plugin code); it's only populated when a caller explicitly
    requests in-process testing via :class:`PluginRegistry` ``--no-isolation``
    helpers (Backend Spec §6.10). Backend Spec §6.2.1.
    """

    manifest: PluginManifest
    plugin_class: type | None
    source_path: Path
    source_root: PluginSourceRoot


@dataclass(frozen=True)
class PluginPlan:
    """One plugin paired with the files in the rendered tree it matches.

    Built by :meth:`PluginRegistry.candidates_for` for each plugin whose
    ``supported_extensions`` matched at least one file.
    """

    record: PluginRecord
    matching_files: list[Path]


@dataclass
class RegistryReport:
    """Outcome of a :meth:`PluginRegistry.reload` pass.

    ``loaded`` is the list of plugin names that survived validation;
    ``rejected`` is a list of ``(name, reason)`` tuples for plugins that
    were skipped, with the reason short enough for a single log line.
    Used by the Settings UI to surface a load summary.
    """

    loaded: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)


class PluginRegistry:
    """Manifest-driven plugin registry.

    Constructed at host startup with the two configured plugin roots; one
    call to :meth:`reload` populates the in-memory record table by
    walking each root, parsing each ``manifest.yml``, and applying the
    validation gates from §6.1.2 / §6.2.1.

    Lab plugins win on name collision with bundled plugins (§6.2.1.4); a
    structured INFO log entry records the override.
    """

    def __init__(
        self,
        bundled_dir: Path | None,
        lab_dir: Path | None,
        supported_api_versions: frozenset[str] = PLUGIN_SUPPORTED_API_VERSIONS,
        allow_network: bool = False,
    ) -> None:
        self._bundled_dir = bundled_dir
        self._lab_dir = lab_dir
        self._supported_api_versions = supported_api_versions
        self._allow_network = allow_network
        self._records: dict[str, PluginRecord] = {}

    # ---------------------------------------------------------------
    # Discovery / reload
    # ---------------------------------------------------------------

    def reload(self) -> RegistryReport:
        """Re-scan both plugin roots and rebuild the in-memory record table.

        Bundled plugins are scanned first; lab plugins are scanned second
        and replace any same-named bundled record. Plugins that fail
        validation (missing manifest, malformed YAML, bad ``api_version``,
        excessive ``isolation`` limits, network-opt-in declined) are
        excluded from the table and added to the report's ``rejected``
        list with a short reason string.

        Backend Spec §6.2.1.
        """
        report = RegistryReport()
        self._records = {}

        # Bundled root first so lab can override it.
        for record, reason in self._iter_root(self._bundled_dir, PluginSourceRoot.BUNDLED):
            self._absorb(record, reason, report)

        for record, reason in self._iter_root(self._lab_dir, PluginSourceRoot.LAB):
            self._absorb(record, reason, report)

        return report

    def _absorb(
        self,
        record: PluginRecord | None,
        reason: tuple[str, str] | None,
        report: RegistryReport,
    ) -> None:
        """Merge one parsed plugin (or one rejection) into the report."""
        if record is None:
            assert reason is not None
            report.rejected.append(reason)
            return
        name = record.manifest.name
        existing = self._records.get(name)
        if (
            existing is not None
            and existing.source_root == PluginSourceRoot.BUNDLED
            and record.source_root == PluginSourceRoot.LAB
        ):
            _log.info(
                "plugin '%s' v%s from %s overrides bundled v%s",
                name,
                record.manifest.version,
                record.source_path,
                existing.manifest.version,
            )
        self._records[name] = record
        if name not in report.loaded:
            report.loaded.append(name)

    def _iter_root(
        self,
        root: Path | None,
        source_root: PluginSourceRoot,
    ) -> list[tuple[PluginRecord | None, tuple[str, str] | None]]:
        """Walk one plugin root and yield ``(record_or_None, reason_or_None)`` pairs.

        Returns a list (rather than a generator) so the reload sequence
        is deterministic and easy to introspect from tests.
        """
        if root is None or not root.is_dir():
            return []
        out: list[tuple[PluginRecord | None, tuple[str, str] | None]] = []
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            record, reason = self._load_plugin(child, source_root)
            out.append((record, reason))
        return out

    def _load_plugin(
        self,
        plugin_dir: Path,
        source_root: PluginSourceRoot,
    ) -> tuple[PluginRecord | None, tuple[str, str] | None]:
        """Parse one plugin directory. Returns ``(record, None)`` or ``(None, (name, reason))``."""
        # Use the directory name as the fallback identifier in the
        # rejection report when the manifest itself is unparseable.
        fallback_name = plugin_dir.name

        manifest_path = plugin_dir / PLUGIN_MANIFEST_NAME
        if not manifest_path.is_file():
            _log.warning("plugin '%s' has no %s -- skipped", fallback_name, PLUGIN_MANIFEST_NAME)
            return (None, (fallback_name, f"missing {PLUGIN_MANIFEST_NAME}"))

        try:
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            _log.warning("plugin '%s' manifest is malformed YAML: %s", fallback_name, exc)
            return (None, (fallback_name, f"malformed manifest: {exc}"))

        if not isinstance(raw, dict):
            _log.warning("plugin '%s' manifest is not a mapping", fallback_name)
            return (None, (fallback_name, "manifest is not a mapping"))

        for f in _REQUIRED_MANIFEST_FIELDS:
            if f not in raw:
                _log.warning("plugin '%s' manifest missing required field '%s'", fallback_name, f)
                return (None, (fallback_name, f"missing required field '{f}'"))

        name = str(raw["name"])
        if not isinstance(raw["name"], str) or not PLUGIN_NAME_PATTERN.match(name):
            _log.warning("plugin '%s' has invalid name", fallback_name)
            return (None, (name or fallback_name, f"invalid name '{name}'"))

        api_version = str(raw["api_version"])
        if api_version not in self._supported_api_versions:
            _log.error(
                "plugin '%s' targets api_version=%s but host supports %s -- skipped",
                name,
                api_version,
                sorted(self._supported_api_versions),
            )
            return (None, (name, f"unsupported api_version '{api_version}'"))

        supported_exts = raw["supported_extensions"]
        if not isinstance(supported_exts, list) or not all(
            isinstance(e, str) for e in supported_exts
        ):
            _log.warning("plugin '%s' supported_extensions must be a list of strings", name)
            return (None, (name, "supported_extensions must be a list of strings"))

        version = str(raw["version"])

        isolation_raw = raw.get("isolation") or {}
        if not isinstance(isolation_raw, dict):
            _log.warning("plugin '%s' isolation block must be a mapping", name)
            return (None, (name, "isolation block must be a mapping"))

        timeout = int(isolation_raw.get("timeout_seconds", 30))
        memory = int(isolation_raw.get("memory_mb", 512))
        network = bool(isolation_raw.get("network", False))

        if timeout > PLUGIN_TIMEOUT_MAX_SECONDS:
            _log.warning(
                "plugin '%s' isolation.timeout_seconds=%d exceeds cap %d -- skipped",
                name,
                timeout,
                PLUGIN_TIMEOUT_MAX_SECONDS,
            )
            return (
                None,
                (
                    name,
                    f"isolation.timeout_seconds={timeout} exceeds cap {PLUGIN_TIMEOUT_MAX_SECONDS}",
                ),
            )

        if memory > PLUGIN_MEMORY_MAX_MB:
            _log.warning(
                "plugin '%s' isolation.memory_mb=%d exceeds cap %d -- skipped",
                name,
                memory,
                PLUGIN_MEMORY_MAX_MB,
            )
            return (
                None,
                (name, f"isolation.memory_mb={memory} exceeds cap {PLUGIN_MEMORY_MAX_MB}"),
            )

        if network and not self._allow_network:
            _log.info(
                "plugin '%s' declares isolation.network=true but allow_network=false -- skipped",
                name,
            )
            return (
                None,
                (name, "network declared but plugins.allow_network=false"),
            )

        manifest = PluginManifest(
            name=name,
            version=version,
            author=str(raw.get("author", "")),
            description=str(raw.get("description", "")),
            supported_extensions=list(supported_exts),
            api_version=api_version,
            required_variables=_as_str_list(raw.get("required_variables")),
            optional_variables=_as_str_list(raw.get("optional_variables")),
            isolation={
                "timeout_seconds": timeout,
                "memory_mb": memory,
                "network": network,
            },
        )

        record = PluginRecord(
            manifest=manifest,
            plugin_class=None,
            source_path=plugin_dir,
            source_root=source_root,
        )
        return (record, None)

    # ---------------------------------------------------------------
    # Lookup surface
    # ---------------------------------------------------------------

    def get(self, name: str) -> PluginRecord | None:
        """Return the record registered under ``name``, or ``None``."""
        return self._records.get(name)

    def list_all(self) -> list[PluginRecord]:
        """Return all registered records, sorted by name for stable output."""
        return [self._records[k] for k in sorted(self._records)]

    def candidates_for(self, file_paths: list[Path]) -> list[PluginPlan]:
        """Resolve plugins that match the given files by extension.

        For each registered plugin, collects the subset of ``file_paths``
        whose suffix matches one of the plugin's
        ``supported_extensions``. Plugins with at least one matching file
        appear in the returned list; the order is alphabetical by plugin
        name (the host applies template-declared ordering on top -- see
        §6.2.3).
        """
        plans: list[PluginPlan] = []
        for record in self.list_all():
            extensions = {e.lower() for e in record.manifest.supported_extensions}
            matches = [p for p in file_paths if _suffix_or_full(p).lower() in extensions]
            if matches:
                plans.append(PluginPlan(record=record, matching_files=matches))
        return plans


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


def _as_str_list(value: Any) -> list[str]:
    """Coerce a YAML node into a ``list[str]``; non-list / non-string entries are dropped."""
    if not isinstance(value, list):
        return []
    return [str(x) for x in value if isinstance(x, str)]


def _suffix_or_full(path: Path) -> str:
    """Return the final suffix (``.xlsx``) for matching against ``supported_extensions``.

    Files without a suffix fall through to the full filename so plugins can
    target dotfile-style names (e.g. ``.gitignore``) by listing the bare
    name in their manifest. This mirrors the "extension or glob" wording
    in §6.1.2.
    """
    suffix = path.suffix
    if suffix:
        return suffix
    return path.name


# Re-export the regex constant from constants.patterns for callers that
# want the raw form (e.g. lint output). The pattern itself is what the
# registry uses to validate plugin names.
_PLUGIN_NAME_REGEX_RAW: str = PLUGIN_NAME_PATTERN.pattern
assert re.match(_PLUGIN_NAME_REGEX_RAW, "valid_name-1") is not None  # sanity guard
