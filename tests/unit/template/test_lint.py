"""Tests for the factored-out template linter (Backend Spec §5.1).

One test per ERROR code and per WARN code, plus the file-level checks
(``copier.yml`` existence / readability / parse) and the Jinja2 syntax
scan. A clean template yields no errors; :func:`has_errors` reflects the
ERROR tier.
"""

from __future__ import annotations

from pathlib import Path

from exlab_wizard.constants import COPIER_MANIFEST_NAME
from exlab_wizard.template.lint import (
    LintFinding,
    has_errors,
    lint_manifest_dict,
    lint_template,
)

_MANIFEST_PATH = Path("/tpl/copier.yml")


def _codes(findings: list[LintFinding]) -> set[str]:
    return {f.code for f in findings}


def _valid_manifest() -> dict:
    """A manifest dict that triggers no findings at all."""
    return {
        "_min_copier_version": "9.0",
        "_answers_file": ".exlab-answers.yml",
        "_exlab_type": "project",
        "_exlab_version": "1.0",
    }


def _write_template(root: Path, manifest_body: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / COPIER_MANIFEST_NAME).write_text(manifest_body, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# clean manifest
# ---------------------------------------------------------------------------


def test_valid_manifest_dict_has_no_findings() -> None:
    assert lint_manifest_dict(_valid_manifest(), _MANIFEST_PATH) == []


def test_valid_run_manifest_dict_has_no_findings() -> None:
    manifest = _valid_manifest()
    manifest["_exlab_type"] = "run"
    manifest["_exlab_run_scope"] = "experimental"
    assert lint_manifest_dict(manifest, _MANIFEST_PATH) == []


# ---------------------------------------------------------------------------
# ERROR codes (manifest-dict)
# ---------------------------------------------------------------------------


def test_error_template_type_missing() -> None:
    manifest = _valid_manifest()
    del manifest["_exlab_type"]
    findings = lint_manifest_dict(manifest, _MANIFEST_PATH)
    assert "template_type_missing" in _codes(findings)
    assert has_errors(findings)


def test_error_template_type_invalid() -> None:
    manifest = _valid_manifest()
    manifest["_exlab_type"] = "nonsense"
    findings = lint_manifest_dict(manifest, _MANIFEST_PATH)
    assert "template_type_invalid" in _codes(findings)
    assert has_errors(findings)


def test_error_exlab_version_missing() -> None:
    manifest = _valid_manifest()
    del manifest["_exlab_version"]
    findings = lint_manifest_dict(manifest, _MANIFEST_PATH)
    assert "exlab_version_missing" in _codes(findings)


def test_error_exlab_version_blank() -> None:
    manifest = _valid_manifest()
    manifest["_exlab_version"] = "   "
    findings = lint_manifest_dict(manifest, _MANIFEST_PATH)
    assert "exlab_version_missing" in _codes(findings)


def test_error_run_scope_missing() -> None:
    manifest = _valid_manifest()
    manifest["_exlab_type"] = "run"
    findings = lint_manifest_dict(manifest, _MANIFEST_PATH)
    assert "run_scope_missing" in _codes(findings)


def test_error_run_scope_invalid() -> None:
    manifest = _valid_manifest()
    manifest["_exlab_type"] = "run"
    manifest["_exlab_run_scope"] = "bogus"
    findings = lint_manifest_dict(manifest, _MANIFEST_PATH)
    assert "run_scope_invalid" in _codes(findings)


def test_error_core_field_redeclared() -> None:
    manifest = _valid_manifest()
    manifest["_exlab_readme"] = {"fields": [{"id": "operator"}]}
    findings = lint_manifest_dict(manifest, _MANIFEST_PATH)
    assert "core_field_redeclared" in _codes(findings)


def test_malformed_readme_does_not_crash() -> None:
    manifest = _valid_manifest()
    manifest["_exlab_readme"] = "not a dict"
    findings = lint_manifest_dict(manifest, _MANIFEST_PATH)
    assert "core_field_redeclared" not in _codes(findings)
    manifest["_exlab_readme"] = {"fields": "not a list"}
    findings = lint_manifest_dict(manifest, _MANIFEST_PATH)
    assert "core_field_redeclared" not in _codes(findings)


# ---------------------------------------------------------------------------
# WARN codes
# ---------------------------------------------------------------------------


def test_warn_tasks_present() -> None:
    manifest = _valid_manifest()
    manifest["_tasks"] = ["echo hi"]
    findings = lint_manifest_dict(manifest, _MANIFEST_PATH)
    assert "tasks_present" in _codes(findings)
    assert not has_errors(findings)


def test_warn_min_copier_version_low() -> None:
    manifest = _valid_manifest()
    manifest["_min_copier_version"] = "8.0"
    findings = lint_manifest_dict(manifest, _MANIFEST_PATH)
    assert "min_copier_version_low" in _codes(findings)


def test_warn_min_copier_version_missing() -> None:
    manifest = _valid_manifest()
    del manifest["_min_copier_version"]
    findings = lint_manifest_dict(manifest, _MANIFEST_PATH)
    assert "min_copier_version_low" in _codes(findings)


def test_min_copier_version_double_digit_major_not_flagged() -> None:
    """A future major (e.g. "10.0") is >= the "9.0" baseline.

    Guards against the lexicographic-compare regression where
    ``"10.0" < "9.0"`` is ``True`` (string order), which would spuriously
    flag every template once Copier reaches v10.
    """
    manifest = _valid_manifest()
    manifest["_min_copier_version"] = "10.0"
    findings = lint_manifest_dict(manifest, _MANIFEST_PATH)
    assert "min_copier_version_low" not in _codes(findings)


def test_warn_answers_file_deviation() -> None:
    manifest = _valid_manifest()
    manifest["_answers_file"] = ".custom-answers.yml"
    findings = lint_manifest_dict(manifest, _MANIFEST_PATH)
    assert "answers_file_deviation" in _codes(findings)


def test_warn_question_id_invalid() -> None:
    manifest = _valid_manifest()
    manifest["Bad-Key"] = {"type": "str"}
    findings = lint_manifest_dict(manifest, _MANIFEST_PATH)
    assert "question_id_invalid" in _codes(findings)


def test_warn_question_type_invalid() -> None:
    manifest = _valid_manifest()
    manifest["good_key"] = {"type": "weirdtype"}
    findings = lint_manifest_dict(manifest, _MANIFEST_PATH)
    assert "question_type_invalid" in _codes(findings)


def test_valid_question_does_not_warn() -> None:
    manifest = _valid_manifest()
    manifest["sample_id"] = {"type": "str", "default": "x"}
    findings = lint_manifest_dict(manifest, _MANIFEST_PATH)
    assert "question_id_invalid" not in _codes(findings)
    assert "question_type_invalid" not in _codes(findings)


# ---------------------------------------------------------------------------
# file-level checks (lint_template)
# ---------------------------------------------------------------------------


def test_lint_template_copier_yml_missing(tmp_path: Path) -> None:
    (tmp_path / "tpl").mkdir()
    findings = lint_template(tmp_path / "tpl")
    assert "copier_yml_missing" in _codes(findings)
    assert has_errors(findings)


def test_lint_template_copier_yml_parse_error(tmp_path: Path) -> None:
    root = _write_template(tmp_path / "tpl", "key: [unclosed\n")
    findings = lint_template(root)
    assert "copier_yml_parse_error" in _codes(findings)


def test_lint_template_valid_template_no_errors(tmp_path: Path) -> None:
    root = _write_template(
        tmp_path / "tpl",
        "_min_copier_version: '9.0'\n"
        "_answers_file: .exlab-answers.yml\n"
        "_exlab_type: project\n"
        "_exlab_version: '1.0'\n",
    )
    (root / "notes.md.jinja").write_text("# {{ project_name }}\n", encoding="utf-8")
    findings = lint_template(root)
    assert not has_errors(findings)


def test_lint_template_jinja_syntax_error(tmp_path: Path) -> None:
    root = _write_template(
        tmp_path / "tpl",
        "_min_copier_version: '9.0'\n_exlab_type: project\n_exlab_version: '1.0'\n",
    )
    broken = root / "broken.md.jinja"
    broken.write_text("{{ broken\n", encoding="utf-8")
    findings = lint_template(root)
    jinja_findings = [f for f in findings if f.code == "jinja_syntax_error"]
    assert len(jinja_findings) == 1
    assert jinja_findings[0].path == "broken.md.jinja"
    assert jinja_findings[0].severity == "error"
    assert has_errors(findings)


# ---------------------------------------------------------------------------
# has_errors + to_dict
# ---------------------------------------------------------------------------


def test_has_errors_true_and_false() -> None:
    assert has_errors([LintFinding(code="x", message="m", severity="error")])
    assert not has_errors([LintFinding(code="x", message="m", severity="warn")])
    assert not has_errors([])


def test_finding_to_dict() -> None:
    finding = LintFinding(code="x", message="m", severity="warn", path="a/b.jinja")
    assert finding.to_dict() == {
        "code": "x",
        "message": "m",
        "severity": "warn",
        "path": "a/b.jinja",
    }
