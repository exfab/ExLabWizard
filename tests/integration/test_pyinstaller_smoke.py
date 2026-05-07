"""Structural smoke tests for the PyInstaller distribution. Phase 15.

The actual ``pyinstaller exlab_wizard.spec`` invocation is exercised by
``.github/workflows/build.yml`` (see Backend Spec §15.1). These tests
guard the *static* contracts that have to hold for the build to even be
worth running:

* The three entry-point modules import cleanly.
* ``__version__`` is exported from the package and well-formed (§15.6).
* ``pyproject.toml`` declares exactly the three console scripts named in
  Backend Spec §15.3.
* ``exlab_wizard.spec`` is valid Python and references each of the
  three entry points by name.
* ``.github/workflows/build.yml`` is valid YAML and lists all four
  target runners (Backend Spec §15.1 table).
* The bundled-content placeholder directories exist (``_internal/``).

If any of these regress, the CI build will either fail later or produce
a broken artifact. Failing them here is cheaper.
"""

from __future__ import annotations

import ast
import importlib
import re
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO_ROOT / "exlab_wizard.spec"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "build.yml"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
INTERNAL_TEMPLATES = REPO_ROOT / "_internal" / "templates"
INTERNAL_PLUGINS = REPO_ROOT / "_internal" / "plugins"

# The three console scripts declared in Backend Spec §15.3 and committed
# to ``[project.scripts]`` in ``pyproject.toml``.
EXPECTED_SCRIPTS = {
    "exlab-wizard": "exlab_wizard.__main__:main",
    "exlab-wizard-tray": "exlab_wizard.tray.main:main",
    "exlab-wizard-window": "exlab_wizard.window.main:main",
}

# The four CI runners enumerated in the §15.1 table.
EXPECTED_RUNNERS = {"ubuntu-latest", "windows-latest", "macos-13", "macos-14"}


# ---------------------------------------------------------------------------
# Entry-point imports
# ---------------------------------------------------------------------------


def test_tray_main_imports() -> None:
    """``exlab_wizard.tray.main:main`` must import cleanly.

    PyInstaller's static analyser bails on import-time exceptions, so a
    regression here would produce a broken bundle.
    """
    mod = importlib.import_module("exlab_wizard.tray.main")
    assert callable(getattr(mod, "main", None))


def test_window_main_imports() -> None:
    """``exlab_wizard.window.main:main`` must import cleanly."""
    mod = importlib.import_module("exlab_wizard.window.main")
    assert callable(getattr(mod, "main", None))


def test_cli_main_imports() -> None:
    """``exlab_wizard.__main__:main`` must import cleanly.

    This is the operator-facing CLI alias (Backend Spec §15.3.3).
    """
    mod = importlib.import_module("exlab_wizard.__main__")
    assert callable(getattr(mod, "main", None))


# ---------------------------------------------------------------------------
# Version exposure
# ---------------------------------------------------------------------------


def test_version_exposed_and_well_formed() -> None:
    """``exlab_wizard.__version__`` is a non-empty SemVer string.

    Backend Spec §15.6 commits to ``MAJOR.MINOR.PATCH`` and embeds the
    string in the binary at build time.
    """
    pkg = importlib.import_module("exlab_wizard")
    version = getattr(pkg, "__version__", "")
    assert isinstance(version, str) and version, "exlab_wizard.__version__ must be set"
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), (
        f"version must match MAJOR.MINOR.PATCH; got {version!r}"
    )


def test_version_matches_pyproject() -> None:
    """``__version__`` mirrors ``[project] version`` in ``pyproject.toml``.

    Drift between the two surfaces leaks into the bundle metadata and
    confuses the operator. Backend Spec §15.6 treats ``pyproject.toml``
    as the source of truth.
    """
    pkg = importlib.import_module("exlab_wizard")
    with PYPROJECT_PATH.open("rb") as fh:
        pp = tomllib.load(fh)
    assert pkg.__version__ == pp["project"]["version"]


# ---------------------------------------------------------------------------
# pyproject.toml -- console scripts
# ---------------------------------------------------------------------------


def test_pyproject_scripts_match_expected() -> None:
    """``[project.scripts]`` lists exactly the three §15.3 entry points."""
    with PYPROJECT_PATH.open("rb") as fh:
        pp = tomllib.load(fh)
    scripts = pp["project"]["scripts"]
    assert scripts == EXPECTED_SCRIPTS, (
        f"pyproject scripts mismatch: {scripts} != {EXPECTED_SCRIPTS}"
    )


# ---------------------------------------------------------------------------
# exlab_wizard.spec
# ---------------------------------------------------------------------------


def test_spec_file_is_valid_python() -> None:
    """``exlab_wizard.spec`` parses as Python source.

    The file is interpreted by PyInstaller as a normal Python module;
    a syntax error here aborts every build.
    """
    assert SPEC_PATH.is_file(), f"{SPEC_PATH} missing"
    source = SPEC_PATH.read_text(encoding="utf-8")
    ast.parse(source, filename=str(SPEC_PATH))


