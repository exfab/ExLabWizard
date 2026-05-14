"""Tests for ``exlab_wizard.ui.mount`` helpers.

The ``@ui.page(...)`` decorators in ``mount.py`` require a running
NiceGUI app, which a unit test won't spin up. Instead we exercise the
pure helpers that drive the routing and state-assembly decisions:
setup-state gating, settings-section completeness, and the
defensive-degradation paths for missing / raising dependencies.
"""

from __future__ import annotations

# Prime the api package first so the orchestrator's deferred `api.schemas`
# import doesn't trigger a fresh top-down load that races against the
# `orchestrator.cleanup <-> api.routers.staging` import cycle. Production
# always loads api first (the tray's _build_default_app imports api.app
# before any page module), so this matches the live import order.
import exlab_wizard.api.app  # noqa: F401  -- import order matters
from types import SimpleNamespace
from typing import Any

import pytest

from exlab_wizard.ui import mount


def _deps(**overrides: Any) -> SimpleNamespace:
    """Build a minimal duck-typed AppDependencies stand-in."""
    base: dict[str, Any] = {
        "config": None,
        "validator": None,
        "controller": None,
        "lims_reachable": True,
        "keyring_password_present": False,
        "autostart_toggle": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _config(
    *,
    local_root: str = "/tmp/data",
    templates_dir: str = "/tmp/tpl",
    lims_endpoint: str = "https://lims.example",
    lims_email: str = "operator@example",
    orchestrator_enabled: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        paths=SimpleNamespace(
            local_root=local_root,
            templates_dir=templates_dir,
            plugin_dir="",
        ),
        lims=SimpleNamespace(endpoint=lims_endpoint, email=lims_email),
        orchestrator=SimpleNamespace(enabled=orchestrator_enabled, staging_root=""),
    )


# ---------------------------------------------------------------------------
# _is_setup_ready
# ---------------------------------------------------------------------------


def test_is_setup_ready_false_when_deps_none() -> None:
    assert mount._is_setup_ready(None) is False


def test_is_setup_ready_false_when_config_missing() -> None:
    assert mount._is_setup_ready(_deps(config=None)) is False


def test_is_setup_ready_false_when_keyring_missing() -> None:
    deps = _deps(config=_config(), keyring_password_present=False)
    assert mount._is_setup_ready(deps) is False


def test_is_setup_ready_false_when_lims_unreachable() -> None:
    deps = _deps(
        config=_config(),
        keyring_password_present=True,
        lims_reachable=False,
    )
    assert mount._is_setup_ready(deps) is False


def test_is_setup_ready_true_when_all_satisfied() -> None:
    deps = _deps(
        config=_config(),
        keyring_password_present=True,
        lims_reachable=True,
    )
    assert mount._is_setup_ready(deps) is True


# ---------------------------------------------------------------------------
# _build_main_state
# ---------------------------------------------------------------------------


def test_build_main_state_marks_incomplete_without_config() -> None:
    state = mount._build_main_state(_deps())
    assert state.setup_incomplete is True
    assert state.orchestrator_enabled is False


def test_build_main_state_reflects_orchestrator_flag() -> None:
    deps = _deps(
        config=_config(orchestrator_enabled=True),
        keyring_password_present=True,
    )
    state = mount._build_main_state(deps)
    assert state.setup_incomplete is False
    assert state.orchestrator_enabled is True


# ---------------------------------------------------------------------------
# _missing_setup_sections
# ---------------------------------------------------------------------------


def test_missing_sections_when_deps_none() -> None:
    assert mount._missing_setup_sections(None) == ("paths", "lims")


def test_missing_sections_when_config_none() -> None:
    assert mount._missing_setup_sections(_deps()) == ("paths", "lims", "operators")


def test_missing_sections_with_paths_unset() -> None:
    deps = _deps(
        config=_config(local_root="", templates_dir=""),
        keyring_password_present=True,
    )
    sections = mount._missing_setup_sections(deps)
    assert "paths" in sections


def test_missing_sections_with_keyring_absent_reports_lims() -> None:
    deps = _deps(config=_config(), keyring_password_present=False)
    assert "lims" in mount._missing_setup_sections(deps)


def test_missing_sections_empty_when_fully_configured() -> None:
    deps = _deps(config=_config(), keyring_password_present=True)
    assert mount._missing_setup_sections(deps) == ()


# ---------------------------------------------------------------------------
# _safe_audit
# ---------------------------------------------------------------------------


class _RaisingValidator:
    def audit(self, _scope: Any) -> list[Any]:
        msg = "boom"
        raise RuntimeError(msg)


class _OkValidator:
    def __init__(self, findings: list[Any]) -> None:
        self._findings = findings

    def audit(self, _scope: Any) -> list[Any]:
        return self._findings


def test_safe_audit_returns_empty_list_when_validator_missing() -> None:
    assert mount._safe_audit(_deps()) == []


def test_safe_audit_swallows_validator_exception(caplog: pytest.LogCaptureFixture) -> None:
    deps = _deps(validator=_RaisingValidator())
    with caplog.at_level("WARNING"):
        result = mount._safe_audit(deps)
    assert result == []
    assert any("validator.audit" in r.message for r in caplog.records)


def test_safe_audit_forwards_validator_output() -> None:
    expected = [object(), object()]
    deps = _deps(validator=_OkValidator(expected))
    assert mount._safe_audit(deps) == expected


# ---------------------------------------------------------------------------
# _build_staging_state
# ---------------------------------------------------------------------------


def test_staging_state_none_when_orchestrator_disabled() -> None:
    deps = _deps(config=_config(orchestrator_enabled=False))
    assert mount._build_staging_state(deps) is None


def test_staging_state_none_when_no_config() -> None:
    assert mount._build_staging_state(_deps()) is None


def test_staging_state_returns_empty_rows_on_query_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps = _deps(config=_config(orchestrator_enabled=True))

    def _raise(*_args: Any, **_kwargs: Any) -> None:
        msg = "no staging root"
        raise RuntimeError(msg)

    monkeypatch.setattr(
        "exlab_wizard.orchestrator.staging_query.list_staged_runs",
        _raise,
    )
    state = mount._build_staging_state(deps)
    assert state is not None
    assert state.rows == []


# ---------------------------------------------------------------------------
# _restart_gate
# ---------------------------------------------------------------------------


class _NavSpy:
    """Minimal stand-in for the NiceGUI ``ui`` object's navigate surface."""

    def __init__(self) -> None:
        self.navigated: list[str] = []
        self.navigate = SimpleNamespace(to=self.navigated.append)


def test_restart_gate_false_when_deps_none() -> None:
    nav = _NavSpy()
    assert mount._restart_gate(None, nav) is False
    assert nav.navigated == []


def test_restart_gate_false_when_flag_unset() -> None:
    nav = _NavSpy()
    assert mount._restart_gate(_deps(restart_required=False), nav) is False
    assert nav.navigated == []


def test_restart_gate_redirects_when_flag_set() -> None:
    nav = _NavSpy()
    assert mount._restart_gate(_deps(restart_required=True), nav) is True
    assert nav.navigated == ["/restart-required"]


# ---------------------------------------------------------------------------
# _persist_config
# ---------------------------------------------------------------------------


def test_persist_config_writes_and_arms_restart_gate() -> None:
    nav = _NavSpy()
    saved: list[Any] = []
    deps = _deps(save_config=saved.append, restart_required=False)
    sentinel_config = object()

    ok = mount._persist_config(deps, sentinel_config, nav)

    assert ok is True
    assert saved == [sentinel_config]
    assert deps.config is sentinel_config
    assert deps.restart_required is True


def test_persist_config_returns_false_when_no_saver() -> None:
    nav = _NavSpy()
    deps = _deps(save_config=None, restart_required=False)

    ok = mount._persist_config(deps, object(), nav)

    assert ok is False
    assert deps.restart_required is False


def test_persist_config_returns_false_when_saver_raises() -> None:
    nav = _NavSpy()

    def _boom(_config: Any) -> None:
        msg = "disk full"
        raise OSError(msg)

    deps = _deps(save_config=_boom, restart_required=False)

    ok = mount._persist_config(deps, object(), nav)

    assert ok is False
    assert deps.restart_required is False
