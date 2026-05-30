"""Single source of truth for template (``copier.yml``) validation.

The §5.1 checks that used to live inline in
:meth:`exlab_wizard.template.copier_driver.TemplateEngine.resolve` are
factored out here so resolve-time and author-time validation share one
rule set. The GUI authoring form calls :func:`lint_template` /
:func:`lint_manifest_dict` to gate saves; ``TemplateEngine.resolve``
calls :func:`lint_manifest_dict` and re-raises its ERROR findings as the
existing ``TemplateLoadError`` / ``TemplateCoreFieldRedeclaredError``.

Findings are returned as :class:`LintFinding` (a small, lint-specific
shape) rather than the validator's run-output ``Finding`` -- the latter
carries ``rule`` / ``run_path`` / ``offending_path`` / ``offending_kind``
fields that are meaningless for a ``copier.yml``.

Two tiers:

* ``error`` -- the manifest/template is unusable; a save is refused and
  ``resolve`` raises.
* ``warn`` -- the manifest is usable but deviates from convention; a
  save proceeds with a banner.

:func:`lint_manifest_dict` validates an already-parsed manifest mapping
(no file I/O). :func:`lint_template` adds the file-level checks
(``copier.yml`` existence / readability / YAML-parse) and a Jinja2
syntax check across every ``*.jinja`` file under the template root.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from jinja2 import Environment, TemplateSyntaxError

from exlab_wizard.constants import (
    COPIER_MANIFEST_NAME,
    TEMPLATE_QUESTION_ID_PATTERN,
    RunScope,
    TemplateType,
)
from exlab_wizard.template.copier_driver import CORE_README_FIELD_IDS

__all__ = [
    "LintFinding",
    "LintSeverity",
    "has_errors",
    "lint_manifest_dict",
    "lint_template",
]

LintSeverity = Literal["error", "warn"]

# Baseline ``_min_copier_version`` the app supports (Backend §5). A
# missing or lower value is a WARN.
_MIN_COPIER_VERSION_BASELINE: str = "9.0"

# Leading dotted-integer run of a version string, e.g. "10.0" in "10.0.1rc2".
_VERSION_NUMERIC_PREFIX = re.compile(r"\d+(?:\.\d+)*")


def _version_tuple(value: str) -> tuple[int, ...]:
    """Parse the leading dotted-integer run of ``value`` into a tuple.

    Returns ``()`` when ``value`` has no leading numeric component, which
    sorts below every real version. Used so ``_min_copier_version``
    comparison is numeric (``"10.0" > "9.0"``) rather than lexicographic
    (where ``"10.0" < "9.0"``).
    """
    match = _VERSION_NUMERIC_PREFIX.match(value.strip())
    if match is None:
        return ()
    return tuple(int(part) for part in match.group().split("."))

# Conventional ``_answers_file`` value (Backend §5.3). A deviation is a WARN.
_CONVENTIONAL_ANSWERS_FILE: str = ".exlab-answers.yml"

# Valid Copier long-form ``type`` strings (Backend §5). A question whose
# declared long-form ``type`` is outside this set is a WARN.
_VALID_QUESTION_TYPES: frozenset[str] = frozenset({"str", "int", "float", "bool", "yaml", "json"})


@dataclass(frozen=True)
class LintFinding:
    """One template-validation finding.

    Attributes:
        code: Stable machine code (e.g. ``"template_type_missing"``).
        message: Human-readable description (carries the same wording
            ``TemplateEngine.resolve`` historically raised, so the
            re-raised exceptions are message-identical).
        severity: ``"error"`` (refuse / raise) or ``"warn"`` (proceed).
        path: Relative path of the offending file for file-scoped
            findings (e.g. a ``*.jinja`` with a syntax error); ``None``
            for manifest-level findings.
    """

    code: str
    message: str
    severity: LintSeverity
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable mapping of this finding."""
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "path": self.path,
        }


