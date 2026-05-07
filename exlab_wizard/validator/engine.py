"""Validator engine. Backend Spec §4.4.4, §8.1, §11.7, §11.8.

The engine is the single component that implements the rules in §8.1 and
runs in two modes against the same rule set:

* **Creation-time mode** (:meth:`Validator.validate_creation`) -- input
  is a resolved destination path, a resolved variable map, and the
  post-render content of files about to be written. Output is a flat
  list of :class:`Finding` instances. The controller raises a
  :class:`~exlab_wizard.errors.ValidationError` containing this list
  when any hard-tier finding fires (§8 bullet "Validation"). This mode
  does not touch the disk; it dispatches to the pure rule-check helpers
  in :mod:`exlab_wizard.validator.rules`.
* **Audit mode** (:meth:`Validator.audit`) -- walks a directory subtree
  under the managed ``local_root`` (and ``staging_root`` when
  orchestrator mode is on; §11.8). Output is a flat list of
  :class:`Finding` instances sorted by
  ``(tier desc, rule, offending_path)``. Reads ``creation.json`` per
  directory via ``msgspec.json.decode``; bounded text-file content scans
  per :attr:`ValidatorConfig.content_scan_max_mib` and
  :attr:`ValidatorConfig.content_scan_extensions`; binary files always
  skipped via the 8-KiB null-byte sniff (§8.1.1).
* :meth:`Validator.query_problems` -- public read-only alias for
  :meth:`Validator.audit` that satisfies the §11.8 problem-query
  contract. Does not mutate ``creation.json``, does not write log
  entries, does not initiate sync.

Performance commitments (§4.5, §11.8):

* The directory walk uses ``os.scandir`` (NOT ``pathlib.Path.rglob``).
  ``DirEntry.is_dir()`` / ``is_file()`` are cached from the iteration,
  so the walk avoids per-entry ``stat()`` syscalls.
* ``creation.json`` is decoded via ``msgspec.json.decode``.
* Regex patterns are pre-compiled at module load (``constants/patterns.py``).
* Pattern matching uses stdlib ``re`` only (no ``hyperscan``,
  ``ripgrep``) so the §11.8 determinism contract holds across hosts.

Determinism (§11.8). The same inputs always produce the same finding
list in the same order. The constructor accepts a
:class:`~exlab_wizard.config.models.ValidatorConfig` so the per-lab
content-scan tuning (size cap, extension list) is captured as part of
the input contract; if no config is supplied the engine uses the
documented defaults from §9.

Sort order. The engine returns findings sorted by ``(tier, rule,
offending_path)``. ``tier`` is sorted with ``"hard"`` before ``"soft"``
(matching the §11.8 contract that hard-tier findings appear first in
the Problems tab). ``rule`` and ``offending_path`` are sorted
lexicographically. The ordering is total: two findings with identical
``(tier, rule, offending_path)`` are equal under the comparator, but
the underlying list keeps insertion order via a stable sort.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any, Literal, TypedDict

import msgspec

from exlab_wizard.api.schemas import (
    CreationJson,
    ReadmeFieldsJson,
    msgspec_json,
)
from exlab_wizard.cache.creation_writer import select_active_overrides
from exlab_wizard.config.models import ValidatorConfig
from exlab_wizard.constants import (
    CACHE_DIR_NAME,
    CREATION_JSON_NAME,
    README_FIELDS_JSON_NAME,
    README_FILE_NAME,
    RUN_DIR_PREFIX,
    TEST_RUN_DIR_PREFIX,
    TEST_RUNS_DIR_NAME,
    VALIDATOR_BINARY_DETECT_BYTES,
    RunKind,
    SyncStatus,
    Tier,
)
from exlab_wizard.logging import get_logger
from exlab_wizard.validator import rules
from exlab_wizard.validator.findings import Finding

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "AuditScope",
    "AuditScopeAll",
    "AuditScopeEquipment",
    "AuditScopeProject",
    "CreationValidationInput",
    "Validator",
]


_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Input bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreationValidationInput:
    """Input bundle for creation-time validation. Backend Spec §8.1, §11.8.

    All fields are positional-or-keyword. The dataclass is ``frozen``
    so callers cannot mutate the bundle between dispatch passes; this
    matches the determinism contract (§11.8).

    Attributes:
        proposed_path: The destination path the creation controller is
            about to write to. Used to derive the per-segment lists for
            the path-segment rules. Accepts ``/`` and ``\\`` as
            separators (the splitter handles both).
        variables: The resolved Copier variable dict. Keys are the
            template question IDs (lower-snake) and values are the
            resolved values. Reserved for downstream rules; not directly
            consumed by the §8.1 rule set today.
        file_names: File names that will be written into the destination.
            Bare names without directory components.
        file_contents: Post-render content for files about to be written
            (text only; binaries excluded by the caller). Keys are the
            same names as ``file_names`` for the entries that have
            content.
        run_kind: ``"experimental"`` or ``"test"``; mirrors the
            ``creation.json`` ``run_kind`` value.
        template_required_field_ids: README field ids the template
            marks required (parsed from ``copier.yml`` ``_exlab_*``
            metadata).
        config_required_field_ids: README field ids ``config.yaml``
            ``readme.defaults`` marks required.
        readme_fields: The merged readme_fields_json dict the controller
            is about to write. Used by the missing-required-field rule.
    """

    proposed_path: str
    variables: Mapping[str, object] = field(default_factory=dict)
    file_names: tuple[str, ...] = ()
    file_contents: Mapping[str, str] = field(default_factory=dict)
    run_kind: str = RunKind.EXPERIMENTAL.value
    template_required_field_ids: tuple[str, ...] = ()
    config_required_field_ids: tuple[str, ...] = ()
    readme_fields: Mapping[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Path splitting
# ---------------------------------------------------------------------------


def _split_path_segments(proposed_path: str) -> list[str]:
    """Split ``proposed_path`` into per-segment names.

    Accepts both POSIX and Windows-style separators. UNC roots
    (``\\\\server\\share``) are kept as a single segment so the share
    name does not get scanned as a content directory. Empty segments
    (e.g. trailing ``/``) are dropped.

    The split is a string-only operation -- the path does not have to
    exist, which matches creation-time mode (§11.8).
    """
    if not proposed_path:
        return []
    # Normalize separators: treat both forms as separators on every OS so
    # the splitter is portable and deterministic. Walking the string
    # manually (rather than calling ``Path``) keeps the result identical
    # across hosts; ``pathlib`` would consult the local platform.
    if "\\" in proposed_path and "/" not in proposed_path:
        parts = PureWindowsPath(proposed_path).parts
    else:
        parts = PurePosixPath(proposed_path.replace("\\", "/")).parts
    # ``parts`` for absolute POSIX paths starts with ``/``; we drop it so
    # downstream rules do not scan a single-character segment.
    return [p for p in parts if p and p not in ("/", "\\")]


# ---------------------------------------------------------------------------
# AuditScope (§11.8)
# ---------------------------------------------------------------------------


class AuditScopeEquipment(TypedDict):
    """Audit one equipment subtree. Spec §11.8.

    The ``value`` is the equipment ID (matched against the configured
    ``equipment[].id`` list); the engine resolves it to the equipment's
    ``local_root`` via the equipment-config map handed to the
    constructor.
    """

    kind: Literal["equipment_id"]
    value: str


class AuditScopeProject(TypedDict):
    """Audit one ``<equipment>/<project>`` subtree. Spec §11.8.

    The ``value`` is an absolute project-level directory path. Useful
    for the per-project Problems tab view (Frontend §3.8).
    """

    kind: Literal["project_path"]
    value: str


class AuditScopeAll(TypedDict):
    """Audit every configured equipment + staging when orchestrator on.

    Spec §11.8. The ``value`` field is omitted; the constant ``kind``
    of ``"all"`` is the discriminator.
    """

    kind: Literal["all"]


# Closed union of the three scope shapes the §11.8 contract accepts.
AuditScope = AuditScopeEquipment | AuditScopeProject | AuditScopeAll


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class Validator:
    """Run §8.1 rules in creation-time and audit modes. Backend Spec §11.8.

    The constructor accepts a :class:`ValidatorConfig` -- the §9
    ``validator`` block -- so callers can tune the content-scan size cap
    and extension list. The default constructs a fresh
    :class:`ValidatorConfig` with the §9 defaults
    (``content_scan_max_mib=5`` and the canonical extension list).

    Audit-mode callers also pass the equipment-roots map (mapping
    ``equipment_id -> absolute equipment directory``) and an optional
    ``staging_root``. These default to empty when audit mode is not in
    use; creation-time-only callers can omit them.
    """

    def __init__(
        self,
        validator_config: ValidatorConfig | None = None,
        *,
        equipment_roots: Mapping[str, Path] | None = None,
        staging_root: Path | None = None,
    ) -> None:
        self._config = validator_config or ValidatorConfig()
        self._equipment_roots: dict[str, Path] = dict(equipment_roots) if equipment_roots else {}
        self._staging_root = staging_root
        self._content_scan_max_bytes: int = self._config.content_scan_max_mib * 1024 * 1024
        self._content_scan_extensions: frozenset[str] = frozenset(
            ext.lower() for ext in self._config.content_scan_extensions
        )

    @property
    def config(self) -> ValidatorConfig:
        """The :class:`ValidatorConfig` this engine instance was built with.

        Exposed read-only so audit-mode helpers (Agent C) can consult
        the same content-scan limits as the creation-time pass.
        """
        return self._config

    # ---------------------------------------------------------------- API

    def validate_creation(self, params: CreationValidationInput) -> list[Finding]:
        """Run every §8.1 creation-time rule against ``params``.

        Returns a flat list of :class:`Finding` instances sorted by
        ``(tier, rule, offending_path)`` with hard-tier findings first.

        Dispatch order (each helper returns ``list[dict]`` in the
        rules-module contract; the engine stamps each dict with the
        common :class:`Finding` fields the helper does not know):

        1. ``check_unresolved_placeholder`` -- against path segments,
           file names, and the file contents map. Markdown front-matter
           extraction happens inside the rule helper.
        2. ``check_illegal_filesystem_character`` -- against path
           segments and file names.
        3. ``check_reserved_filesystem_name`` -- against file names
           (Windows reserved-name set; case-insensitive).
        4. ``check_mode_prefix_mismatch`` -- against the leaf and
           parent of the proposed path, with the declared ``run_kind``.
        5. ``check_missing_required_field`` -- against the merged
           readme_fields dict and the union of required IDs from the
           template + config layers.
        6. ``check_malformed_yaml_front_matter`` -- against
           ``file_contents['README.md']`` if present.

        The orphan rule (§8.1.4) is **not** dispatched here -- it is an
        audit-mode rule by spec. The mode-prefix mismatch rule is the
        only one of the seven that consults ``run_kind``; everything
        else is structural.
        """
        path_segments = _split_path_segments(params.proposed_path)
        leaf = path_segments[-1] if path_segments else ""
        parent = path_segments[-2] if len(path_segments) >= 2 else ""

        # The §8.1.5 missing-required-field rule wants the union of the
        # two required-id sources (template + config). The two layers
        # are kept separate in the input bundle so the rule can attribute
        # the source layer in its ``rule_detail``; the helper takes a
        # single combined list for simplicity, with deduplication.
        required_field_ids = tuple(
            dict.fromkeys((*params.template_required_field_ids, *params.config_required_field_ids))
        )

        raw_findings: list[dict] = []

        raw_findings.extend(
            rules.check_unresolved_placeholder(
                path_segments=path_segments,
                file_names=list(params.file_names),
                file_contents=dict(params.file_contents),
            )
        )
        raw_findings.extend(
            rules.check_illegal_filesystem_character(
                path_segments=path_segments,
                file_names=list(params.file_names),
            )
        )
        raw_findings.extend(
            rules.check_reserved_filesystem_name(
                file_names=list(params.file_names),
            )
        )
        raw_findings.extend(
            rules.check_mode_prefix_mismatch(
                leaf_dir_name=leaf,
                parent_dir_name=parent,
                creation_run_kind=params.run_kind,
            )
        )
        raw_findings.extend(
            rules.check_missing_required_field(
                readme_fields=dict(params.readme_fields),
                required_field_ids=list(required_field_ids),
            )
        )
        readme_content = params.file_contents.get(README_FILE_NAME)
        if readme_content is not None:
            raw_findings.extend(rules.check_malformed_yaml_front_matter(content=readme_content))

        run_path = params.proposed_path
        findings = [self._materialise(raw, run_path=run_path) for raw in raw_findings]

        if findings:
            _log.debug(
                "validate_creation: %d finding(s) for %s",
                len(findings),
                run_path,
            )

        return sorted(findings, key=_finding_sort_key)

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _materialise(raw: dict, *, run_path: str) -> Finding:
        """Wrap a rule-helper dict in a :class:`Finding`.

        The §8.1 rule helpers return rule-specific ``dict`` payloads.
        Every helper supplies ``rule``, ``tier``, ``offending_path``,
        ``offending_kind``, ``matched_token``, and ``rule_detail``; the
        engine stamps ``run_path`` (which the helper cannot know -- it
        is the destination path of the proposed creation) and the two
        audit-mode flags (``synced_under_prior_policy``,
        ``override_active``). Both flags default to ``False`` for
        creation-time findings -- there is no synced run yet at this
        point in the lifecycle and overrides apply only in audit mode.
        """
        return Finding(
            rule=raw["rule"],
            tier=raw["tier"],
            run_path=run_path,
            offending_path=raw.get("offending_path", run_path),
            offending_kind=raw["offending_kind"],
            matched_token=raw.get("matched_token"),
            rule_detail=raw.get("rule_detail", ""),
            synced_under_prior_policy=False,
            override_active=False,
        )

    # ---------------------------------------------------------------------
    # Audit mode (§11.8)
    # ---------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: Any) -> Validator:
        """Build a :class:`Validator` from the full ``config.yaml`` model.

        Projects the relevant fields out of
        :class:`exlab_wizard.config.models.Config` so the engine is not
        coupled to the entire config schema. Used by the FastAPI
        lifespan when wiring the audit task.
        """
        equipment_roots: dict[str, Path] = {}
        for entry in getattr(config, "equipment", []) or []:
            equipment_roots[entry.id] = Path(entry.local_root) / entry.id
        staging_root: Path | None = None
        orch = getattr(config, "orchestrator", None)
        if orch is not None and getattr(orch, "enabled", False):
            staging_root_value = getattr(orch, "staging_root", "")
            if staging_root_value:
                staging_root = Path(staging_root_value)
        return cls(
            validator_config=getattr(config, "validator", None),
            equipment_roots=equipment_roots,
            staging_root=staging_root,
        )

    def audit(self, scope: AuditScope) -> list[Finding]:
        """Walk a directory subtree and return all findings.

        Backend Spec §11.8. Uses ``os.scandir`` (NOT ``pathlib.rglob``)
        per Backend §4.5. Reads ``creation.json`` via
        ``msgspec.json.decode(..., type=CreationJson)`` where present.
        Bounded text-file content scan via ``content_scan_max_mib`` and
        ``content_scan_extensions``. Binary files are always skipped via
        the 8-KiB null-byte sniff (§8.1.1).

        ``scope`` is one of:

        - ``{"kind": "equipment_id", "value": "<id>"}`` -- one
          equipment subtree (resolved via the equipment-roots map
          handed to the constructor).
        - ``{"kind": "project_path", "value": "<absolute path>"}`` --
          one project subtree.
        - ``{"kind": "all"}`` -- every configured equipment plus the
          staging root when orchestrator is on.

        Returns a :class:`Finding` list sorted by
        ``(tier desc, rule, offending_path)``. The list is
        deterministic across repeated calls with the same fixture: a
        contract pinned by ``test_validator_determinism.py``.
        """
        roots = self._resolve_scope_roots(scope)
        findings: list[Finding] = []
        for root in roots:
            findings.extend(self._walk_root(root))
        return sorted(findings, key=_finding_sort_key)

    def query_problems(self, scope: AuditScope) -> list[Finding]:
        """Public read-only alias for :meth:`audit`.

        Backend Spec §11.8. Read-only: does not mutate ``creation.json``,
        does not write log entries, does not initiate sync. The GUI's
        per-row actions (mark-as-known, override) call dedicated
        mutation endpoints rather than this method.
        """
        return self.audit(scope)

    # -- Audit: scope resolution ------------------------------------------

    def _resolve_scope_roots(self, scope: AuditScope) -> list[Path]:
        """Map an :class:`AuditScope` onto the directory roots to walk."""
        kind = scope["kind"]
        if kind == "equipment_id":
            equipment_id = scope["value"]  # type: ignore[typeddict-item]
            root = self._equipment_roots.get(equipment_id)
            return [root] if root is not None else []
        if kind == "project_path":
            return [Path(scope["value"])]  # type: ignore[typeddict-item]
        if kind == "all":
            roots = list(self._equipment_roots.values())
            if self._staging_root is not None:
                roots.append(self._staging_root)
            return roots
        msg = f"unknown audit scope kind: {kind!r}"
        raise ValueError(msg)

    # -- Audit: directory walk --------------------------------------------

    def _walk_root(self, root: Path) -> list[Finding]:
        """Walk a single subtree rooted at ``root`` (depth-first).

        Returns a flat list of findings. The walk is a depth-first
        ``os.scandir`` traversal that skips ``.exlab-wizard`` cache
        directories (their contents are read directly per directory
        rather than walked through the §8.1 rules).
        """
        if not root.exists() or not root.is_dir():
            return []
        equipment_root_abs = str(root.resolve())
        findings: list[Finding] = []
        self._walk_dir(
            current=root,
            equipment_root_abs=equipment_root_abs,
            findings=findings,
        )
        return findings

    def _walk_dir(
        self,
        *,
        current: Path,
        equipment_root_abs: str,
        findings: list[Finding],
    ) -> None:
        """Recursive ``os.scandir`` walk; apply rules at every level.

        Per-directory steps:

        1. Classify the level by directory shape relative to
           ``equipment_root_abs`` (equipment / project / run / TestRuns
           marker / nested sub-folder).
        2. Read ``creation.json`` (typed decode) when present at this
           level.
        3. Apply the §8.1 rules whose scope is the directory itself
           (orphan, mode-prefix mismatch, missing-required-field).
        4. For each file child, apply filename + content-scan rules.
        5. For each directory child, apply directory-name rules then
           recurse, skipping ``.exlab-wizard`` cache dirs.
        """
        level = self._classify_level(current, equipment_root_abs)
        creation_payload, creation_raw = self._read_creation_for(current)
        run_path_str = self._compute_run_path(current, level, equipment_root_abs)

        if level in {"project", "run", "test_run"}:
            self._apply_directory_rules(
                current=current,
                level=level,
                creation_payload=creation_payload,
                creation_raw=creation_raw,
                run_path_str=run_path_str,
                findings=findings,
            )

        try:
            entries = list(os.scandir(current))
        except (FileNotFoundError, PermissionError):
            return

        for entry in entries:
            entry_name = entry.name
            if entry_name == CACHE_DIR_NAME:
                # Cache contents are read above via the typed decoder;
                # do not re-walk them through the §8.1 rules.
                continue
            entry_path = Path(entry.path)
            if entry.is_file(follow_symlinks=False):
                self._apply_file_rules(
                    file_entry_path=entry_path,
                    file_name=entry_name,
                    run_path_str=run_path_str,
                    creation_payload=creation_payload,
                    findings=findings,
                )
            elif entry.is_dir(follow_symlinks=False):
                self._apply_directory_name_rules(
                    dir_path=entry_path,
                    dir_name=entry_name,
                    run_path_str=run_path_str,
                    creation_payload=creation_payload,
                    findings=findings,
                )
                self._walk_dir(
                    current=entry_path,
                    equipment_root_abs=equipment_root_abs,
                    findings=findings,
                )

    def _classify_level(
        self,
        directory: Path,
        equipment_root_abs: str,
    ) -> Literal["equipment", "project", "run", "test_run", "test_runs", "other"]:
        """Classify a directory's role in an equipment subtree.

        Equipment root is depth 0; first child is the project (depth 1);
        the next level depends on the shape:

        - ``Run_*`` -> ``"run"``
        - ``TestRuns`` -> ``"test_runs"`` (the marker folder)
        - ``TestRuns/TestRun_*`` -> ``"test_run"``
        - anything else (depth >= 2 not matching the patterns) ->
          ``"other"`` (unmanaged sub-folder under a project / run)
        """
        try:
            rel = directory.resolve().relative_to(equipment_root_abs)
        except ValueError:
            return "other"
        parts = rel.parts
        if len(parts) == 0:
            return "equipment"
        if len(parts) == 1:
            return "project"
        if len(parts) == 2:
            name = parts[1]
            if name == TEST_RUNS_DIR_NAME:
                return "test_runs"
            if name.startswith(RUN_DIR_PREFIX):
                return "run"
            return "other"
        if len(parts) == 3 and parts[1] == TEST_RUNS_DIR_NAME:
            if parts[2].startswith(TEST_RUN_DIR_PREFIX):
                return "test_run"
            return "other"
        return "other"

    def _compute_run_path(
        self,
        directory: Path,
        level: str,
        equipment_root_abs: str,
    ) -> str:
        """Return the §11.8 ``run_path`` for findings at ``directory``.

        Per spec, ``run_path`` is the run-level directory ancestor;
        for orphans at project level it is the project directory
        itself; at equipment level it is the equipment root. For
        nested ``"other"`` sub-folders the closest run / project
        ancestor on the way down is returned.
        """
        if level in {"run", "test_run", "test_runs", "project"}:
            return str(directory)
        if level == "equipment":
            return equipment_root_abs
        # ``other``: resolve up to the nearest run / project segment.
        try:
            rel = directory.resolve().relative_to(equipment_root_abs)
        except ValueError:
            return str(directory)
        parts = rel.parts
        if len(parts) >= 3 and parts[1] == TEST_RUNS_DIR_NAME:
            return str(Path(equipment_root_abs) / Path(*parts[:3]))
        if len(parts) >= 2 and parts[1].startswith(RUN_DIR_PREFIX):
            return str(Path(equipment_root_abs) / Path(*parts[:2]))
        if len(parts) >= 1:
            return str(Path(equipment_root_abs) / parts[0])
        return equipment_root_abs

    # -- Audit: rule application ------------------------------------------

    def _apply_directory_rules(
        self,
        *,
        current: Path,
        level: str,
        creation_payload: CreationJson | None,
        creation_raw: dict[str, Any] | None,
        run_path_str: str,
        findings: list[Finding],
    ) -> None:
        """Apply rules whose scope is the run-level directory itself.

        Three rule families fire here:

        - orphan (when ``creation.json`` is absent at project / run level).
        - mode-prefix mismatch (when ``creation.json`` is present at the
          run level).
        - missing-required-field (when ``readme_fields.json`` exists and
          the configured layer flags required IDs).

        The leaf directory's own name is also pushed through the
        directory-name rules (placeholder + illegal char) so that a
        violation in the run leaf is reported -- the parent's walk
        loop applies those rules to children but not to itself.
        """
        active_classes, sync_status = self._extract_overrides_and_sync(creation_payload)
        current_str = str(current)

        target_orphan_level = _level_for_orphan(level)
        if target_orphan_level is not None:
            self._extend_findings(
                rules.check_orphan(
                    level=target_orphan_level,
                    has_creation_json=creation_payload is not None,
                ),
                findings=findings,
                run_path_str=run_path_str,
                offending_path_override=current_str,
                active_classes=active_classes,
                sync_status=sync_status,
            )

        if level in {"run", "test_run"} and creation_payload is not None:
            parent_name = current.parent.name if current.parent != current else None
            self._extend_findings(
                rules.check_mode_prefix_mismatch(
                    leaf_dir_name=current.name,
                    parent_dir_name=parent_name,
                    creation_run_kind=creation_payload.run_kind,
                ),
                findings=findings,
                run_path_str=run_path_str,
                offending_path_override=current_str,
                active_classes=active_classes,
                sync_status=sync_status,
            )

        if level in {"run", "test_run", "project"}:
            self._apply_missing_required_field_rule(
                current=current,
                creation_raw=creation_raw,
                run_path_str=run_path_str,
                active_classes=active_classes,
                sync_status=sync_status,
                findings=findings,
            )

        # Directory-name rules on the leaf itself.
        self._apply_directory_name_rules(
            dir_path=current,
            dir_name=current.name,
            run_path_str=run_path_str,
            creation_payload=creation_payload,
            findings=findings,
        )

    def _extend_findings(
        self,
        raw_findings: list[dict[str, Any]],
        *,
        findings: list[Finding],
        run_path_str: str,
        offending_path_override: str,
        active_classes: set[str],
        sync_status: str | None,
    ) -> None:
        """Materialise raw rule output into :class:`Finding` instances.

        Reduces audit-mode call-site boilerplate: every rule helper
        returns a ``list[dict]`` of the same shape, and every audit
        finding needs the same five fields stamped on it.
        """
        for raw in raw_findings:
            findings.append(
                self._materialise_audit(
                    raw=raw,
                    run_path_str=run_path_str,
                    offending_path_override=offending_path_override,
                    active_classes=active_classes,
                    sync_status=sync_status,
                )
            )

    def _apply_missing_required_field_rule(
        self,
        *,
        current: Path,
        creation_raw: dict[str, Any] | None,
        run_path_str: str,
        active_classes: set[str],
        sync_status: str | None,
        findings: list[Finding],
    ) -> None:
        """Read ``readme_fields.json`` and call ``check_missing_required_field``.

        The required-field list is sourced from the ``creation.json``
        wire dict's ``required_readme_field_ids`` extra (a writer
        convention -- callers that don't stamp the field get no
        findings). The rule itself is soft-tier so an absent layer is
        not a bug.
        """
        readme_path = current / CACHE_DIR_NAME / README_FIELDS_JSON_NAME
        if not readme_path.exists():
            return
        try:
            readme_payload = msgspec_json.decode(readme_path.read_bytes(), type=ReadmeFieldsJson)
        except (msgspec.DecodeError, msgspec.ValidationError):
            _log.debug("readme_fields.json failed typed decode: %s", readme_path)
            return

        required_ids: list[str] = []
        if isinstance(creation_raw, dict):
            extra = creation_raw.get("required_readme_field_ids")
            if isinstance(extra, list):
                required_ids = [str(x) for x in extra]
        if not required_ids:
            return

        readme_dict = msgspec.to_builtins(readme_payload)
        self._extend_findings(
            rules.check_missing_required_field(
                readme_fields=readme_dict,
                required_field_ids=required_ids,
            ),
            findings=findings,
            run_path_str=run_path_str,
            offending_path_override=str(readme_path),
            active_classes=active_classes,
            sync_status=sync_status,
        )

    def _apply_directory_name_rules(
        self,
        *,
        dir_path: Path,
        dir_name: str,
        run_path_str: str,
        creation_payload: CreationJson | None,
        findings: list[Finding],
    ) -> None:
        """Apply name-level rules (placeholder + illegal char) to a directory.

        Reserved-name and content-scan rules do not apply to directory
        names (the spec wires reserved names to file names only and
        content scans to file content only); the placeholder and
        illegal-character rules apply to every directory segment.
        """
        active_classes, sync_status = self._extract_overrides_and_sync(creation_payload)
        dir_path_str = str(dir_path)

        self._extend_findings(
            rules.check_unresolved_placeholder(
                path_segments=[dir_name],
                file_names=[],
                file_contents={},
            ),
            findings=findings,
            run_path_str=run_path_str,
            offending_path_override=dir_path_str,
            active_classes=active_classes,
            sync_status=sync_status,
        )
        self._extend_findings(
            rules.check_illegal_filesystem_character(
                path_segments=[dir_name],
                file_names=[],
            ),
            findings=findings,
            run_path_str=run_path_str,
            offending_path_override=dir_path_str,
            active_classes=active_classes,
            sync_status=sync_status,
        )

    def _apply_file_rules(
        self,
        *,
        file_entry_path: Path,
        file_name: str,
        run_path_str: str,
        creation_payload: CreationJson | None,
        findings: list[Finding],
    ) -> None:
        """Apply file-name + file-content rules to a single file.

        Filename rules (placeholder, illegal char, reserved name) fire
        on every file. Content scans are gated by
        :meth:`_content_scan_eligible` (extension + size cap) and the
        8-KiB null-byte sniff for binary detection.
        """
        active_classes, sync_status = self._extract_overrides_and_sync(creation_payload)
        file_path_str = str(file_entry_path)

        # Filename rules.
        self._extend_findings(
            rules.check_unresolved_placeholder(
                path_segments=[],
                file_names=[file_name],
                file_contents={},
            ),
            findings=findings,
            run_path_str=run_path_str,
            offending_path_override=file_path_str,
            active_classes=active_classes,
            sync_status=sync_status,
        )
        self._extend_findings(
            rules.check_illegal_filesystem_character(
                path_segments=[],
                file_names=[file_name],
            ),
            findings=findings,
            run_path_str=run_path_str,
            offending_path_override=file_path_str,
            active_classes=active_classes,
            sync_status=sync_status,
        )
        self._extend_findings(
            rules.check_reserved_filesystem_name(file_names=[file_name]),
            findings=findings,
            run_path_str=run_path_str,
            offending_path_override=file_path_str,
            active_classes=active_classes,
            sync_status=sync_status,
        )

        # Content scan.
        if not self._content_scan_eligible(file_entry_path):
            return
        content = self._read_text_for_scan(file_entry_path)
        if content is None:
            return
        self._extend_findings(
            rules.check_unresolved_placeholder(
                path_segments=[],
                file_names=[],
                file_contents={file_path_str: content},
            ),
            findings=findings,
            run_path_str=run_path_str,
            offending_path_override=file_path_str,
            active_classes=active_classes,
            sync_status=sync_status,
        )

    # -- Audit: helpers ---------------------------------------------------

    def _content_scan_eligible(self, file_path: Path) -> bool:
        """Return True iff the file passes the size + extension gates.

        Spec §8.1.1: files outside the configured extension list are
        skipped; files larger than the configured size cap are skipped.
        Cache files (under ``.exlab-wizard/``, e.g. ``test_runs.json``)
        never reach this method because the parent's scandir loop skips
        the cache directory.
        """
        ext = file_path.suffix.lower()
        if ext not in self._content_scan_extensions:
            return False
        try:
            size = file_path.stat().st_size
        except OSError:
            return False
        return size <= self._content_scan_max_bytes

    def _read_text_for_scan(self, file_path: Path) -> str | None:
        """Read text bytes for placeholder scan, applying the binary sniff.

        Returns ``None`` if the file is detected as binary (any NUL
        byte in the first 8 KiB) or unreadable. UTF-8 decoded with
        ``errors="replace"`` so non-UTF-8 text files still scan rather
        than spuriously skip.
        """
        try:
            with file_path.open("rb") as handle:
                head = handle.read(VALIDATOR_BINARY_DETECT_BYTES)
                if b"\x00" in head:
                    return None
                rest = handle.read()
        except OSError:
            return None
        return (head + rest).decode("utf-8", errors="replace")

    def _read_creation_for(
        self, directory: Path
    ) -> tuple[CreationJson | None, dict[str, Any] | None]:
        """Read ``<directory>/.exlab-wizard/creation.json`` if present.

        Returns ``(payload, raw_dict)`` on success; ``(None, None)``
        when the file is missing, malformed, or unreadable. The raw
        dict is retained alongside the typed payload so the
        missing-required-field rule can pick up extra IDs the typed
        Struct discards.
        """
        path = directory / CACHE_DIR_NAME / CREATION_JSON_NAME
        if not path.exists():
            return None, None
        try:
            blob = path.read_bytes()
        except OSError:
            return None, None
        try:
            raw = msgspec_json.decode(blob, type=dict[str, Any])
        except (msgspec.DecodeError, msgspec.ValidationError):
            _log.debug("creation.json failed raw decode: %s", path)
            return None, None
        try:
            payload = msgspec.convert(raw, type=CreationJson)
        except (msgspec.ValidationError, msgspec.DecodeError):
            _log.debug("creation.json failed typed decode: %s", path)
            return None, raw
        return payload, raw

    @staticmethod
    def _extract_overrides_and_sync(
        creation_payload: CreationJson | None,
    ) -> tuple[set[str], str | None]:
        """Return ``(active_problem_classes, sync_status)`` for a payload.

        ``active_problem_classes`` is the set of ``problem_class``
        strings that have a non-revoked, non-expired override entry
        (per the §11.3 matching algorithm). ``sync_status`` is the
        payload's literal ``sync_status`` value (or ``None`` when the
        payload itself is absent).
        """
        if creation_payload is None:
            return set(), None
        active = select_active_overrides(creation_payload.validation_overrides)
        return (
            {e["problem_class"] for e in active if "problem_class" in e},
            creation_payload.sync_status,
        )

    @staticmethod
    def _materialise_audit(
        *,
        raw: dict[str, Any],
        run_path_str: str,
        offending_path_override: str | None,
        active_classes: set[str],
        sync_status: str | None,
    ) -> Finding:
        """Audit-mode counterpart of :meth:`_materialise`.

        Computes ``override_active`` (the rule's class is in
        ``active_classes``) and ``synced_under_prior_policy`` (the
        finding is hard-tier AND the run was already synced -- either
        ``"synced"`` or ``"cleaned"``, since a cleaned run was synced
        first) per §11.8, then builds the :class:`Finding` instance.
        """
        rule_name = str(raw["rule"])
        tier = str(raw["tier"])
        offending_kind = str(raw["offending_kind"])
        offending_path = (
            offending_path_override
            if offending_path_override is not None
            else str(raw.get("offending_path", ""))
        )
        matched_token = raw.get("matched_token")
        rule_detail = str(raw.get("rule_detail", ""))

        override_active = rule_name in active_classes
        synced_under_prior_policy = tier == Tier.HARD.value and sync_status in (
            SyncStatus.SYNCED.value,
            SyncStatus.CLEANED.value,
        )

        return Finding(
            rule=rule_name,
            tier=tier,
            run_path=run_path_str,
            offending_path=offending_path,
            offending_kind=offending_kind,
            matched_token=None if matched_token is None else str(matched_token),
            rule_detail=rule_detail,
            synced_under_prior_policy=synced_under_prior_policy,
            override_active=override_active,
        )


# ---------------------------------------------------------------------------
# Sort key
# ---------------------------------------------------------------------------


def _tier_rank(tier: str) -> int:
    """Hard tier sorts before soft tier (§11.8).

    Returns 0 for ``"hard"`` and 1 for ``"soft"``; any other string
    sorts after both tiers (defensive -- the rule helpers only emit
    the two committed values).
    """
    if tier == Tier.HARD.value:
        return 0
    if tier == Tier.SOFT.value:
        return 1
    return 2


def _finding_sort_key(finding: Finding) -> tuple[int, str, str]:
    """Sort key for the §11.8 finding list ordering.

    Tuple: ``(tier_rank, rule, offending_path)``. ``rule`` and
    ``offending_path`` are compared lexicographically -- the §11.8
    determinism contract only requires byte-identical lists across
    hosts given byte-identical inputs, so locale-independent ordinal
    comparison is the right choice.
    """
    return (_tier_rank(finding.tier), finding.rule, finding.offending_path)


# ---------------------------------------------------------------------------
# Audit-mode helpers
# ---------------------------------------------------------------------------


def _level_for_orphan(level: str) -> str | None:
    """Translate the engine-level enum into the rules.check_orphan input.

    The orphan rule only applies at project / run level (§8.1.4);
    equipment, test-runs marker, and "other" levels return ``None``.
    Test-run leafs are treated as ``"run"`` for orphan purposes (the
    spec wires the rule to project / run; a test run is a kind of run).
    """
    if level == "project":
        return "project"
    if level in {"run", "test_run"}:
        return "run"
    return None