@pytest.mark.parametrize(
    "exe_name", ["ExLab-Wizard-Tray", "ExLab-Wizard-Window", "ExLab-Wizard"]
)
def test_spec_references_entry_executable(exe_name: str) -> None:
    """The spec names each of the three §15.1 executables."""
    source = SPEC_PATH.read_text(encoding="utf-8")
    assert exe_name in source, f"{exe_name} not referenced in {SPEC_PATH.name}"


@pytest.mark.parametrize(
    "script_path",
    [
        "exlab_wizard/tray/main.py",
        "exlab_wizard/window/main.py",
        "exlab_wizard/__main__.py",
    ],
)
def test_spec_references_entry_script(script_path: str) -> None:
    """The spec names the three entry-point script paths.

    PyInstaller resolves these against the repo root; an out-of-tree
    move would silently break the build.
    """
    source = SPEC_PATH.read_text(encoding="utf-8")
    assert script_path in source, f"{script_path} not referenced in {SPEC_PATH.name}"


def test_spec_uses_merge_directive() -> None:
    """The spec uses PyInstaller's ``MERGE`` directive (Backend Spec §15.1).

    ``MERGE`` is the mechanism that lets the three entry-point binaries
    share a single ``_internal/`` directory; without it we would ship
    three copies of the bundled CPython interpreter.
    """
    source = SPEC_PATH.read_text(encoding="utf-8")
    assert "MERGE(" in source, "spec must call MERGE() to share _internal/"


@pytest.mark.parametrize(
    "hidden_import",
    ["nicegui", "pystray", "plyer", "keyring", "copier", "ruamel.yaml"],
)
def test_spec_lists_required_hidden_imports(hidden_import: str) -> None:
    """The spec declares hidden imports for libs PyInstaller misses.

    Backend Spec §15.1 enumerates these explicitly.
    """
    source = SPEC_PATH.read_text(encoding="utf-8")
    assert hidden_import in source, f"hidden import {hidden_import!r} missing from spec"


def test_spec_declares_macos_bundle_identifier() -> None:
    """The spec sets the macOS ``CFBundleIdentifier`` (§15.1)."""
    source = SPEC_PATH.read_text(encoding="utf-8")
    assert "com.lab.exlab-wizard" in source


# ---------------------------------------------------------------------------
# CI workflow
# ---------------------------------------------------------------------------


def test_workflow_is_valid_yaml() -> None:
    """``.github/workflows/build.yml`` parses as YAML."""
    assert WORKFLOW_PATH.is_file(), f"{WORKFLOW_PATH} missing"
    with WORKFLOW_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict)
    assert "jobs" in data and "build" in data["jobs"]


def test_workflow_matrix_lists_all_four_targets() -> None:
    """The matrix covers all four §15.1 runners."""
    with WORKFLOW_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    matrix_includes = data["jobs"]["build"]["strategy"]["matrix"]["include"]
    runners = {entry["os"] for entry in matrix_includes}
    assert runners == EXPECTED_RUNNERS, (
        f"matrix runners mismatch: {runners} != {EXPECTED_RUNNERS}"
    )


def test_workflow_runs_pyinstaller_and_smoke() -> None:
    """The workflow runs ``pyinstaller exlab_wizard.spec`` and a smoke step.

    Backend Spec §15.1: the CI build is the canonical artifact producer;
    the smoke step is what gives us confidence the bundle even boots.
    """
    source = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "pyinstaller exlab_wizard.spec" in source
    assert "/api/v1/health" in source


# ---------------------------------------------------------------------------
# Bundled starter content placeholders (§15.4)
# ---------------------------------------------------------------------------


def test_internal_templates_dir_exists() -> None:
    """``_internal/templates/`` is checked into the repo (§15.4)."""
    assert INTERNAL_TEMPLATES.is_dir(), f"{INTERNAL_TEMPLATES} missing"
    assert (INTERNAL_TEMPLATES / ".gitkeep").is_file(), (
        "templates/.gitkeep placeholder missing"
    )


def test_internal_plugins_dir_exists() -> None:
    """``_internal/plugins/`` is checked into the repo (§15.4)."""
    assert INTERNAL_PLUGINS.is_dir(), f"{INTERNAL_PLUGINS} missing"
    assert (INTERNAL_PLUGINS / ".gitkeep").is_file(), (
        "plugins/.gitkeep placeholder missing"
    )


# ---------------------------------------------------------------------------
# Local build helpers
# ---------------------------------------------------------------------------


def test_local_build_scripts_exist() -> None:
    """Both POSIX and Windows wrappers are committed.

    These mirror the CI build steps; without them, developers cannot
    reproduce the artifact locally.
    """
    sh = REPO_ROOT / "scripts" / "build_local.sh"
    ps1 = REPO_ROOT / "scripts" / "build_local.ps1"
    assert sh.is_file(), f"{sh} missing"
    assert ps1.is_file(), f"{ps1} missing"
    if sys.platform != "win32":
        # POSIX permission bit. On Windows the bit is meaningless.
        assert sh.stat().st_mode & 0o111, "build_local.sh must be executable"
