"""Tests for the standalone dev seeder (``python -m exlab_wizard.dev.seed``)."""

from __future__ import annotations

from pathlib import Path

import pytest


def _sandbox_under(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin the sandbox under ``tmp_path`` and enable test mode.

    Patches ``paths.os_config_path`` / ``paths.ensure_state_dir`` directly so
    the seeder lands in ``tmp_path`` without depending on per-platform path
    resolution (forcing ``sys.platform`` would make ``sysconfig`` look up a
    nonexistent data module on the host). ``TEST_MODE_ENV`` is set via
    ``monkeypatch.setenv`` -- so it is restored on teardown even though
    ``dev.seed.main`` also sets it -- which keeps ``_app_name()`` ending in
    ``-test`` for the wipe guardrail. Returns the sandbox directory.
    """
    from exlab_wizard import paths

    sandbox = tmp_path / "exlab-wizard-test"
    config_path = sandbox / "config.yaml"
    monkeypatch.setenv(paths.TEST_MODE_ENV, "1")
    monkeypatch.setattr(paths, "os_config_path", lambda: config_path)
    monkeypatch.setattr(paths, "ensure_state_dir", lambda: sandbox)
    return sandbox


def test_seed_main_creates_full_tree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A clean sandbox (no config) is bootstrapped and fully seeded."""
    from exlab_wizard.constants import TEST_MODE_PREFIX
    from exlab_wizard.dev import seed
    from exlab_wizard.paths import creation_json_path

    sandbox = _sandbox_under(tmp_path, monkeypatch)

    assert seed.main([]) == 0

    local_root = sandbox / "app" / "data"
    testrig = local_root / f"{TEST_MODE_PREFIX}TESTRIG"
    altrig = local_root / f"{TEST_MODE_PREFIX}ALTRIG"
    assert (testrig / "Demo Project").is_dir()
    assert (altrig / "Calibration Study").is_dir()
    assert (altrig / "Failure Modes").is_dir()

    # 7 runs total: 4 experimental (Runs/Run_*) + 3 test (TestRuns/TestRun_*).
    run_dirs = [
        *local_root.glob("*/*/Runs/Run_*"),
        *local_root.glob("*/*/TestRuns/TestRun_*"),
    ]
    assert len(run_dirs) == 7
    for run_dir in run_dirs:
        assert (run_dir / "README.md").is_file()
        assert creation_json_path(run_dir).is_file()


def test_seed_main_wipes_and_rebuilds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A second run wipes the seeded subtree (clearing stray files) and rebuilds."""
    from exlab_wizard.constants import TEST_MODE_PREFIX
    from exlab_wizard.dev import seed

    sandbox = _sandbox_under(tmp_path, monkeypatch)

    assert seed.main([]) == 0

    # A stray file inside a seeded equipment dir must not survive the wipe.
    local_root = sandbox / "app" / "data"
    stray = local_root / f"{TEST_MODE_PREFIX}TESTRIG" / "STRAY.txt"
    stray.write_text("x", encoding="utf-8")
    assert stray.exists()

    assert seed.main([]) == 0
    assert not stray.exists()
    assert (local_root / f"{TEST_MODE_PREFIX}TESTRIG" / "Demo Project").is_dir()