def lint_manifest_dict(manifest: dict[str, Any], manifest_path: Path) -> list[LintFinding]:
    """Validate an already-parsed ``copier.yml`` mapping.

    Ports the §5.1 metadata checks out of ``TemplateEngine.resolve`` and
    ``_extract_readme_fields``. Does **not** check file existence or
    YAML-parse the file -- those are file-level and handled by
    :func:`lint_template`. Messages match the wording
    ``TemplateEngine.resolve`` historically raised so re-raised
    exceptions are byte-identical.

    Args:
        manifest: The parsed ``copier.yml`` body.
        manifest_path: Path to the manifest, used only to prefix messages.

    Returns:
        All findings (ERROR + WARN), in a stable order.
    """
    findings: list[LintFinding] = []

    # ---- _exlab_type: present, valid -------------------------------------
    raw_type = manifest.get("_exlab_type")
    type_ok = False
    if not isinstance(raw_type, str) or not raw_type:
        findings.append(
            LintFinding(
                code="template_type_missing",
                message=f"{manifest_path}: _exlab_type missing or empty",
                severity="error",
            )
        )
    elif raw_type not in {t.value for t in TemplateType}:
        findings.append(
            LintFinding(
                code="template_type_invalid",
                message=(
                    f"{manifest_path}: _exlab_type must be one of "
                    f"{sorted(t.value for t in TemplateType)}, got {raw_type!r}"
                ),
                severity="error",
            )
        )
    else:
        type_ok = True

    # ---- _exlab_version: required non-empty string (§5.7) ----------------
    exlab_version = manifest.get("_exlab_version")
    if not isinstance(exlab_version, str) or not exlab_version.strip():
        findings.append(
            LintFinding(
                code="exlab_version_missing",
                message=(
                    f"{manifest_path}: _exlab_version is required and must be a "
                    f"non-empty string (§5.7)"
                ),
                severity="error",
            )
        )

    # ---- _exlab_run_scope: required + valid for run templates ------------
    if type_ok and raw_type == TemplateType.RUN.value:
        raw_scope = manifest.get("_exlab_run_scope")
        if not isinstance(raw_scope, str) or not raw_scope:
            findings.append(
                LintFinding(
                    code="run_scope_missing",
                    message=(
                        f"{manifest_path}: _exlab_run_scope is required for run "
                        f"templates and must be one of "
                        f"{sorted(s.value for s in RunScope)}"
                    ),
                    severity="error",
                )
            )
        elif raw_scope not in {s.value for s in RunScope}:
            findings.append(
                LintFinding(
                    code="run_scope_invalid",
                    message=(
                        f"{manifest_path}: _exlab_run_scope must be one of "
                        f"{sorted(s.value for s in RunScope)}, got {raw_scope!r}"
                    ),
                    severity="error",
                )
            )

    # ---- _exlab_readme.fields: must not redeclare core fields (§10.3) ----
    # Tolerate malformed _exlab_readme / fields shapes exactly like
    # ``_extract_readme_fields`` (skip silently, never crash).
    readme_block = manifest.get("_exlab_readme")
    if isinstance(readme_block, dict):
        raw_fields = readme_block.get("fields")
        if isinstance(raw_fields, list):
            for entry in raw_fields:
                if not isinstance(entry, dict):
                    continue
                field_id = entry.get("id")
                if isinstance(field_id, str) and field_id in CORE_README_FIELD_IDS:
                    findings.append(
                        LintFinding(
                            code="core_field_redeclared",
                            message=(
                                f"{manifest_path}: _exlab_readme.fields redeclares "
                                f"core field {field_id!r}; core fields (label / "
                                f"operator / objective) are backend-managed (§10.3)"
                            ),
                            severity="error",
                        )
                    )

    # ---- _tasks: present (silently ignored under unsafe=False, §5.5) -----
    if "_tasks" in manifest:
        findings.append(
            LintFinding(
                code="tasks_present",
                message=(
                    f"{manifest_path}: _tasks declared; silently ignored "
                    f"(unsafe=False, see Backend Spec §5.5)"
                ),
                severity="warn",
            )
        )

    # ---- _min_copier_version: missing or below the "9.0" baseline --------
    raw_min = manifest.get("_min_copier_version")
    if not isinstance(raw_min, str) or _version_tuple(raw_min) < _version_tuple(
        _MIN_COPIER_VERSION_BASELINE
    ):
        findings.append(
            LintFinding(
                code="min_copier_version_low",
                message=(
                    f"{manifest_path}: _min_copier_version should be >= "
                    f"{_MIN_COPIER_VERSION_BASELINE!r}"
                ),
                severity="warn",
            )
        )

    # ---- _answers_file: deviates from convention -------------------------
    raw_answers = manifest.get("_answers_file")
    if isinstance(raw_answers, str) and raw_answers != _CONVENTIONAL_ANSWERS_FILE:
        findings.append(
            LintFinding(
                code="answers_file_deviation",
                message=(
                    f"{manifest_path}: _answers_file {raw_answers!r} deviates from "
                    f"the convention {_CONVENTIONAL_ANSWERS_FILE!r}"
                ),
                severity="warn",
            )
        )

    # ---- question keys: grammar + long-form type -------------------------
    for key, spec in manifest.items():
        if key.startswith("_"):
            continue
        if not TEMPLATE_QUESTION_ID_PATTERN.match(key):
            findings.append(
                LintFinding(
                    code="question_id_invalid",
                    message=(
                        f"{manifest_path}: question id {key!r} must match "
                        f"{TEMPLATE_QUESTION_ID_PATTERN.pattern!r}"
                    ),
                    severity="warn",
                )
            )
        if isinstance(spec, dict) and "type" in spec:
            q_type = str(spec.get("type"))
            if q_type not in _VALID_QUESTION_TYPES:
                findings.append(
                    LintFinding(
                        code="question_type_invalid",
                        message=(
                            f"{manifest_path}: question {key!r} has type {q_type!r}; "
                            f"expected one of {sorted(_VALID_QUESTION_TYPES)}"
                        ),
                        severity="warn",
                    )
                )

    return findings


def lint_template(template_dir: Path) -> list[LintFinding]:
    """Validate a template directory end-to-end.

    Runs the file-level checks (``copier.yml`` existence, readability,
    YAML-parse), then delegates the manifest checks to
    :func:`lint_manifest_dict`, then parses every ``*.jinja`` file under
    ``template_dir`` with Jinja2 and reports syntax errors.

    Args:
        template_dir: The template root (directory containing
            ``copier.yml``).

    Returns:
        All findings (ERROR + WARN). A fatal ``copier.yml`` problem
        short-circuits the manifest checks (but the Jinja scan still runs).
    """
    findings: list[LintFinding] = []
    manifest_path = template_dir / COPIER_MANIFEST_NAME

    if not manifest_path.is_file():
        findings.append(
            LintFinding(
                code="copier_yml_missing",
                message=f"copier.yml not found at {manifest_path}",
                severity="error",
            )
        )
    else:
        manifest: dict[str, Any] | None = None
        try:
            text = manifest_path.read_text(encoding="utf-8")
            parsed = yaml.safe_load(text)
        except OSError as exc:
            findings.append(
                LintFinding(
                    code="copier_yml_unreadable",
                    message=f"failed to read {manifest_path}: {exc}",
                    severity="error",
                )
            )
        except yaml.YAMLError as exc:
            findings.append(
                LintFinding(
                    code="copier_yml_parse_error",
                    message=f"failed to parse {manifest_path}: {exc}",
                    severity="error",
                )
            )
        else:
            manifest = parsed if isinstance(parsed, dict) else {}
        if manifest is not None:
            findings.extend(lint_manifest_dict(manifest, manifest_path))

    findings.extend(_lint_jinja_files(template_dir))
    return findings


def _lint_jinja_files(template_dir: Path) -> list[LintFinding]:
    """Parse every ``*.jinja`` file under ``template_dir`` with Jinja2.

    Emits an ERROR ``jinja_syntax_error`` (with the offending file's
    path relative to ``template_dir`` and the failing line number in the
    message) for each file Jinja2 cannot parse. Unreadable files are
    reported the same way so the author sees the problem.
    """
    findings: list[LintFinding] = []
    env = Environment()  # parse-only; never renders untrusted input.
    for jinja_path in sorted(template_dir.rglob("*.jinja")):
        if not jinja_path.is_file():
            continue
        rel = str(jinja_path.relative_to(template_dir))
        try:
            text = jinja_path.read_text(encoding="utf-8")
        except OSError as exc:
            findings.append(
                LintFinding(
                    code="jinja_syntax_error",
                    message=f"{rel}: failed to read: {exc}",
                    severity="error",
                    path=rel,
                )
            )
            continue
        try:
            env.parse(text)
        except TemplateSyntaxError as exc:
            findings.append(
                LintFinding(
                    code="jinja_syntax_error",
                    message=f"{rel}: Jinja syntax error on line {exc.lineno}: {exc.message}",
                    severity="error",
                    path=rel,
                )
            )
    return findings


def has_errors(findings: list[LintFinding]) -> bool:
    """Return ``True`` if any finding has ``severity == "error"``."""
    return any(f.severity == "error" for f in findings)
