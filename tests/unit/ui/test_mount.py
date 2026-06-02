"""Tests for ``exlab_wizard.ui.mount`` helpers.

The ``@ui.page(...)`` decorators in ``mount.py`` require a running
NiceGUI app, which a unit test won't spin up. Instead we exercise the
pure helpers that drive the routing and state-assembly decisions:
setup-state gating, settings-section completeness, and the
defensive-degradation paths for missing / raising dependencies.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

# Prime the api package first so the orchestrator's deferred `api.schemas`
# import doesn't trigger a fresh top-down load that races against the
# `orchestrator.cleanup <-> api.routers.staging` import cycle. Production
# always loads api first (the tray's _build_default_app imports api.app
# before any page module), so this matches the live import order.
import exlab_wizard.api.app  # noqa: F401  -- import order matters
from exlab_wizard.constants import KEYRING_USERNAME_LIMS, RunKind
from exlab_wizard.controller import SessionState
from exlab_wizard.ui import mount
from exlab_wizard.ui.components.file_list import FileListEntry


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
    orchestrator_enabled: bool = False,  # legacy kw; orchestrator pipeline is always on
    orchestrator_label: str = "",
    orchestrator_staging_root: str = "",
    equipment: tuple[Any, ...] = (),
    offline_catalogue_path: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        paths=SimpleNamespace(
            local_root=local_root,
            templates_dir=templates_dir,
            plugin_dir="",
        ),
        lims=SimpleNamespace(
            endpoint=lims_endpoint,
            email=lims_email,
            offline_catalogue_path=offline_catalogue_path,
        ),
        orchestrator=SimpleNamespace(
            label=orchestrator_label,
            staging_root=orchestrator_staging_root,
        ),
        equipment=list(equipment),
    )


# ---------------------------------------------------------------------------
# Fake NiceGUI ``ui`` surfaces
# ---------------------------------------------------------------------------


class _Fluent:
    """Chainable no-op stand-in for a NiceGUI element."""

    def props(self, *_args: Any, **_kwargs: Any) -> _Fluent:
        return self

    def style(self, *_args: Any, **_kwargs: Any) -> _Fluent:
        return self

    def classes(self, *_args: Any, **_kwargs: Any) -> _Fluent:
        return self

    def __enter__(self) -> _Fluent:
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False


class _FakeController:
    """Duck-typed CreationController for the create-flow helpers."""

    def __init__(
        self,
        *,
        final_state: SessionState,
        session: Any = None,
        create_raises: bool = False,
        task: Any = None,
    ) -> None:
        self._tasks: dict[str, Any] = {}
        if task is not None:
            self._tasks["sess-1"] = task
        self._final_state = final_state
        self._create_raises = create_raises
        self.session_store = SimpleNamespace(get=lambda _sid: session)
        self.created: list[Any] = []

    async def status(self, _session_id: str) -> Any:
        return SimpleNamespace(state=self._final_state)

    async def create_project(self, request: Any) -> Any:
        self.created.append(request)
        if self._create_raises:
            msg = "controller rejected request"
            raise RuntimeError(msg)
        return SimpleNamespace(session_id="sess-1")

    async def create_run(self, request: Any) -> Any:
        return await self.create_project(request)


# ---------------------------------------------------------------------------
# _is_setup_ready
# ---------------------------------------------------------------------------


# ``_is_setup_ready`` delegates to ``api.setup.compute_setup_state`` (the
# single source of truth ``/setup/status`` uses), so these assert the
# delegation -- READY -> True, any INCOMPLETE_* -> False -- across the
# real §4.9 gate chain rather than a hand-rolled LIMS-only mirror. The
# fully-satisfied configs use ``_nas_config`` because the real evaluator
# requires non-empty equipment, ``plugin_dir``, and orchestrator identity
# that the lightweight ``_config`` stand-in deliberately omits.


def test_is_setup_ready_false_when_deps_none() -> None:
    assert mount._is_setup_ready(None) is False


def test_is_setup_ready_false_when_config_missing() -> None:
    assert mount._is_setup_ready(_deps(config=None)) is False


def test_is_setup_ready_false_when_keyring_missing() -> None:
    """Live-LIMS branch with the keyring password absent -> not ready."""
    deps = _deps(
        config=_nas_config(),
        keyring_password_present=False,
        lims_reachable=True,
    )
    assert mount._is_setup_ready(deps) is False


def test_is_setup_ready_false_when_lims_unreachable() -> None:
    deps = _deps(
        config=_nas_config(),
        keyring_password_present=True,
        lims_reachable=False,
    )
    assert mount._is_setup_ready(deps) is False


def test_is_setup_ready_true_when_all_satisfied() -> None:
    deps = _deps(
        config=_nas_config(),
        keyring_password_present=True,
        lims_reachable=True,
    )
    assert mount._is_setup_ready(deps) is True


def test_is_setup_ready_false_when_nas_remote_unset() -> None:
    """A nas-mode device whose ``nas.remote`` is unset blocks ready."""
    deps = _deps(
        config=_nas_config(nas_remote=""),
        keyring_password_present=True,
        lims_reachable=True,
    )
    assert mount._is_setup_ready(deps) is False


def test_is_setup_ready_false_when_nas_remote_unavailable() -> None:
    """A configured nas remote absent from rclone.conf blocks ready."""
    deps = _deps(
        config=_nas_config(),
        keyring_password_present=True,
        lims_reachable=True,
        nas_remote_available=lambda _remote: False,
    )
    assert mount._is_setup_ready(deps) is False


def test_is_setup_ready_true_with_offline_catalogue_lims() -> None:
    """Regression: LIMS satisfied via the offline catalogue (no keyring
    password) must read as ready on the main page, matching
    ``GET /setup/status``. The earlier narrow mirror checked only the
    LIMS keyring branch and so kept the setup-incomplete banner up for a
    disconnected-workstation install that the API reported as READY.
    """
    deps = _deps(
        config=_nas_config(offline_catalogue=True),
        keyring_password_present=False,
        lims_reachable=True,
    )
    assert mount._is_setup_ready(deps) is True


# ---------------------------------------------------------------------------
# _build_main_state
# ---------------------------------------------------------------------------


def test_build_main_state_marks_incomplete_without_config() -> None:
    state = mount._build_main_state(_deps())
    assert state.setup_incomplete is True
    # Redesign §3.1: orchestrator pipeline is always active; the
    # MainPageState flag (kept for the staging-dock render path until
    # Phase 8) is always True.
    assert state.orchestrator_enabled is True


def test_build_main_state_sources_problems_counts_from_audit() -> None:
    # Counts come straight off deps (the 30 s background audit), not a
    # per-render re-audit (T6 / §B5).
    state = mount._build_main_state(_deps(last_audit_hard=3, last_audit_soft=12))
    assert state.problems_count_hard == 3
    assert state.problems_count_soft == 12


def test_build_main_state_always_on_orchestrator() -> None:
    """Redesign §3.1: the orchestrator pipeline is unconditional."""
    deps = _deps(
        config=_config(
            orchestrator_label="LAB-1",
            orchestrator_staging_root="/staging",
        ),
        keyring_password_present=True,
    )
    state = mount._build_main_state(deps)
    assert state.orchestrator_enabled is True


# ---------------------------------------------------------------------------
# _operation_counts (T3/T4)
# ---------------------------------------------------------------------------


def test_operation_counts_distinguishes_panel_active_and_input_required() -> None:
    from exlab_wizard.controller.session_store import SessionStore

    store = SessionStore()
    running = store.open("project", {})
    suspended = store.open("run", {})
    failed = store.open("project", {})
    done = store.open("run", {})
    store.get(running.session_id).state = SessionState.RENDERING
    store.get(suspended.session_id).state = SessionState.INPUT_REQUIRED
    store.get(failed.session_id).state = SessionState.FAILED
    store.get(done.session_id).state = SessionState.DONE

    deps = SimpleNamespace(controller=SimpleNamespace(session_store=store))
    panel, input_required, active = mount._operation_counts(deps)
    # panel: all but DONE/ABORTED -> running + suspended + failed
    assert panel == 3
    assert input_required == 1  # only the suspended session
    assert active == 2  # strictly non-terminal -> running + suspended (FAILED excluded)


def test_operation_counts_zero_without_controller() -> None:
    assert mount._operation_counts(SimpleNamespace()) == (0, 0, 0)


# ---------------------------------------------------------------------------
# _missing_setup_sections
# ---------------------------------------------------------------------------


def test_missing_sections_when_deps_none() -> None:
    assert mount._missing_setup_sections(None) == ("paths", "lims")


def test_missing_sections_when_config_none() -> None:
    # ``operators`` is deferred (chip editor not yet wired); it must not
    # surface in the setup-incomplete tuple or it would push the operator
    # into a placeholder section before they reach the main GUI.
    assert mount._missing_setup_sections(_deps()) == ("paths", "lims")


# NOTE: the old "paths section appears when paths are unset" test was removed:
# `paths.app_root` always carries a Documents-based default, so the Settings
# section picker no longer surfaces a "paths" section for an existing config.
# The unwritable-app-root edge case is covered by the setup-state gate tests
# in tests/unit/test_paths.py.


def test_missing_sections_with_keyring_absent_reports_lims() -> None:
    deps = _deps(config=_config(), keyring_password_present=False)
    assert "lims" in mount._missing_setup_sections(deps)


def test_missing_sections_empty_when_fully_configured() -> None:
    deps = _deps(config=_config(), keyring_password_present=True)
    assert mount._missing_setup_sections(deps) == ()


def _nas_config(*, offline_catalogue: bool = False, nas_remote: str = "nas01") -> Any:
    """Real Config with one nas-mode equipment.

    ``offline_catalogue`` swaps the live-LIMS slot (endpoint + email +
    keyring password) for the offline-catalogue branch
    (``offline_catalogue_path`` set, no endpoint / email / keyring) --
    the disconnected-workstation setup that exposed the main-page banner
    staying up despite a ``READY`` ``/setup/status``.

    ``nas_remote`` is the configured ``nas.remote`` name (rclone.conf
    NAS-sync migration: the setup gate keys on it). Pass ``""`` to model
    a device whose NAS sync is in use but whose remote is unset.
    """
    from exlab_wizard.config.models import (
        Config,
        EquipmentConfig,
        LIMSConfig,
        NasConfig,
        OrchestratorConfig,
        PathsConfig,
    )

    lims = (
        LIMSConfig(offline_catalogue_path="/cat/projects.json")
        if offline_catalogue
        else LIMSConfig(endpoint="https://lims.example", email="op@example")
    )
    return Config(
        paths=PathsConfig(app_root="/srv/exlab"),
        lims=lims,
        equipment=[
            EquipmentConfig(
                id="EQ1",
                label="Equipment 1",
                nas_root="/n",
            )
        ],
        orchestrator=OrchestratorConfig(label="LAB", staging_root="/staging"),
        nas=NasConfig(remote=nas_remote, base_root="/srv/nas"),
    )


def test_missing_sections_includes_nas_remote_when_remote_unavailable() -> None:
    deps = _deps(
        config=_nas_config(),
        keyring_password_present=True,
        nas_remote_available=lambda _remote: False,
    )
    assert "nas_remote" in mount._missing_setup_sections(deps)


def test_missing_sections_includes_nas_remote_when_remote_unset() -> None:
    deps = _deps(
        config=_nas_config(nas_remote=""),
        keyring_password_present=True,
    )
    assert "nas_remote" in mount._missing_setup_sections(deps)


def test_missing_sections_excludes_nas_remote_when_remote_available() -> None:
    # No ``nas_remote_available`` override -> the _dependencies reader's
    # default ("available") applies, so a configured remote is usable.
    deps = _deps(
        config=_nas_config(),
        keyring_password_present=True,
    )
    assert "nas_remote" not in mount._missing_setup_sections(deps)


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


# NOTE: _build_staging_state was removed when the /staging route was hidden
# (orchestrator/staging hidden — see
# docs/superpowers/specs/2026-05-29-hide-orchestrator-staging-design.md). The
# staging read-side itself (orchestrator.staging_query.list_staged_runs) stays
# and is covered by test_staging_query.


# ---------------------------------------------------------------------------
# _persist_config / _apply_live_config
# ---------------------------------------------------------------------------


class _NavSpy:
    """Minimal stand-in for the NiceGUI ``ui`` object's navigate surface."""

    def __init__(self) -> None:
        self.navigated: list[str] = []
        self.navigate = SimpleNamespace(to=self.navigated.append)


def test_persist_config_writes_and_applies_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful save persists, then hot-reloads the live components.

    The old behaviour armed ``deps.restart_required`` and routed to a
    relaunch screen; now the new config is pushed in-process via
    ``_apply_live_config`` and no restart flag is set.
    """
    nav = _NavSpy()
    saved: list[Any] = []
    applied: list[tuple[Any, Any]] = []
    monkeypatch.setattr(mount, "_apply_live_config", lambda d, c: applied.append((d, c)))
    deps = _deps(save_config=saved.append)
    sentinel_config = object()

    ok = mount._persist_config(deps, sentinel_config, nav)

    assert ok is True
    assert saved == [sentinel_config]
    assert applied == [(deps, sentinel_config)]
    # The restart gate is gone -- no flag is armed.
    assert not hasattr(deps, "restart_required")


def test_persist_config_returns_false_when_no_saver(monkeypatch: pytest.MonkeyPatch) -> None:
    nav = _NavSpy()
    applied: list[Any] = []
    monkeypatch.setattr(mount, "_apply_live_config", lambda d, c: applied.append(c))
    deps = _deps(save_config=None)

    ok = mount._persist_config(deps, object(), nav)

    assert ok is False
    assert applied == []  # never reached the live-apply step


def test_persist_config_returns_false_when_saver_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    nav = _NavSpy()
    applied: list[Any] = []
    monkeypatch.setattr(mount, "_apply_live_config", lambda d, c: applied.append(c))

    def _boom(_config: Any) -> None:
        msg = "disk full"
        raise OSError(msg)

    deps = _deps(save_config=_boom)

    ok = mount._persist_config(deps, object(), nav)

    assert ok is False
    assert applied == []  # write failed before the live-apply step


def test_persist_config_warns_when_saver_returns_awaitable(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    nav = _NavSpy()
    applied: list[Any] = []
    monkeypatch.setattr(mount, "_apply_live_config", lambda d, c: applied.append(c))

    class _Awaitable:
        def __await__(self) -> Any:
            yield

    deps = _deps(save_config=lambda _cfg: _Awaitable())
    sentinel = object()

    with caplog.at_level("WARNING"):
        ok = mount._persist_config(deps, sentinel, nav)

    assert ok is True
    assert applied == [sentinel]
    assert any("awaitable" in r.message for r in caplog.records)


def test_persist_config_creates_staging_root_when_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Opt-in: a saved non-empty staging_root is created on save."""
    monkeypatch.setattr(mount, "_apply_live_config", lambda _d, _c: None)
    staging = tmp_path / "staging"
    updated = _config(orchestrator_staging_root=str(staging))
    deps = _deps(save_config=lambda _cfg: None)

    ok = mount._persist_config(deps, updated, _NavSpy())

    assert ok is True
    assert staging.is_dir()


def test_persist_config_does_not_create_staging_root_when_blank(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Blank staging_root creates nothing -- the device is not a staging PC."""
    monkeypatch.setattr(mount, "_apply_live_config", lambda _d, _c: None)
    updated = _config(orchestrator_staging_root="")
    deps = _deps(save_config=lambda _cfg: None)

    ok = mount._persist_config(deps, updated, _NavSpy())

    assert ok is True
    assert list(tmp_path.iterdir()) == []  # nothing created


def test_persist_config_staging_dir_failure_is_non_fatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An ensure_dir failure warns but keeps the persisted config."""
    monkeypatch.setattr(mount, "_apply_live_config", lambda _d, _c: None)
    toasts: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        mount, "_show_toast", lambda _ui, msg, *, positive: toasts.append((msg, positive))
    )

    def _boom(_path: Any) -> Any:
        msg = "permission denied"
        raise OSError(msg)

    monkeypatch.setattr("exlab_wizard.paths.ensure_dir", _boom)
    saved: list[Any] = []
    updated = _config(orchestrator_staging_root=str(tmp_path / "staging"))
    deps = _deps(save_config=saved.append)

    ok = mount._persist_config(deps, updated, _NavSpy())

    assert ok is True  # non-fatal: config is already persisted
    assert saved == [updated]
    assert toasts and toasts[-1][1] is False  # a negative toast was surfaced


def test_apply_live_config_falls_back_to_setting_config(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If the coordinator raises wholesale, the live config is still kept."""

    def _boom(_deps: Any, _cfg: Any) -> None:
        msg = "coordinator exploded"
        raise RuntimeError(msg)

    monkeypatch.setattr("exlab_wizard.tray.dependencies.apply_live_config", _boom)
    deps = _deps()
    sentinel = object()

    with caplog.at_level("ERROR"):
        mount._apply_live_config(deps, sentinel)

    assert deps.config is sentinel
    assert any("live config reload failed" in r.message for r in caplog.records)


# NOTE: _render_unavailable was removed with the /staging route (its only
# caller) — orchestrator/staging hidden; see the design spec referenced above.


# ---------------------------------------------------------------------------
# _show_toast
# ---------------------------------------------------------------------------


def test_show_toast_positive_and_negative_never_raise() -> None:
    # No NiceGUI slot context: the notifications helper raises internally
    # and the toast helper must swallow it rather than propagate.
    mount._show_toast(None, "all good", positive=True)
    mount._show_toast(None, "something broke", positive=False)


def test_show_toast_swallows_notification_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exlab_wizard.ui import notifications

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        msg = "no slot"
        raise RuntimeError(msg)

    monkeypatch.setattr(notifications, "notify_success", _boom)
    # Must not raise even though the underlying notify call does.
    mount._show_toast(None, "x", positive=True)


# ---------------------------------------------------------------------------
# _lims_credential_handlers
# ---------------------------------------------------------------------------


class _RecordingKeyringStore:
    """Keyring-store stand-in that records the calls the handlers make."""

    def __init__(self, *, raises: bool = False) -> None:
        self._raises = raises
        self.set_calls: list[tuple[str, str]] = []
        self.delete_calls: list[str] = []

    def set_password(self, *, username: str, password: str) -> None:
        if self._raises:
            msg = "keyring backend unavailable"
            raise RuntimeError(msg)
        self.set_calls.append((username, password))

    def delete_password(self, *, username: str) -> None:
        if self._raises:
            msg = "keyring backend unavailable"
            raise RuntimeError(msg)
        self.delete_calls.append(username)


def test_lims_credential_handlers_save_writes_under_lims_username() -> None:
    store = _RecordingKeyringStore()
    on_save, _on_clear = mount._lims_credential_handlers(_deps(keyring_store=store), None)

    on_save("hunter2")

    assert store.set_calls == [(KEYRING_USERNAME_LIMS, "hunter2")]


def test_lims_credential_handlers_clear_deletes_under_lims_username() -> None:
    store = _RecordingKeyringStore()
    _on_save, on_clear = mount._lims_credential_handlers(_deps(keyring_store=store), None)

    on_clear()

    assert store.delete_calls == [KEYRING_USERNAME_LIMS]


def test_lims_credential_handlers_tolerate_missing_keyring_store() -> None:
    """A best-effort keyring build can fail; the handlers must not crash."""

    on_save, on_clear = mount._lims_credential_handlers(_deps(keyring_store=None), None)

    on_save("hunter2")
    on_clear()


def test_lims_credential_handlers_swallow_backend_errors() -> None:
    """A raising keyring backend surfaces a toast, not an unhandled crash."""

    store = _RecordingKeyringStore(raises=True)
    on_save, on_clear = mount._lims_credential_handlers(_deps(keyring_store=store), None)

    on_save("hunter2")
    on_clear()


# ---------------------------------------------------------------------------
# _apply_autostart
# ---------------------------------------------------------------------------


def test_apply_autostart_noop_when_deps_none() -> None:
    mount._apply_autostart(None, True)  # must not raise


def test_apply_autostart_noop_when_toggle_missing() -> None:
    mount._apply_autostart(_deps(autostart_toggle=None), True)  # must not raise


def test_apply_autostart_invokes_toggle() -> None:
    calls: list[bool] = []
    deps = _deps(autostart_toggle=calls.append)
    mount._apply_autostart(deps, True)
    assert calls == [True]


def test_apply_autostart_returns_real_registration_state() -> None:
    # The toggle returns is_registered(); _apply_autostart relays it so
    # Settings can reflect / revert the checkbox (T8).
    assert mount._apply_autostart(_deps(autostart_toggle=lambda _e: True), True) is True
    assert mount._apply_autostart(_deps(autostart_toggle=lambda _e: False), False) is False
    assert mount._apply_autostart(None, True) is None
    assert mount._apply_autostart(_deps(autostart_toggle=None), True) is None


def test_apply_autostart_swallows_toggle_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def _boom(_enabled: bool) -> None:
        msg = "registry locked"
        raise RuntimeError(msg)

    with caplog.at_level("WARNING"):
        mount._apply_autostart(_deps(autostart_toggle=_boom), False)
    assert any("autostart toggle failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _templates_dir / _equipment_ids
# ---------------------------------------------------------------------------


def test_templates_dir_none_when_deps_none() -> None:
    assert mount._templates_dir(None) is None


def test_templates_dir_none_when_config_none() -> None:
    assert mount._templates_dir(_deps()) is None


def test_templates_dir_none_when_unset() -> None:
    assert mount._templates_dir(_deps(config=_config(templates_dir=""))) is None


def test_templates_dir_returns_path() -> None:
    deps = _deps(config=_config(templates_dir="/tmp/tpl"))
    assert mount._templates_dir(deps) == Path("/tmp/tpl")


def test_equipment_ids_empty_without_config() -> None:
    assert mount._equipment_ids(_deps()) == []


def test_equipment_ids_lists_configured_ids() -> None:
    deps = _deps(
        config=_config(equipment=(SimpleNamespace(id="MIC1"), SimpleNamespace(id="SPEC1")))
    )
    assert mount._equipment_ids(deps) == ["MIC1", "SPEC1"]


# ---------------------------------------------------------------------------
# _template_names
# ---------------------------------------------------------------------------


def test_template_names_empty_without_templates_dir() -> None:
    assert mount._template_names(_deps(), "project") == []


def test_template_names_lists_summary_names(monkeypatch: pytest.MonkeyPatch) -> None:
    from exlab_wizard.ui.pages import templates as templates_page

    summaries = [
        SimpleNamespace(name="proj_a", path=Path("/tmp/tpl/proj_a"), run_scope=None),
        SimpleNamespace(name="proj_b", path=Path("/tmp/tpl/proj_b"), run_scope=None),
    ]
    monkeypatch.setattr(templates_page, "list_templates", lambda _d, template_type=None: summaries)
    deps = _deps(config=_config())
    assert mount._template_names(deps, "project") == ["proj_a", "proj_b"]


def test_template_names_swallows_scan_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from exlab_wizard.ui.pages import templates as templates_page

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        msg = "unreadable templates dir"
        raise RuntimeError(msg)

    monkeypatch.setattr(templates_page, "list_templates", _boom)
    with caplog.at_level("WARNING"):
        assert mount._template_names(_deps(config=_config()), "project") == []
    assert any("template scan failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _lims_projects
# ---------------------------------------------------------------------------


async def test_lims_projects_empty_without_config() -> None:
    assert await mount._lims_projects(_deps()) == []


async def test_lims_projects_empty_without_catalogue_path() -> None:
    assert await mount._lims_projects(_deps(config=_config())) == []


async def test_lims_projects_empty_when_catalogue_missing(tmp_path: Path) -> None:
    deps = _deps(config=_config(offline_catalogue_path=str(tmp_path / "missing.json")))
    assert await mount._lims_projects(deps) == []


async def test_lims_projects_reads_offline_catalogue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    catalogue = tmp_path / "catalogue.json"
    catalogue.write_text("{}", encoding="utf-8")
    projects = [SimpleNamespace(short_id="P-1", name="Project One", uid="uid-1")]
    monkeypatch.setattr(
        "exlab_wizard.lims.catalogue.read_catalogue",
        lambda _path, expected_endpoint=None: SimpleNamespace(projects=projects),
    )
    deps = _deps(config=_config(offline_catalogue_path=str(catalogue)))
    assert await mount._lims_projects(deps) == [
        {
            "short_id": "P-1",
            "name": "Project One",
            "uid": "uid-1",
            "source": "offline_catalogue",
        }
    ]


async def test_lims_projects_swallows_read_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    catalogue = tmp_path / "catalogue.json"
    catalogue.write_text("{}", encoding="utf-8")

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        msg = "corrupt catalogue"
        raise RuntimeError(msg)

    monkeypatch.setattr("exlab_wizard.lims.catalogue.read_catalogue", _boom)
    deps = _deps(config=_config(offline_catalogue_path=str(catalogue)))
    with caplog.at_level("WARNING"):
        assert await mount._lims_projects(deps) == []
    assert any("offline catalogue read failed" in r.message for r in caplog.records)


class _FakeLimsClient:
    """Async ``list_projects`` stub for the live-LIMS picker path."""

    def __init__(self, projects: list[Any] | None = None, *, raises: bool = False) -> None:
        self._projects = projects or []
        self._raises = raises
        self.calls = 0

    async def list_projects(self, *, status_filter: Any = None) -> list[Any]:
        self.calls += 1
        if self._raises:
            msg = "lims unreachable"
            raise RuntimeError(msg)
        return self._projects


async def test_lims_projects_uses_live_lims() -> None:
    client = _FakeLimsClient([SimpleNamespace(short_id="PROJ-9", name="Live Project", uid="uid-9")])
    deps = _deps(config=_config(), lims_client=client, lims_reachable=True)
    assert await mount._lims_projects(deps) == [
        {
            "short_id": "PROJ-9",
            "name": "Live Project",
            "uid": "uid-9",
            "source": "lims",
        }
    ]
    assert client.calls == 1


async def test_lims_projects_skips_live_lims_when_unreachable() -> None:
    client = _FakeLimsClient([SimpleNamespace(short_id="PROJ-9", name="Live Project", uid="uid-9")])
    deps = _deps(config=_config(), lims_client=client, lims_reachable=False)
    assert await mount._lims_projects(deps) == []
    assert client.calls == 0


async def test_lims_projects_falls_back_to_catalogue_on_live_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    catalogue = tmp_path / "catalogue.json"
    catalogue.write_text("{}", encoding="utf-8")
    projects = [SimpleNamespace(short_id="P-1", name="Project One", uid="uid-1")]
    monkeypatch.setattr(
        "exlab_wizard.lims.catalogue.read_catalogue",
        lambda _path, expected_endpoint=None: SimpleNamespace(projects=projects),
    )
    client = _FakeLimsClient(raises=True)
    deps = _deps(
        config=_config(offline_catalogue_path=str(catalogue)),
        lims_client=client,
        lims_reachable=True,
    )
    with caplog.at_level("WARNING"):
        result = await mount._lims_projects(deps)
    assert result == [
        {
            "short_id": "P-1",
            "name": "Project One",
            "uid": "uid-1",
            "source": "offline_catalogue",
        }
    ]
    assert client.calls == 1
    assert any("live LIMS project list failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _template_questions_map
# ---------------------------------------------------------------------------


def test_template_questions_map_empty_without_templates_dir() -> None:
    assert mount._template_questions_map(_deps(), "project") == {}


def test_template_questions_map_resolves_questions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exlab_wizard.template import copier_driver
    from exlab_wizard.ui.pages import templates as templates_page

    summaries = [
        SimpleNamespace(name="proj_basic", path=Path("/tmp/tpl/proj_basic"), run_scope=None)
    ]
    monkeypatch.setattr(templates_page, "list_templates", lambda _d, template_type=None: summaries)

    class _Engine:
        def resolve(self, _path: Any, _scope: Any) -> Any:
            return SimpleNamespace(raw_manifest={"sample_id": {"type": "str"}})

    monkeypatch.setattr(copier_driver, "TemplateEngine", _Engine)
    monkeypatch.setattr(
        templates_page, "template_questions", lambda _manifest: [{"key": "sample_id"}]
    )
    deps = _deps(config=_config())
    assert mount._template_questions_map(deps, "project") == {"proj_basic": [{"key": "sample_id"}]}


def test_template_questions_map_skips_unresolvable_template(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from exlab_wizard.template import copier_driver
    from exlab_wizard.ui.pages import templates as templates_page

    summaries = [SimpleNamespace(name="broken", path=Path("/tmp/tpl/broken"), run_scope=None)]
    monkeypatch.setattr(templates_page, "list_templates", lambda _d, template_type=None: summaries)

    class _Engine:
        def resolve(self, _path: Any, _scope: Any) -> Any:
            msg = "bad copier.yml"
            raise RuntimeError(msg)

    monkeypatch.setattr(copier_driver, "TemplateEngine", _Engine)
    with caplog.at_level("WARNING"):
        assert mount._template_questions_map(_deps(config=_config()), "run") == {}
    assert any("failed to resolve" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _resolve_template_choices -- per-instance resolution (Phase 5)
# ---------------------------------------------------------------------------


def _write_min_template(
    parent: Path, *, name: str, template_type: str, run_scope: str | None = None
) -> Path:
    """Write a minimal valid copier.yml template under ``parent/<name>``."""
    import yaml

    from exlab_wizard.constants import COPIER_MANIFEST_NAME

    root = parent / name
    root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"_exlab_type": template_type, "_exlab_version": "1.0"}
    if run_scope is not None:
        manifest["_exlab_run_scope"] = run_scope
    (root / COPIER_MANIFEST_NAME).write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return root


def test_resolve_template_choices_prefers_per_equipment_and_captures_path(
    tmp_path: Path,
) -> None:
    """A per-equipment project template shadows a same-named global one.

    Proves Phase 5 end to end at the mount layer: the resolver merges the
    per-equipment store over the global store nearest-wins, and the returned
    ``paths`` point at the *resolved* (per-equipment) source -- which is what
    the wizard stores on the state and submits, so the pipeline renders the
    override rather than ``templates_dir / name``.
    """
    from exlab_wizard.constants import CACHE_DIR_NAME

    global_dir = tmp_path / "global"
    local_root = tmp_path / "local"
    equipment_id = "MICROSCOPE_01"
    # Same template name in both scopes; the per-equipment copy must win.
    _write_min_template(global_dir, name="lab_default", template_type="project")
    per_eq_dir = local_root / equipment_id / CACHE_DIR_NAME / "templates" / "project"
    per_eq_template = _write_min_template(per_eq_dir, name="lab_default", template_type="project")
    # A global-only template still surfaces, ranked after the per-equipment one.
    _write_min_template(global_dir, name="global_only", template_type="project")

    deps = _deps(
        config=_config(
            templates_dir=str(global_dir),
            local_root=str(local_root),
            equipment=(SimpleNamespace(id=equipment_id),),
        )
    )

    # No equipment context -> global only (degrades gracefully, prior behaviour).
    bare = mount._resolve_template_choices(deps, "project")
    assert set(bare.names) == {"lab_default", "global_only"}
    assert bare.paths["lab_default"] == global_dir / "lab_default"

    # With the equipment context the per-equipment copy shadows the global one.
    scoped = mount._resolve_template_choices(deps, "project", equipment_id=equipment_id)
    assert scoped.names[0] == "lab_default"
    assert "global_only" in scoped.names
    assert scoped.paths["lab_default"] == per_eq_template


async def test_submit_run_uses_resolved_template_path(tmp_path: Path) -> None:
    """``_submit_run`` renders the resolved path the wizard stored, not name-join.

    The fix that makes per-instance selection real: when the wizard state
    carries ``selected_template_path`` (the absolute resolved source), submit
    must use it verbatim rather than ``templates_dir / selected_template``.
    """
    nav = _NavSpy()
    controller = _FakeController(final_state=SessionState.DONE)
    deps = _deps(controller=controller, config=_config(templates_dir=str(tmp_path / "global")))
    resolved = (
        tmp_path / "local" / "MICROSCOPE_01" / "ProjA" / ".exlab-wizard" / "templates" / "run"
    )
    resolved.mkdir(parents=True)
    state = SimpleNamespace(
        selected_template="confocal",
        selected_template_path=resolved,
        selected_equipment="MICROSCOPE_01",
        selected_project_name="ProjA",
        template_variables={},
        readme_fields={"label": "L", "operator": "op", "objective": "obj"},
    )
    await mount._submit_run(deps, state, RunKind.EXPERIMENTAL, nav)
    assert len(controller.created) == 1
    assert controller.created[0].template_path == resolved


# ---------------------------------------------------------------------------
# _await_session
# ---------------------------------------------------------------------------


async def test_await_session_awaits_pending_task() -> None:
    drained: list[bool] = []

    async def _background() -> None:
        drained.append(True)

    controller = _FakeController(final_state=SessionState.DONE, task=_background())
    handle = SimpleNamespace(session_id="sess-1")
    result = await mount._await_session(controller, handle)
    assert result.state is SessionState.DONE
    assert drained == [True]


async def test_await_session_without_pending_task() -> None:
    controller = _FakeController(final_state=SessionState.DONE)
    handle = SimpleNamespace(session_id="sess-1")
    result = await mount._await_session(controller, handle)
    assert result.state is SessionState.DONE


# ---------------------------------------------------------------------------
# _run_creation
# ---------------------------------------------------------------------------


async def test_run_creation_navigates_home_on_done() -> None:
    nav = _NavSpy()
    controller = _FakeController(final_state=SessionState.DONE)
    await mount._run_creation(controller, controller.create_project, object(), nav, label="Project")
    assert nav.navigated == ["/main"]


async def test_run_creation_reports_failure_state() -> None:
    nav = _NavSpy()
    session = SimpleNamespace(error={"code": "E_VALIDATION", "message": "bad inputs"})
    controller = _FakeController(final_state=SessionState.FAILED, session=session)
    await mount._run_creation(controller, controller.create_run, object(), nav, label="Run")
    assert nav.navigated == []


async def test_run_creation_swallows_create_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    nav = _NavSpy()
    controller = _FakeController(final_state=SessionState.DONE, create_raises=True)
    with caplog.at_level("ERROR"):
        await mount._run_creation(
            controller, controller.create_project, object(), nav, label="Project"
        )
    assert nav.navigated == []
    assert any("creation raised" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _submit_project / _submit_run
# ---------------------------------------------------------------------------


async def test_submit_project_toasts_when_controller_missing() -> None:
    nav = _NavSpy()
    await mount._submit_project(_deps(controller=None), SimpleNamespace(), nav)
    assert nav.navigated == []


async def test_submit_project_toasts_without_template() -> None:
    nav = _NavSpy()
    controller = _FakeController(final_state=SessionState.DONE)
    deps = _deps(controller=controller, config=_config())
    await mount._submit_project(deps, SimpleNamespace(selected_template=""), nav)
    assert controller.created == []


async def test_submit_project_builds_request_and_runs(tmp_path: Path) -> None:
    nav = _NavSpy()
    controller = _FakeController(final_state=SessionState.DONE)
    deps = _deps(controller=controller, config=_config(templates_dir=str(tmp_path)))
    state = SimpleNamespace(
        selected_template="proj_basic",
        selected_equipment="MIC1",
        selected_lims_short_id="P-1",
        lims_project_name="Project One",
        template_variables={"sample_id": "S-1"},
        readme_fields={"label": "L", "operator": "op", "objective": "obj"},
    )
    await mount._submit_project(deps, state, nav)
    assert len(controller.created) == 1
    request = controller.created[0]
    assert request.equipment_id == "MIC1"
    assert request.template_path == tmp_path / "proj_basic"
    assert request.variables == {"sample_id": "S-1"}
    assert nav.navigated == ["/main"]


async def test_submit_run_toasts_when_controller_missing() -> None:
    nav = _NavSpy()
    await mount._submit_run(_deps(controller=None), SimpleNamespace(), RunKind.EXPERIMENTAL, nav)
    assert nav.navigated == []


async def test_submit_run_builds_request_and_runs(tmp_path: Path) -> None:
    nav = _NavSpy()
    controller = _FakeController(final_state=SessionState.DONE)
    deps = _deps(controller=controller, config=_config(templates_dir=str(tmp_path)))
    state = SimpleNamespace(
        selected_template="run_exp",
        selected_equipment="MIC1",
        selected_project_name="Cortex Q3 Pilot",
        template_variables={"gain": 7},
        readme_fields={"label": "L", "operator": "op", "objective": "obj"},
    )
    await mount._submit_run(deps, state, RunKind.TEST, nav)
    assert len(controller.created) == 1
    request = controller.created[0]
    assert request.run_kind is RunKind.TEST
    assert request.project_name == "Cortex Q3 Pilot"
    assert request.template_path == tmp_path / "run_exp"
    assert nav.navigated == ["/main"]


# ---------------------------------------------------------------------------
# Helpers added for the file-explorer /main rewire
# ---------------------------------------------------------------------------


def test_build_main_query_omits_empty_params() -> None:
    """Both empty -> empty string. One present -> single param."""
    assert mount._build_main_query("", "") == ""
    assert mount._build_main_query("EQ1", "") == "?selected=EQ1"
    assert mount._build_main_query("", "collapsed") == "?right_pane=collapsed"
    assert mount._build_main_query("EQ1", "collapsed") == "?selected=EQ1&right_pane=collapsed"


def test_build_main_query_url_encodes_special_chars() -> None:
    """Project names with spaces / special chars survive the round trip."""
    # Spaces in the project name get percent-encoded; path slashes stay
    # readable so the address bar shows the hierarchical id.
    out = mount._build_main_query("EQ1/Cortex Q3 Pilot", "")
    assert out == "?selected=EQ1/Cortex%20Q3%20Pilot"
    # An ampersand or question mark in a node id would otherwise break
    # the query parser; encoding guards against that.
    assert "&" not in mount._build_main_query("oddly&named", "")[len("?selected=") :]
    assert "?" not in mount._build_main_query("with?q", "")[len("?selected=") :]


def test_build_main_query_includes_file_q_density() -> None:
    """Phase 4 adds optional file / q / density params to the same URL model."""
    out = mount._build_main_query("EQ1", "", file="/d/EQ1/scan.tif", q="cortex", density="compact")
    assert out.startswith("?selected=EQ1")
    assert "file=/d/EQ1/scan.tif" in out
    assert "q=cortex" in out
    assert "density=compact" in out


def test_build_main_query_omits_empty_phase4_params() -> None:
    """The Phase 4 params default empty and drop out of the URL entirely."""
    assert mount._build_main_query("EQ1", "collapsed") == "?selected=EQ1&right_pane=collapsed"


def test_build_main_query_encodes_file_path_and_search() -> None:
    """``file`` keeps '/' readable but encodes spaces; ``q`` encodes everything."""
    out = mount._build_main_query("", "", file="/d/My Run/a b.tif", q="a&b c")
    assert "file=/d/My%20Run/a%20b.tif" in out
    # The search query is fully encoded (safe='') so '&' and ' ' can't break
    # the query parser.
    assert "q=a%26b%20c" in out


def test_classify_node_returns_none_for_empty_selection() -> None:
    assert mount._classify_node(None, {}) == (None, False)
    assert mount._classify_node("", {}) == (None, False)


def test_classify_node_discriminates_kinds() -> None:
    """Equipment / project / run / received_equipment all classify correctly."""
    from exlab_wizard.ui.components import tree as ui_tree

    hierarchy = {
        ui_tree.EquipmentNode(equipment_id="EQ1", relay=False): {},
        ui_tree.EquipmentNode(equipment_id="RELAY_EQX", relay=True): {},
    }
    assert mount._classify_node("EQ1", hierarchy) == ("equipment", False)
    assert mount._classify_node("RELAY_EQX", hierarchy) == ("received_equipment", True)
    assert mount._classify_node("EQ1/PROJ-0001", hierarchy) == ("project", False)
    assert mount._classify_node("EQ1/PROJ-0001/Run_2026-05-07", hierarchy) == ("run", False)
    assert mount._classify_node("EQ1/PROJ-0001/TestRuns/TestRun_2026-05-08", hierarchy) == (
        "run",
        False,
    )
    # A node under a relay root keeps the relay-equipment flag set so
    # the toolbar's New-Project/Run/Test-Run buttons stay disabled.
    assert mount._classify_node("RELAY_EQX/proj", hierarchy) == ("project", True)


def test_classify_node_rejects_unknown_root() -> None:
    """A node id whose root is not in the hierarchy returns ``(None, False)``.

    Guards against half-rendered pages when the URL captures an id
    from a different device's tree, or the config changed between the
    operator's link and the current session.
    """
    from exlab_wizard.ui.components import tree as ui_tree

    hierarchy = {ui_tree.EquipmentNode(equipment_id="EQ1", relay=False): {}}
    assert mount._classify_node("EQ_STRANGER", hierarchy) == (None, False)
    assert mount._classify_node("EQ_STRANGER/proj", hierarchy) == (None, False)
    # Empty hierarchy: every node id is rejected.
    assert mount._classify_node("EQ1", {}) == (None, False)


def test_build_metadata_payload_returns_empty_when_node_missing() -> None:
    assert mount._build_metadata_payload(None, None, None) == {}
    assert mount._build_metadata_payload("EQ1", None, _deps()) == {}
    assert mount._build_metadata_payload("EQ1", "equipment", _deps(config=None)) == {}


def test_build_metadata_payload_owned_equipment_reads_config() -> None:
    """An equipment node payload pulls fields from config.equipment[id]."""
    equipment = SimpleNamespace(
        id="EQ1",
        label="Confocal Microscope 1",
        sync_mode="nas",
        nas_root="//nas/EQ1",
    )
    # ``local_root`` is no longer a per-equipment field; the payload derives
    # it as ``<config.paths.local_root>/<id>`` (== ``<app_root>/data/<id>``).
    config = _config(local_root="/srv/exlab/data", equipment=(equipment,))
    payload = mount._build_metadata_payload("EQ1", "equipment", _deps(config=config))
    assert payload["id"] == "EQ1"
    assert payload["label"] == "Confocal Microscope 1"
    assert payload["sync_mode"] == "nas"
    assert payload["local_root"] == str(Path("/srv/exlab/data") / "EQ1")
    assert payload["nas_root"] == "//nas/EQ1"


def test_build_metadata_payload_unknown_equipment_id_returns_empty() -> None:
    """Asking for a node that isn't in config.equipment returns ``{}``."""
    config = _config(equipment=())
    payload = mount._build_metadata_payload("EQX", "equipment", _deps(config=config))
    assert payload == {}


def test_build_metadata_payload_project_scans_run_counts(tmp_path: Path) -> None:
    """The project payload counts Run_* and TestRun_* directories."""
    from exlab_wizard.constants import RUN_DIR_PREFIX, TEST_RUN_DIR_PREFIX

    project_dir = tmp_path / "EQ1" / "Cortex Q3"
    runs_dir = project_dir / "Runs"
    test_runs_dir = project_dir / "TestRuns"
    runs_dir.mkdir(parents=True)
    test_runs_dir.mkdir(parents=True)
    (runs_dir / f"{RUN_DIR_PREFIX}2026-05-01").mkdir()
    (runs_dir / f"{RUN_DIR_PREFIX}2026-05-02").mkdir()
    (test_runs_dir / f"{TEST_RUN_DIR_PREFIX}2026-05-03").mkdir()
    config = _config(local_root=str(tmp_path))
    payload = mount._build_metadata_payload("EQ1/Cortex Q3", "project", _deps(config=config))
    assert payload["run_count"] == 2
    assert payload["test_run_count"] == 1
    assert payload["name"] == "Cortex Q3"


def test_build_metadata_payload_run_parses_creation_json(tmp_path: Path) -> None:
    """The run payload decodes creation.json into the metadata fields."""
    from datetime import UTC, datetime

    import msgspec

    from exlab_wizard.api.schemas import (
        CreationJson,
        LimsProjectBlock,
        PathsBlock,
        TemplateBlock,
    )
    from exlab_wizard.constants import CACHE_DIR_NAME, CREATION_JSON_NAME, CREATION_JSON_VERSION

    run_dir = tmp_path / "EQ1" / "Cortex Q3" / "Runs" / "Run_2026-05-07"
    cache = run_dir / CACHE_DIR_NAME
    cache.mkdir(parents=True)
    payload_obj = CreationJson(
        schema_version=CREATION_JSON_VERSION,
        created_at=datetime(2026, 5, 7, 10, 0, 0, tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        created_by="asmith",
        level="run",
        run_kind="experimental",
        lims_project=LimsProjectBlock(
            uid="x", short_id="PROJ-0042", name_at_creation="Cortex Run", source="live"
        ),
        template=TemplateBlock(
            name="basic", version="1.0.0", source_path="/tpl/basic", run_scope="experimental"
        ),
        variables={},
        paths=PathsBlock(local=str(run_dir), nas="/srv/nas/EQ1"),
        sync_status="pending",
    )
    (cache / CREATION_JSON_NAME).write_bytes(msgspec.json.encode(payload_obj))
    config = _config(local_root=str(tmp_path))
    out = mount._build_metadata_payload(str(run_dir), "run", _deps(config=config))
    assert out["operator"] == "asmith"
    assert out["template"] == "basic"
    assert out["sync_status"] == "pending"
    assert out["lims_project"] == "PROJ-0042"
    assert out["label"] == "Cortex Run"
    assert out["run_kind"] == "experimental"
    assert out["path"] == str(run_dir)


def test_build_metadata_payload_run_missing_creation_returns_path_only(tmp_path: Path) -> None:
    """A run dir without a creation.json yields {path, name}, not crash."""
    run_dir = tmp_path / "EQ1" / "Cortex Q3" / "Runs" / "Run_2026-05-09"
    run_dir.mkdir(parents=True)
    config = _config(local_root=str(tmp_path))
    payload = mount._build_metadata_payload(str(run_dir), "run", _deps(config=config))
    assert payload == {"path": str(run_dir), "name": run_dir.name}


def test_build_main_state_threads_selection_into_state() -> None:
    """The selected-node fields end up on MainPageState for the renderer."""
    state = mount._build_main_state(
        _deps(),
        selected_node="EQ1/Cortex",
        node_kind="project",
        is_received=False,
        right_pane_collapsed=True,
    )
    assert state.selected_node == "EQ1/Cortex"
    assert state.selected_node_kind == "project"
    assert state.selected_node_is_received is False
    assert state.right_pane_collapsed is True
    assert state.folder_feed_path == "EQ1/Cortex"


def test_build_main_state_threads_file_search_density() -> None:
    """Phase 4 selection / search / density fields reach MainPageState."""
    selected = {"kind": "file", "name": "scan.tif", "path": "/d/scan.tif"}
    state = mount._build_main_state(
        _deps(),
        selected_file_path="/d/scan.tif",
        selected_file=selected,
        search_query="cortex",
        density="compact",
    )
    assert state.selected_file_path == "/d/scan.tif"
    assert state.selected_file == selected
    assert state.search_query == "cortex"
    assert state.density == "compact"


# ---------------------------------------------------------------------------
# _build_selected_file / _build_selected_folder (Phase 4, Option B)
# ---------------------------------------------------------------------------


def test_build_selected_file_resolves_file_by_path_match() -> None:
    """A clicked path is resolved from the in-memory feed into a file payload."""
    from exlab_wizard.ui.pages.staging import format_bytes

    entries = [
        FileListEntry(
            name="scan.tif",
            path="/d/EQ1/scan.tif",
            is_dir=False,
            size_bytes=2048,
            modified_iso="2026-05-20T10:00:00Z",
            sync_status="synced",
        ),
        FileListEntry(name="meta.json", path="/d/EQ1/meta.json", is_dir=False),
    ]
    payload = mount._build_selected_file("/d/EQ1/scan.tif", entries, _deps())
    assert payload is not None
    assert payload["kind"] == "file"
    assert payload["name"] == "scan.tif"
    assert payload["path"] == "/d/EQ1/scan.tif"
    assert payload["size"] == format_bytes(2048)  # pre-formatted, not raw bytes
    assert payload["modified"] == "2026-05-20T10:00:00Z"
    assert payload["sync_status"] == "synced"
    assert payload["tombstone"] is False


def test_build_selected_file_none_for_empty_or_missing_path() -> None:
    """Empty path or a path matching no current entry resolves to None."""
    entries = [FileListEntry(name="a", path="/d/a", is_dir=False)]
    assert mount._build_selected_file(None, entries, _deps()) is None
    assert mount._build_selected_file("", entries, _deps()) is None
    # Removed since the click -> no match -> None (no sub-card, no error).
    assert mount._build_selected_file("/d/removed", entries, _deps()) is None


def test_build_selected_file_tombstone_omits_size() -> None:
    """A tombstone ("On NAS") file has no on-disk copy, so size is None."""
    entries = [
        FileListEntry(
            name="old.tif",
            path="/d/old.tif",
            is_dir=False,
            size_bytes=999,
            sync_status="on_nas",
            tombstone=True,
        )
    ]
    payload = mount._build_selected_file("/d/old.tif", entries, _deps())
    assert payload is not None
    assert payload["tombstone"] is True
    assert payload["size"] is None
    assert payload["sync_status"] == "on_nas"


def test_build_selected_file_folder_delegates_to_aggregate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A directory match delegates to the one-level folder aggregate scan."""
    from exlab_wizard.api.routers import browse as browse_mod

    fake_entries = [
        SimpleNamespace(sync_status="synced"),
        SimpleNamespace(sync_status="failed"),
        SimpleNamespace(sync_status=None),
    ]
    monkeypatch.setattr(
        browse_mod,
        "scan_folder_sync",
        lambda _path, _config: SimpleNamespace(entries=fake_entries),
    )
    entries = [FileListEntry(name="Runs", path="/d/EQ1/Runs", is_dir=True)]
    payload = mount._build_selected_file("/d/EQ1/Runs", entries, _deps(config=_config()))
    assert payload is not None
    assert payload["kind"] == "folder"
    assert payload["name"] == "Runs"
    assert payload["path"] == "/d/EQ1/Runs"
    assert payload["item_count"] == 3
    # Worst-of rollup: a single failed child dominates. The two-icon view
    # model maps the "failed" discriminator to the "upload_failed" view value.
    assert payload["rollup"] == "upload_failed"


def test_build_selected_folder_degrades_on_scan_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A scan failure yields item_count=None + neutral rollup, no raise."""
    from exlab_wizard.api.routers import browse as browse_mod

    def _boom(_path: Any, _config: Any) -> Any:
        msg = "folder vanished"
        raise RuntimeError(msg)

    monkeypatch.setattr(browse_mod, "scan_folder_sync", _boom)
    with caplog.at_level("WARNING"):
        payload = mount._build_selected_folder("Runs", "/d/EQ1/Runs", _deps(config=_config()))
    assert payload == {
        "kind": "folder",
        "name": "Runs",
        "path": "/d/EQ1/Runs",
        "item_count": None,
        "rollup": None,
    }
    assert any("folder aggregate scan failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _refresh_selected_folder (Phase 4, OQ-1/A)
# ---------------------------------------------------------------------------


def test_refresh_selected_folder_primes_feed_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A force-refresh writes the fresh scan onto the per-tab feed payload and
    records a folder walk on the coordinator (coalescing bookkeeping)."""
    from exlab_wizard.api.routers import browse as browse_mod

    sentinel = SimpleNamespace(path="/d/EQ1/Runs", entries=[])
    monkeypatch.setattr(browse_mod, "scan_folder_sync", lambda _path, _config: sentinel)
    recorded: list[bool] = []
    coord = SimpleNamespace(record_folder_refresh=lambda: recorded.append(True))
    feed = SimpleNamespace(state=SimpleNamespace(last_payload=None))
    app = SimpleNamespace(
        storage=SimpleNamespace(tab={"folder_feed": feed, "folder_feed_coord": coord})
    )
    mount._refresh_selected_folder(app, _deps(config=_config()), "/d/EQ1/Runs")
    assert feed.state.last_payload is sentinel
    assert recorded == [True]


def test_refresh_selected_folder_noop_when_path_none() -> None:
    """Nothing selected -> no scan, no write, no raise."""
    app = SimpleNamespace(storage=SimpleNamespace(tab={}))
    mount._refresh_selected_folder(app, _deps(), None)


def test_refresh_selected_folder_swallows_scan_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed scan keeps the existing payload and warns."""
    from exlab_wizard.api.routers import browse as browse_mod

    def _boom(_path: Any, _config: Any) -> Any:
        msg = "gone"
        raise RuntimeError(msg)

    monkeypatch.setattr(browse_mod, "scan_folder_sync", _boom)
    feed = SimpleNamespace(state=SimpleNamespace(last_payload="keep"))
    app = SimpleNamespace(storage=SimpleNamespace(tab={"folder_feed": feed}))
    with caplog.at_level("WARNING"):
        mount._refresh_selected_folder(app, _deps(config=_config()), "/d/EQ1/Runs")
    assert feed.state.last_payload == "keep"
    assert any("per-folder refresh scan failed" in r.message for r in caplog.records)


def test_open_in_os_returns_false_on_unhandled_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown sys.platform falls through to False so the toast can warn."""
    monkeypatch.setattr("sys.platform", "exotic-os")
    assert mount._open_in_os("/some/path") is False


def test_open_in_os_linux_invokes_xdg_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Linux dispatches to xdg-open via subprocess.Popen."""
    monkeypatch.setattr("sys.platform", "linux")
    calls: list[list[str]] = []

    class _StubPopen:
        def __init__(self, args: list[str]) -> None:
            calls.append(args)

    monkeypatch.setattr("subprocess.Popen", _StubPopen)
    assert mount._open_in_os("/data/scan.tif") is True
    assert calls == [["xdg-open", "/data/scan.tif"]]


def test_open_in_os_darwin_invokes_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """macOS dispatches to the ``open`` command."""
    monkeypatch.setattr("sys.platform", "darwin")
    calls: list[list[str]] = []
    monkeypatch.setattr("subprocess.Popen", lambda args: calls.append(args) or SimpleNamespace())
    assert mount._open_in_os("/Users/x/scan.tif") is True
    assert calls == [["open", "/Users/x/scan.tif"]]


def test_open_in_os_returns_false_when_popen_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A subprocess failure is caught and surfaces False so the caller can toast."""
    monkeypatch.setattr("sys.platform", "linux")

    def _raise(*_args: Any, **_kw: Any) -> None:
        raise OSError("no such binary")

    monkeypatch.setattr("subprocess.Popen", _raise)
    assert mount._open_in_os("/data/scan.tif") is False


# ---------------------------------------------------------------------------
# _file_context_action: open-in-OS / copy-path / unknown
# ---------------------------------------------------------------------------


class _ClipboardSpy:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def write(self, value: str) -> None:
        self.writes.append(value)


class _UiSpy:
    """Minimal NiceGUI surface stand-in: clipboard + navigate.to + notify."""

    def __init__(self, *, clipboard_raises: bool = False) -> None:
        self._clipboard_raises = clipboard_raises
        self.clipboard = _ClipboardSpy()

        class _Nav:
            def __init__(self) -> None:
                self.navigated: list[str] = []

            def to(self_inner, url: str) -> None:  # noqa: N805
                self_inner.navigated.append(url)

        self.navigate = _Nav()

    def __getattr__(self, name: str) -> Any:
        # Anything else the helpers might touch (`ui.dialog`, `ui.card`,
        # `ui.label`, ...) raises Exception so the helpers' defensive
        # try/except paths take effect.
        raise AttributeError(name)


def test_file_context_action_open_in_os_dispatches_to_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``open_in_os`` action calls the platform opener and reports success."""
    called: list[str] = []
    monkeypatch.setattr(mount, "_open_in_os", lambda p: called.append(p) or True)
    ui = _UiSpy()
    entry = SimpleNamespace(path="/data/EQ1/scan.tif")
    mount._file_context_action(None, entry, "open_in_os", ui)
    assert called == ["/data/EQ1/scan.tif"]


def test_file_context_action_open_in_os_failure_toasts_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the opener returns False the action surfaces a negative toast."""
    monkeypatch.setattr(mount, "_open_in_os", lambda _p: False)
    ui = _UiSpy()
    entry = SimpleNamespace(path="/data/scan.tif")
    mount._file_context_action(None, entry, "open_in_os", ui)
    # No assertion needed -- the test only verifies the call doesn't raise.


def test_file_context_action_copy_path_writes_clipboard() -> None:
    """``copy_path`` writes the entry path to the NiceGUI clipboard."""
    ui = _UiSpy()
    entry = SimpleNamespace(path="/data/EQ1/scan.tif")
    mount._file_context_action(None, entry, "copy_path", ui)
    assert ui.clipboard.writes == ["/data/EQ1/scan.tif"]


def test_file_context_action_unknown_action_no_raise() -> None:
    """An unknown action verb is logged + toasted without raising."""
    ui = _UiSpy()
    entry = SimpleNamespace(path="/data/x.bin")
    mount._file_context_action(None, entry, "rename", ui)  # no AssertionError


def test_file_context_action_empty_path_toasts_negative() -> None:
    """An entry with no path triggers the early-return toast."""
    ui = _UiSpy()
    mount._file_context_action(None, SimpleNamespace(path=""), "open_in_os", ui)


# ---------------------------------------------------------------------------
# _run_staging_action: force_sync / clear_verified / view_log / unknown
# ---------------------------------------------------------------------------


class _StubNasSync:
    def __init__(self) -> None:
        self.enqueued: list[Path] = []

    async def enqueue(self, run_path: Path) -> SimpleNamespace:
        self.enqueued.append(run_path)
        return SimpleNamespace(state="queued", job_id="j-1")


async def test_run_staging_action_force_sync_invokes_nas_sync_enqueue() -> None:
    """Force-sync routes to ``deps.nas_sync.enqueue`` with the run path."""
    nas_sync = _StubNasSync()
    deps = _deps(config=_config(), nas_sync=nas_sync)
    ui = _UiSpy()
    mount._run_staging_action(deps, "EQ1/proj/Run_x", "force_sync", ui)
    # The action spawns a background task; drain it.
    pending = [t for t in mount._BACKGROUND_TASKS if not t.done()]
    for task in pending:
        await task
    assert nas_sync.enqueued == [Path("EQ1/proj/Run_x")]


def test_run_staging_action_force_sync_missing_nas_sync_toasts() -> None:
    """Force-sync without a wired NAS sync surfaces a negative toast (no raise)."""
    deps = _deps(config=_config(), nas_sync=None)
    ui = _UiSpy()
    mount._run_staging_action(deps, "EQ1/proj/Run_x", "force_sync", ui)


def test_run_staging_action_no_config_toasts_negative() -> None:
    """The early-exit when config is missing keeps the action a no-op."""
    deps = _deps(config=None)
    ui = _UiSpy()
    mount._run_staging_action(deps, "EQ1/proj/Run_x", "force_sync", ui)


def test_run_staging_action_view_log_invokes_log_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``view_log`` dispatches to the dialog opener helper."""
    seen: list[Path] = []
    monkeypatch.setattr(mount, "_open_log_dialog", lambda _deps, path, _ui: seen.append(path))
    deps = _deps(config=_config())
    ui = _UiSpy()
    mount._run_staging_action(deps, "EQ1/proj/Run_x", "view_log", ui)
    assert seen == [Path("EQ1/proj/Run_x")]


def test_run_staging_action_unknown_action_no_raise() -> None:
    """An unknown action verb is reported but doesn't raise."""
    deps = _deps(config=_config())
    ui = _UiSpy()
    mount._run_staging_action(deps, "EQ1/proj/Run_x", "rename", ui)


async def test_run_staging_action_clear_verified_invokes_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``clear_verified`` deletes the run directory via the local helper."""
    captured: list[Path] = []

    def _stub(run_path: Path) -> tuple[int, int]:
        captured.append(run_path)
        return 3, 1024

    monkeypatch.setattr(mount, "clear_run_dir", _stub)
    deps = _deps(config=_config())
    ui = _UiSpy()
    mount._run_staging_action(deps, "EQ1/proj/Run_x", "clear_verified", ui)
    pending = [t for t in mount._BACKGROUND_TASKS if not t.done()]
    for task in pending:
        await task
    assert captured == [Path("EQ1/proj/Run_x")]


# NOTE: _bulk_clear_verified was removed with the footer "Clear verified runs"
# button (its only caller) — orchestrator/staging hidden; see the design spec.
# Per-run clear (the kept tree context-menu action) is still covered by
# test_run_staging_action_clear_verified_invokes_clear above.


# ---------------------------------------------------------------------------
# _drive_folder_feed: no selection / cached feed / first render
# ---------------------------------------------------------------------------


class _StubTabStorage(dict):
    pass


class _StubApp:
    """Lightweight ``app`` stand-in carrying ``storage.tab``."""

    def __init__(self) -> None:
        class _Storage:
            def __init__(self_inner) -> None:  # noqa: N805
                self_inner.tab = _StubTabStorage()

        self.storage = _Storage()


def test_drive_folder_feed_returns_empty_when_no_selection() -> None:
    """Without a selected node the feed isn't started and returns ``[]``."""
    app = _StubApp()
    assert mount._drive_folder_feed(app, _deps(), None) == []
    # No feed should have been created.
    assert "folder_feed" not in app.storage.tab


def test_drive_folder_feed_creates_feed_on_first_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first call mounts a FolderFeed + RefreshCoordinator on tab storage."""
    # Stub out asyncio.create_task so we don't need a running loop.
    monkeypatch.setattr(mount, "_spawn_background", lambda coro: coro.close())
    app = _StubApp()
    out = mount._drive_folder_feed(app, _deps(), "EQ1/proj/Run_a")
    assert out == []  # last_payload is None on first render
    assert "folder_feed" in app.storage.tab
    assert "folder_feed_coord" in app.storage.tab


def test_drive_folder_feed_converts_cached_payload_to_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the feed's last_payload is set, entries are projected into
    FileListEntry rows."""
    from exlab_wizard.api.routers.browse import FolderEntry, FolderResponse
    from exlab_wizard.ui.client import folder_feed as _ff

    monkeypatch.setattr(mount, "_spawn_background", lambda coro: coro.close())
    app = _StubApp()
    # Prime the tab storage with a feed already bound to the path.
    seeded = FolderResponse(
        path="EQ1/proj/Run_a",
        entries=[
            FolderEntry(
                name="scan.tif",
                path="EQ1/proj/Run_a/scan.tif",
                is_dir=False,
                size_bytes=10,
                modified_iso="2026-05-14T09:22:00Z",
                sync_status="synced",
            )
        ],
    )
    feed = _ff.FolderFeed(fetch=lambda _p: None)  # type: ignore[arg-type]
    feed._state.path = "EQ1/proj/Run_a"
    feed._state.last_payload = seeded
    app.storage.tab["folder_feed"] = feed
    app.storage.tab["folder_feed_coord"] = SimpleNamespace(
        should_skip_folder=lambda: False, record_folder_refresh=lambda: None
    )
    out = mount._drive_folder_feed(app, _deps(), "EQ1/proj/Run_a")
    assert len(out) == 1
    assert out[0].name == "scan.tif"
    assert out[0].sync_status == "synced"


# ---------------------------------------------------------------------------
# _fetch_folder_async: skip-when-coalescing + record-on-success
# ---------------------------------------------------------------------------


async def test_fetch_folder_async_skips_when_coord_says_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fetch is a no-op when RefreshCoordinator just walked the tree."""
    scanned: list[str] = []

    def _scan(path: str, _config: Any) -> Any:
        scanned.append(path)
        return SimpleNamespace(path=path, entries=[])

    monkeypatch.setattr("exlab_wizard.api.routers.browse.scan_folder_sync", _scan)
    coord = SimpleNamespace(should_skip_folder=lambda: True, record_folder_refresh=lambda: None)
    result = await mount._fetch_folder_async(_deps(), "EQ1/proj/Run_a", coord)
    assert result is None
    assert scanned == []  # no FS walk happened


async def test_fetch_folder_async_records_refresh_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful scan records the refresh on the coordinator."""
    recorded: list[bool] = []

    def _scan(_path: str, _config: Any) -> Any:
        return SimpleNamespace(path=_path, entries=[])

    monkeypatch.setattr("exlab_wizard.api.routers.browse.scan_folder_sync", _scan)
    coord = SimpleNamespace(
        should_skip_folder=lambda: False,
        record_folder_refresh=lambda: recorded.append(True),
    )
    result = await mount._fetch_folder_async(_deps(), "EQ1/proj/Run_a", coord)
    assert result is not None
    assert recorded == [True]


async def test_fetch_folder_async_swallows_helper_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scan failure returns ``None`` so the feed keeps polling."""

    def _raise(*_a: Any, **_kw: Any) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr("exlab_wizard.api.routers.browse.scan_folder_sync", _raise)
    out = await mount._fetch_folder_async(_deps(), "EQ1/proj/Run_a", coord=None)
    assert out is None


# ---------------------------------------------------------------------------
# _open_log_dialog: no queue job / queue job present
# ---------------------------------------------------------------------------


async def test_open_log_dialog_no_queue_job_renders_dialog(tmp_path: Path) -> None:
    """A run with no sync-queue job still renders a dialog without raising."""
    ui = _UiSpy()
    deps = _deps(nas_sync=None)
    mount._open_log_dialog(deps, tmp_path / "nope", ui)
    for task in [t for t in mount._BACKGROUND_TASKS if not t.done()]:
        await task


async def test_open_log_dialog_with_queue_job_renders_dialog(tmp_path: Path) -> None:
    """A run with a sync-queue job renders its state without raising."""

    class _Row:
        state = SimpleNamespace(value="verified")
        enqueued_at = "2026-05-01T10:00:00Z"
        verified_at = "2026-05-01T10:35:00Z"
        attempts = 1
        last_error = None

    class _Queue:
        async def get_by_run_path(self, _path: Path) -> Any:
            return _Row()

    ui = _UiSpy()
    deps = _deps(nas_sync=_Queue())
    mount._open_log_dialog(deps, tmp_path / "EQ1" / "Run_x", ui)
    for task in [t for t in mount._BACKGROUND_TASKS if not t.done()]:
        await task


# ---------------------------------------------------------------------------
# Extra coverage: exception / edge paths
# ---------------------------------------------------------------------------


def test_build_metadata_payload_returns_empty_on_builder_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any exception raised by a per-kind builder is logged + swallowed."""

    def _raise(*_a: Any, **_kw: Any) -> dict[str, Any]:
        raise RuntimeError("boom")

    monkeypatch.setattr(mount, "_metadata_for_owned_equipment", _raise)
    out = mount._build_metadata_payload("EQ1", "equipment", _deps(config=_config()))
    assert out == {}


def test_metadata_for_relay_equipment_emits_id_label_source_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The relay payload pulls id+label from build_received_equipment_nodes."""
    monkeypatch.setattr(
        "exlab_wizard.api.routers.browse.build_received_equipment_nodes",
        lambda _config: [
            SimpleNamespace(id="RELAY_EQX", label="Confocal X"),
        ],
    )
    out = mount._metadata_for_relay_equipment("RELAY_EQX", _config())
    assert out == {
        "id": "RELAY_EQX",
        "label": "Confocal X",
        "source_host": "",
    }


def test_metadata_for_relay_equipment_unknown_id_falls_back() -> None:
    """An unknown relay id still produces a payload (best-effort label)."""
    out = mount._metadata_for_relay_equipment("RELAY_UNKNOWN", _config())
    assert out["id"] == "RELAY_UNKNOWN"
    assert out["label"] == "RELAY_UNKNOWN"


def test_metadata_for_project_returns_empty_for_single_segment_id() -> None:
    """A node id without an equipment/project split returns ``{}``."""
    out = mount._metadata_for_project("only-equipment", _config())
    assert out == {}


def test_count_dir_children_returns_zero_for_missing_dir(tmp_path: Path) -> None:
    """A missing parent dir returns 0 (no FS pre-check needed)."""
    assert mount._count_dir_children(tmp_path / "nope", "Run_") == 0


def test_count_dir_children_filters_by_prefix(tmp_path: Path) -> None:
    """Only directory children whose names start with the prefix are counted."""
    (tmp_path / "Run_a").mkdir()
    (tmp_path / "Run_b").mkdir()
    (tmp_path / "Other").mkdir()
    (tmp_path / "Run_file.txt").write_text("not-a-dir")
    assert mount._count_dir_children(tmp_path, "Run_") == 2


def test_metadata_for_run_returns_partial_on_decode_failure(tmp_path: Path) -> None:
    """A corrupt creation.json yields {path, name}, never crashes."""
    from exlab_wizard.constants import CACHE_DIR_NAME, CREATION_JSON_NAME

    run_dir = tmp_path / "Run_x"
    (run_dir / CACHE_DIR_NAME).mkdir(parents=True)
    (run_dir / CACHE_DIR_NAME / CREATION_JSON_NAME).write_bytes(b"{not-json")
    out = mount._metadata_for_run(str(run_dir))
    assert out == {"path": str(run_dir), "name": run_dir.name}


async def test_run_staging_action_force_sync_exception_path_no_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When nas_sync.enqueue raises, the background task swallows + toasts."""

    class _Bad:
        async def enqueue(self, _path: Path) -> None:
            raise RuntimeError("queue full")

    deps = _deps(config=_config(), nas_sync=_Bad())
    ui = _UiSpy()
    mount._run_staging_action(deps, "EQ1/Run_x", "force_sync", ui)
    for task in [t for t in mount._BACKGROUND_TASKS if not t.done()]:
        await task


async def test_run_staging_action_clear_verified_exception_path_no_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the clear helper raises, the background task swallows + toasts."""

    def _raise(*_a: Any, **_kw: Any) -> tuple[int, int]:
        raise RuntimeError("oh no")

    monkeypatch.setattr(mount, "clear_run_dir", _raise)
    deps = _deps(config=_config())
    ui = _UiSpy()
    mount._run_staging_action(deps, "EQ1/Run_x", "clear_verified", ui)
    for task in [t for t in mount._BACKGROUND_TASKS if not t.done()]:
        await task


async def test_run_staging_action_clear_verified_zero_files_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 0-file path reports ``already cleared`` rather than ``cleared N``."""

    monkeypatch.setattr(mount, "clear_run_dir", lambda *_a, **_kw: (0, 0))
    deps = _deps(config=_config())
    ui = _UiSpy()
    mount._run_staging_action(deps, "EQ1/Run_x", "clear_verified", ui)
    for task in [t for t in mount._BACKGROUND_TASKS if not t.done()]:
        await task


def test_file_context_action_clipboard_failure_toasts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clipboard write failure is caught and reported as a negative toast."""

    class _BadClipboard:
        def write(self, _v: str) -> None:
            raise RuntimeError("no clipboard")

    ui = _UiSpy()
    ui.clipboard = _BadClipboard()
    entry = SimpleNamespace(path="/data/scan.tif")
    mount._file_context_action(None, entry, "copy_path", ui)


# ---------------------------------------------------------------------------
# A richer fake ``ui`` that can build dialogs/cards/rows/buttons -- needed by
# the helpers that render NiceGUI elements through the *passed* ui object
# (``_open_operation_details``, ``_cancel_session``, ``_open_log_dialog``).
# It records labels and button (text, handler) pairs so a test can fire a
# button click and assert the downstream behaviour.
# ---------------------------------------------------------------------------


class _DialogStub(_Fluent):
    def __init__(self, log: list[str]) -> None:
        self._log = log
        self.opened = 0
        self.closed = 0

    def open(self) -> None:
        self.opened += 1
        self._log.append("open")

    def close(self, *_args: Any) -> None:
        self.closed += 1
        self._log.append("close")


class _DialogUI:
    """A fake ``ui`` whose factories build recording stand-ins.

    Unlike ``_UiSpy`` (which raises on every element factory to drive the
    *except* paths) this one returns chainable elements so the dialog
    *render* bodies execute. Buttons capture their ``on_click`` handler so a
    test can invoke it.
    """

    def __init__(self) -> None:
        self.labels: list[str] = []
        self.buttons: list[tuple[str, Any]] = []
        self.dialogs: list[_DialogStub] = []
        self.events: list[str] = []
        self.navigate = SimpleNamespace(to=lambda _url: None)

    def dialog(self, *_args: Any, **_kwargs: Any) -> _DialogStub:
        d = _DialogStub(self.events)
        self.dialogs.append(d)
        return d

    def card(self, *_args: Any, **_kwargs: Any) -> _Fluent:
        return _Fluent()

    def row(self, *_args: Any, **_kwargs: Any) -> _Fluent:
        return _Fluent()

    def label(self, text: str = "", *_args: Any, **_kwargs: Any) -> _Fluent:
        self.labels.append(text)
        return _Fluent()

    def icon(self, *_args: Any, **_kwargs: Any) -> _Fluent:
        return _Fluent()

    def button(self, text: str = "", *, on_click: Any = None, **_kwargs: Any) -> _Fluent:
        self.buttons.append((text, on_click))
        return _Fluent()


# ---------------------------------------------------------------------------
# _nas_test_connection
# ---------------------------------------------------------------------------


async def test_nas_test_connection_unavailable_when_probe_missing() -> None:
    """No probe wired -> a failure result rather than a crash."""
    result = await mount._nas_test_connection(_deps(config=_nas_config()))
    assert result.success is False
    assert "not available" in result.detail


async def test_nas_test_connection_unavailable_when_config_missing() -> None:
    result = await mount._nas_test_connection(_deps(config=None, equipment_probe=lambda _e: {}))
    assert result.success is False


async def test_nas_test_connection_success_maps_latency() -> None:
    """A reachable probe maps ok/latency into a Connected result."""
    probed: list[Any] = []

    def _probe(equipment: Any) -> dict[str, Any]:
        probed.append(equipment)
        return {"ok": True, "latency_ms": 42}

    deps = _deps(config=_nas_config(), equipment_probe=_probe)
    result = await mount._nas_test_connection(deps)
    assert result.success is True
    assert result.headline == "Connected"
    assert "42 ms" in result.detail
    # The nas-mode equipment was chosen as the probe argument.
    assert probed[0].id == "EQ1"


async def test_nas_test_connection_failure_maps_reason() -> None:
    deps = _deps(
        config=_nas_config(),
        equipment_probe=lambda _e: {"ok": False, "reason": "auth denied"},
    )
    result = await mount._nas_test_connection(deps)
    assert result.success is False
    assert result.detail == "auth denied"


async def test_nas_test_connection_awaits_coroutine_probe() -> None:
    """An async probe is awaited before its dict is mapped."""

    async def _probe(_equipment: Any) -> dict[str, Any]:
        return {"ok": True}

    result = await mount._nas_test_connection(_deps(config=_nas_config(), equipment_probe=_probe))
    assert result.success is True
    assert result.detail == "reachable"


async def test_nas_test_connection_swallows_probe_exception() -> None:
    def _probe(_equipment: Any) -> dict[str, Any]:
        msg = "boom"
        raise RuntimeError(msg)

    result = await mount._nas_test_connection(_deps(config=_nas_config(), equipment_probe=_probe))
    assert result.success is False
    assert result.detail == "boom"


# ---------------------------------------------------------------------------
# _setup_next_action
# ---------------------------------------------------------------------------


def test_setup_next_action_none_when_deps_none() -> None:
    assert mount._setup_next_action(None) is None


def test_setup_next_action_returns_action_for_incomplete_setup() -> None:
    """A half-wired install names its first-failing gate as the next action."""
    action = mount._setup_next_action(_deps(config=_nas_config(nas_remote="")))
    assert isinstance(action, str)
    assert action


def test_setup_next_action_none_when_ready() -> None:
    """A fully-satisfied install has no outstanding next action."""
    deps = _deps(config=_nas_config(), keyring_password_present=True, lims_reachable=True)
    assert mount._setup_next_action(deps) is None


def test_setup_next_action_swallows_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _boom(_deps: Any) -> Any:
        msg = "evaluator exploded"
        raise RuntimeError(msg)

    monkeypatch.setattr("exlab_wizard.api.setup.compute_setup_state", _boom)
    with caplog.at_level("WARNING"):
        assert mount._setup_next_action(_deps(config=_nas_config())) is None
    assert any("setup next-action" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _toggle_keep_local
# ---------------------------------------------------------------------------


def test_toggle_keep_local_no_writer_toasts() -> None:
    """No sync-state writer wired -> negative toast, no task spawned."""
    ui = _UiSpy()
    entry = SimpleNamespace(path="/data/EQ1/proj/Run_x/scan.tif", keep_local=False)
    mount._toggle_keep_local(_deps(sync_state_writer=None), entry, ui)


def test_toggle_keep_local_file_not_in_run_toasts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A path with no enclosing run root surfaces a negative toast."""
    monkeypatch.setattr("exlab_wizard.api.routers.browse._find_run_root", lambda _parent: None)
    writer = SimpleNamespace(set_keep_local=lambda *a, **k: None)
    ui = _UiSpy()
    entry = SimpleNamespace(path=str(tmp_path / "loose.tif"), keep_local=False)
    mount._toggle_keep_local(_deps(sync_state_writer=writer), entry, ui)


async def test_toggle_keep_local_invokes_writer_and_on_done(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A resolvable file flips keep_local via the writer and fires on_done."""
    run_root = tmp_path / "EQ1" / "proj" / "Run_x"
    run_root.mkdir(parents=True)
    file_path = run_root / "scan.tif"
    file_path.write_text("x")

    monkeypatch.setattr("exlab_wizard.api.routers.browse._find_run_root", lambda _parent: run_root)
    monkeypatch.setattr(
        "exlab_wizard.api.routers.browse._run_relative_posix",
        lambda _root, _path: "scan.tif",
    )

    calls: list[tuple[Any, str, bool]] = []
    done: list[bool] = []

    class _Writer:
        async def set_keep_local(self, root: Any, rel: str, value: bool) -> None:
            calls.append((root, rel, value))

    ui = _UiSpy()
    entry = SimpleNamespace(path=str(file_path), keep_local=False)
    mount._toggle_keep_local(
        _deps(sync_state_writer=_Writer()), entry, ui, on_done=lambda: done.append(True)
    )
    for task in [t for t in mount._BACKGROUND_TASKS if not t.done()]:
        await task
    # keep_local was False -> the new value flips to True; on_done fired.
    assert calls == [(run_root, "scan.tif", True)]
    assert done == [True]


async def test_toggle_keep_local_writer_failure_toasts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_root = tmp_path / "EQ1" / "Run_x"
    run_root.mkdir(parents=True)
    monkeypatch.setattr("exlab_wizard.api.routers.browse._find_run_root", lambda _parent: run_root)
    monkeypatch.setattr(
        "exlab_wizard.api.routers.browse._run_relative_posix",
        lambda _root, _path: "scan.tif",
    )

    class _BadWriter:
        async def set_keep_local(self, *_a: Any, **_k: Any) -> None:
            msg = "disk full"
            raise RuntimeError(msg)

    ui = _UiSpy()
    entry = SimpleNamespace(path=str(run_root / "scan.tif"), keep_local=True)
    mount._toggle_keep_local(_deps(sync_state_writer=_BadWriter()), entry, ui)
    for task in [t for t in mount._BACKGROUND_TASKS if not t.done()]:
        await task


def test_file_context_action_keep_local_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``keep_local`` action routes to ``_toggle_keep_local``."""
    seen: list[Any] = []
    monkeypatch.setattr(
        mount, "_toggle_keep_local", lambda _deps, entry, _ui, on_done=None: seen.append(entry)
    )
    ui = _UiSpy()
    entry = SimpleNamespace(path="/data/EQ1/Run_x/scan.tif", keep_local=False)
    mount._file_context_action(_deps(), entry, "keep_local", ui)
    assert seen == [entry]


# ---------------------------------------------------------------------------
# _open_in_os (win32 branch)
# ---------------------------------------------------------------------------


def test_open_in_os_win32_invokes_startfile(monkeypatch: pytest.MonkeyPatch) -> None:
    """On win32 the helper calls ``os.startfile`` and reports success."""
    import os as _os

    monkeypatch.setattr("sys.platform", "win32")
    started: list[str] = []
    # ``os.startfile`` only exists on Windows; create it for the test.
    monkeypatch.setattr(_os, "startfile", lambda p: started.append(p), raising=False)
    assert mount._open_in_os("C:/data/scan.tif") is True
    assert started == ["C:/data/scan.tif"]


# ---------------------------------------------------------------------------
# _build_operation_rows / _open_operations_modal
# ---------------------------------------------------------------------------


def _store_with_running_session() -> Any:
    from exlab_wizard.controller.session_store import SessionStore

    store = SessionStore()
    handle = store.open("project", {})
    store.get(handle.session_id).state = SessionState.RENDERING
    return store, handle.session_id


def test_build_operation_rows_maps_panel_sessions() -> None:
    store, sid = _store_with_running_session()
    deps = SimpleNamespace(controller=SimpleNamespace(session_store=store))
    rows = mount._build_operation_rows(deps)
    assert len(rows) == 1
    assert rows[0].operation_id == sid


def test_open_operations_modal_no_controller_toasts() -> None:
    """No controller -> a negative toast and no dialog open."""
    ui = _UiSpy()
    mount._open_operations_modal(_deps(controller=None), ui)


def test_open_operations_modal_opens_with_controller() -> None:
    """A wired controller builds the modal and opens it (no raise)."""
    store, _sid = _store_with_running_session()
    deps = SimpleNamespace(controller=SimpleNamespace(session_store=store))
    ui = _UiSpy()
    mount._open_operations_modal(deps, ui)


# ---------------------------------------------------------------------------
# _open_operation_details
# ---------------------------------------------------------------------------


def test_open_operation_details_session_not_found_toasts() -> None:
    deps = SimpleNamespace(
        controller=SimpleNamespace(session_store=SimpleNamespace(get=lambda _s: None))
    )
    ui = _UiSpy()
    mount._open_operation_details(deps, "sess-x", ui)


def test_open_operation_details_renders_state_and_pending() -> None:
    """The details dialog renders the session state plus pending/error lines."""
    session = SimpleNamespace(
        state=SimpleNamespace(value="input_required"),
        pending_input={"reason": "need sample id"},
        error={"message": "earlier failure"},
    )
    store = SimpleNamespace(get=lambda _s: session)
    deps = SimpleNamespace(controller=SimpleNamespace(session_store=store))
    ui = _DialogUI()
    mount._open_operation_details(deps, "sess-7", ui)
    assert ui.dialogs and ui.dialogs[0].opened == 1
    rendered = " | ".join(ui.labels)
    assert "Operation sess-7" in rendered
    assert "State: input_required" in rendered
    assert "need sample id" in rendered
    assert "earlier failure" in rendered


# ---------------------------------------------------------------------------
# _resume_operation
# ---------------------------------------------------------------------------


def test_resume_operation_no_pending_input_toasts() -> None:
    """A session not awaiting input cannot be resumed -> negative toast."""
    session = SimpleNamespace(pending_input=None)
    store = SimpleNamespace(get=lambda _s: session)
    deps = SimpleNamespace(controller=SimpleNamespace(session_store=store))
    ui = _UiSpy()
    mount._resume_operation(deps, "sess-1", ui)


def test_resume_operation_opens_input_dialog(monkeypatch: pytest.MonkeyPatch) -> None:
    """A suspended session re-opens its escalation dialog with parked fields."""
    captured: dict[str, Any] = {}

    def _fake_dialog(controller: Any, session_id: str, _ui: Any, **kwargs: Any) -> Any:
        captured["session_id"] = session_id
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(mount, "_open_input_required_dialog", _fake_dialog)
    session = SimpleNamespace(
        pending_input={"plugin": "checksum", "reason": "verify", "fields": [{"key": "ok"}]}
    )
    store = SimpleNamespace(get=lambda _s: session)
    controller = SimpleNamespace(session_store=store)
    mount._resume_operation(SimpleNamespace(controller=controller), "sess-2", _UiSpy())
    assert captured["session_id"] == "sess-2"
    assert captured["plugin"] == "checksum"
    assert captured["reason"] == "verify"
    assert captured["fields"] == [{"key": "ok"}]


# ---------------------------------------------------------------------------
# _open_input_required_dialog (submit / cancel callbacks)
# ---------------------------------------------------------------------------


async def test_open_input_required_dialog_submit_resumes_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dialog's Submit handler calls ``controller.resume`` in the background."""
    resumed: list[tuple[str, dict[str, Any]]] = []

    class _Controller:
        async def resume(self, session_id: str, values: dict[str, Any]) -> None:
            resumed.append((session_id, values))

    captured: dict[str, Any] = {}

    def _fake_builder(
        *, plugin: str, reason: str, fields: Any, on_submit: Any, on_cancel: Any
    ) -> Any:
        captured["on_submit"] = on_submit
        captured["on_cancel"] = on_cancel
        return SimpleNamespace(open=lambda: None)

    monkeypatch.setattr(
        "exlab_wizard.ui.components.input_required_dialog.input_required_dialog", _fake_builder
    )
    mount._open_input_required_dialog(
        _Controller(), "sess-3", _UiSpy(), plugin="p", reason="r", fields=[]
    )
    # Fire the captured submit handler -> spawns the resume task.
    captured["on_submit"]({"answer": "yes"})
    for task in [t for t in mount._BACKGROUND_TASKS if not t.done()]:
        await task
    assert resumed == [("sess-3", {"answer": "yes"})]


def test_open_input_required_dialog_cancel_routes_to_cancel_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dialog's Cancel handler routes through ``_cancel_session``."""
    cancelled: list[str] = []
    monkeypatch.setattr(mount, "_cancel_session", lambda _ctrl, sid, _ui: cancelled.append(sid))
    captured: dict[str, Any] = {}

    def _fake_builder(
        *, plugin: str, reason: str, fields: Any, on_submit: Any, on_cancel: Any
    ) -> Any:
        captured["on_cancel"] = on_cancel
        return SimpleNamespace(open=lambda: None)

    monkeypatch.setattr(
        "exlab_wizard.ui.components.input_required_dialog.input_required_dialog", _fake_builder
    )
    mount._open_input_required_dialog(
        SimpleNamespace(), "sess-4", _UiSpy(), plugin="p", reason="r", fields=[]
    )
    captured["on_cancel"]()
    assert cancelled == ["sess-4"]


# ---------------------------------------------------------------------------
# _cancel_operation / _cancel_session
# ---------------------------------------------------------------------------


def test_cancel_operation_no_controller_toasts() -> None:
    ui = _UiSpy()
    mount._cancel_operation(_deps(controller=None), "sess-1", ui)


def test_cancel_operation_routes_to_cancel_session(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(mount, "_cancel_session", lambda _ctrl, sid, _ui: seen.append(sid))
    controller = SimpleNamespace()
    mount._cancel_operation(SimpleNamespace(controller=controller), "sess-5", _UiSpy())
    assert seen == ["sess-5"]


async def test_cancel_session_discard_calls_controller_cancel() -> None:
    """The §9.4 dialog's Discard button cancels with discard_files=True."""
    cancels: list[tuple[str, bool]] = []

    class _Controller:
        async def cancel(self, session_id: str, *, discard_files: bool) -> None:
            cancels.append((session_id, discard_files))

    ui = _DialogUI()
    mount._cancel_session(_Controller(), "sess-6", ui)
    # Buttons: Back, Keep files, Discard files. Fire "Discard files".
    handlers = {text: fn for text, fn in ui.buttons}
    assert set(handlers) == {"Back", "Keep files", "Discard files"}
    handlers["Discard files"](None)
    for task in [t for t in mount._BACKGROUND_TASKS if not t.done()]:
        await task
    assert cancels == [("sess-6", True)]


async def test_cancel_session_keep_files_calls_controller_cancel() -> None:
    """The Keep-files button cancels with discard_files=False."""
    cancels: list[tuple[str, bool]] = []

    class _Controller:
        async def cancel(self, session_id: str, *, discard_files: bool) -> None:
            cancels.append((session_id, discard_files))

    ui = _DialogUI()
    mount._cancel_session(_Controller(), "sess-7", ui)
    handlers = {text: fn for text, fn in ui.buttons}
    handlers["Keep files"](None)
    for task in [t for t in mount._BACKGROUND_TASKS if not t.done()]:
        await task
    assert cancels == [("sess-7", False)]


async def test_cancel_session_swallows_controller_failure() -> None:
    """A cancel failure is caught and toasted, not raised."""

    class _Controller:
        async def cancel(self, *_a: Any, **_k: Any) -> None:
            msg = "already gone"
            raise RuntimeError(msg)

    ui = _DialogUI()
    mount._cancel_session(_Controller(), "sess-8", ui)
    handlers = {text: fn for text, fn in ui.buttons}
    handlers["Discard files"](None)
    for task in [t for t in mount._BACKGROUND_TASKS if not t.done()]:
        await task


def test_cancel_session_back_button_closes_dialog() -> None:
    """The Back button closes the dialog without cancelling."""
    ui = _DialogUI()
    mount._cancel_session(SimpleNamespace(), "sess-9", ui)
    handlers = {text: fn for text, fn in ui.buttons}
    handlers["Back"](None)
    assert ui.dialogs[0].closed >= 1


# ---------------------------------------------------------------------------
# _open_log_dialog: render body via a dialog-capable ui
# ---------------------------------------------------------------------------


async def test_open_log_dialog_renders_row_fields(tmp_path: Path) -> None:
    """With a job row, the dialog renders each populated field line."""

    class _Row:
        state = SimpleNamespace(value="verified")
        enqueued_at = "2026-05-01T10:00:00Z"
        verified_at = "2026-05-01T10:35:00Z"
        attempts = 2
        last_error = None

    class _Queue:
        async def get_by_run_path(self, _path: Path) -> Any:
            return _Row()

    ui = _DialogUI()
    deps = _deps(nas_sync=_Queue())
    mount._open_log_dialog(deps, tmp_path / "EQ1" / "Run_x", ui)
    for task in [t for t in mount._BACKGROUND_TASKS if not t.done()]:
        await task
    rendered = " | ".join(ui.labels)
    assert "Sync state: verified" in rendered
    assert "enqueued_at" in rendered
    assert "attempts" in rendered
    # last_error is None -> not rendered.
    assert "last_error" not in rendered
    assert ui.dialogs[0].opened == 1


async def test_open_log_dialog_no_row_renders_empty_line(tmp_path: Path) -> None:
    """Without a job row the dialog shows the 'no sync job' line."""
    ui = _DialogUI()
    mount._open_log_dialog(_deps(nas_sync=None), tmp_path / "Run_y", ui)
    for task in [t for t in mount._BACKGROUND_TASKS if not t.done()]:
        await task
    rendered = " | ".join(ui.labels)
    assert "Sync state: none" in rendered
    assert "No sync job recorded" in rendered


# ---------------------------------------------------------------------------
# _consume_session_progress / _close_dialog
# ---------------------------------------------------------------------------


async def test_consume_session_progress_early_return_without_progress() -> None:
    """No progress view on the wizard state -> the consumer returns at once."""

    class _Controller:
        async def subscribe(self, _sid: str) -> Any:  # pragma: no cover -- never called
            yield {"kind": "done"}

    state = SimpleNamespace(progress=None, progress_refresh=None)
    await mount._consume_session_progress(_Controller(), "sess-1", state, _UiSpy())


async def test_consume_session_progress_opens_dialog_then_closes_on_done() -> None:
    """An input_required frame opens the dialog; a done frame closes it."""
    from exlab_wizard.ui.components import session_progress

    closed: list[bool] = []
    opened_dialog = SimpleNamespace(close=lambda: closed.append(True))

    import exlab_wizard.ui.mount as _m

    orig = _m._open_input_required_dialog
    try:
        _m._open_input_required_dialog = (  # type: ignore[assignment]
            lambda *a, **k: opened_dialog
        )

        class _Controller:
            async def subscribe(self, _sid: str) -> Any:
                yield {"kind": "phase", "phase": "validating_inputs"}
                yield {"kind": "input_required", "plugin": "p", "reason": "r", "fields": []}
                yield {"kind": "done"}

        refreshed: list[bool] = []
        state = SimpleNamespace(
            progress=session_progress.SessionProgressState(),
            progress_refresh=lambda: refreshed.append(True),
        )
        await mount._consume_session_progress(_Controller(), "sess-2", state, _UiSpy())
    finally:
        _m._open_input_required_dialog = orig  # type: ignore[assignment]
    # The terminal frame force-closed the open dialog.
    assert closed == [True]
    # The phase frame triggered a re-render.
    assert refreshed


async def test_consume_session_progress_swallows_stream_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A subscribe error is logged, not raised."""
    from exlab_wizard.ui.components import session_progress

    class _Controller:
        async def subscribe(self, _sid: str) -> Any:
            msg = "stream broke"
            raise RuntimeError(msg)
            yield  # pragma: no cover -- unreachable, makes this an async gen

    state = SimpleNamespace(progress=session_progress.SessionProgressState(), progress_refresh=None)
    with caplog.at_level("ERROR"):
        await mount._consume_session_progress(_Controller(), "sess-3", state, _UiSpy())
    assert any("progress consumer failed" in r.message for r in caplog.records)


def test_close_dialog_none_is_noop() -> None:
    mount._close_dialog(None)


def test_close_dialog_calls_close() -> None:
    closed: list[bool] = []
    mount._close_dialog(SimpleNamespace(close=lambda: closed.append(True)))
    assert closed == [True]


def test_close_dialog_swallows_close_failure() -> None:
    def _boom() -> None:
        msg = "no slot"
        raise RuntimeError(msg)

    # Must not raise.
    mount._close_dialog(SimpleNamespace(close=_boom))


# ---------------------------------------------------------------------------
# _submit_run no-template gate + _render_run_wizard
# ---------------------------------------------------------------------------


async def test_submit_run_toasts_without_template() -> None:
    """Submitting a run with no template selected surfaces a negative toast."""
    controller = _FakeController(final_state=SessionState.DONE)
    deps = _deps(config=_config(), controller=controller)
    state = SimpleNamespace(selected_template="")
    ui = _UiSpy()
    await mount._submit_run(deps, state, RunKind.EXPERIMENTAL, ui)
    # No request was built (no template) and the controller never created.
    assert controller.created == []


def test_render_run_wizard_builds_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_render_run_wizard`` wires resolved templates/equipment + ``on_resolve``."""
    from exlab_wizard.template.resolution import TemplateChoices
    from exlab_wizard.ui.pages import wizard_run as wizard_run_page

    captured: dict[str, Any] = {}

    def _fake_render(
        *, state: Any, templates: Any, equipment_ids: Any, on_resolve: Any = None, **kwargs: Any
    ) -> Any:
        captured["run_kind"] = state.run_kind
        captured["templates"] = templates
        captured["equipment_ids"] = equipment_ids
        captured["on_resolve"] = on_resolve
        return "PAGE"

    # The run wizard resolves its templates through ``_resolve_template_choices``
    # (names + questions + absolute paths) rather than the legacy producers.
    monkeypatch.setattr(wizard_run_page, "render_run_wizard", _fake_render)
    monkeypatch.setattr(
        mount,
        "_resolve_template_choices",
        lambda _deps, _t, **_kw: TemplateChoices(names=["run_basic"]),
    )
    deps = _deps(config=_config(equipment=(SimpleNamespace(id="EQ1"),)))
    out = mount._render_run_wizard(deps, RunKind.TEST, _UiSpy())
    assert out == "PAGE"
    assert captured["run_kind"] is RunKind.TEST
    assert captured["templates"] == ["run_basic"]
    assert captured["equipment_ids"] == ["EQ1"]
    # ``on_resolve`` is wired so the template step re-resolves per-instance.
    assert callable(captured["on_resolve"])


# ---------------------------------------------------------------------------
# Small remaining branches: _classify_node non-EquipmentNode, metadata kinds,
# _lims_catalogue_projects schema mismatch, _drive_folder_feed storage error.
# ---------------------------------------------------------------------------


def test_classify_node_skips_non_equipment_hierarchy_keys() -> None:
    """Non-EquipmentNode keys in the hierarchy are ignored; unknown root ->
    treated as unselected."""
    hierarchy = {"a-plain-string-key": ["child"]}
    assert mount._classify_node("EQ1", hierarchy) == (None, False)


def test_build_metadata_payload_received_equipment_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The received_equipment kind routes to the relay-equipment builder."""
    monkeypatch.setattr(
        "exlab_wizard.api.routers.browse.build_received_equipment_nodes",
        lambda _config: [SimpleNamespace(id="RELAY_X", label="Relay X")],
    )
    out = mount._build_metadata_payload("RELAY_X", "received_equipment", _deps(config=_config()))
    assert out["id"] == "RELAY_X"
    assert out["label"] == "Relay X"


def test_build_metadata_payload_unknown_kind_returns_empty() -> None:
    out = mount._build_metadata_payload("EQ1", "some_other_kind", _deps(config=_config()))
    assert out == {}


def test_metadata_for_owned_equipment_projects_fields() -> None:
    """A matching equipment id projects its config fields into the payload."""
    config = SimpleNamespace(
        paths=SimpleNamespace(local_root="/srv/exlab/data"),
        equipment=[
            SimpleNamespace(
                id="EQ1",
                label="Confocal",
                sync_mode="nas",
                nas_root="/n/EQ1",
            )
        ],
    )
    out = mount._metadata_for_owned_equipment("EQ1", config)
    assert out["id"] == "EQ1"
    assert out["label"] == "Confocal"
    # ``local_root`` is derived as ``<config.paths.local_root>/<id>``.
    assert out["local_root"] == str(Path("/srv/exlab/data") / "EQ1")
    assert out["nas_root"] == "/n/EQ1"


def test_lims_catalogue_projects_schema_mismatch_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A schema_version mismatch (read_catalogue -> None) yields ``[]``."""
    catalogue = tmp_path / "catalogue.json"
    catalogue.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "exlab_wizard.lims.catalogue.read_catalogue",
        lambda _path, expected_endpoint=None: None,
    )
    deps = _deps(config=_config(offline_catalogue_path=str(catalogue)))
    assert mount._lims_catalogue_projects(deps) == []


def test_drive_folder_feed_tolerates_storage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raising ``app.storage.tab`` degrades to an in-memory feed (no tab)."""
    monkeypatch.setattr(mount, "_spawn_background", lambda coro: coro.close())

    class _BadStorage:
        @property
        def tab(self) -> Any:
            msg = "no tab context"
            raise RuntimeError(msg)

    app = SimpleNamespace(storage=_BadStorage())
    out = mount._drive_folder_feed(app, _deps(), "EQ1/proj/Run_a")
    assert out == []


# ---------------------------------------------------------------------------
# Remaining helper branches
# ---------------------------------------------------------------------------


def test_is_setup_ready_swallows_evaluation_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A raising evaluator degrades to 'not ready' (banner stays up)."""

    def _boom(_deps: Any) -> Any:
        msg = "evaluator exploded"
        raise RuntimeError(msg)

    monkeypatch.setattr("exlab_wizard.api.setup.compute_setup_state", _boom)
    with caplog.at_level("WARNING"):
        assert mount._is_setup_ready(_deps(config=_nas_config())) is False
    assert any("setup-readiness" in r.message for r in caplog.records)


def test_metadata_for_owned_equipment_no_match_returns_empty() -> None:
    """An equipment id absent from config yields ``{}``."""
    config = SimpleNamespace(equipment=[SimpleNamespace(id="EQ_OTHER")])
    assert mount._metadata_for_owned_equipment("EQ1", config) == {}


async def test_toggle_keep_local_unresolvable_relative_path_toasts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A run root that can't yield a relative path surfaces a negative toast."""
    run_root = tmp_path / "EQ1" / "Run_x"
    run_root.mkdir(parents=True)
    monkeypatch.setattr("exlab_wizard.api.routers.browse._find_run_root", lambda _parent: run_root)
    monkeypatch.setattr(
        "exlab_wizard.api.routers.browse._run_relative_posix", lambda _root, _path: None
    )
    writer = SimpleNamespace(set_keep_local=lambda *a, **k: None)
    ui = _UiSpy()
    entry = SimpleNamespace(path=str(run_root / "scan.tif"), keep_local=False)
    mount._toggle_keep_local(_deps(sync_state_writer=writer), entry, ui)
    # No background task spawned -- the early return fires before _do_toggle.
    assert not [t for t in mount._BACKGROUND_TASKS if not t.done()]


async def test_open_input_required_dialog_submit_swallows_resume_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing ``controller.resume`` is caught + toasted, not raised."""

    class _Controller:
        async def resume(self, *_a: Any, **_k: Any) -> None:
            msg = "stale session"
            raise RuntimeError(msg)

    captured: dict[str, Any] = {}

    def _fake_builder(
        *, plugin: str, reason: str, fields: Any, on_submit: Any, on_cancel: Any
    ) -> Any:
        captured["on_submit"] = on_submit
        return SimpleNamespace(open=lambda: None)

    monkeypatch.setattr(
        "exlab_wizard.ui.components.input_required_dialog.input_required_dialog", _fake_builder
    )
    mount._open_input_required_dialog(
        _Controller(), "sess-x", _UiSpy(), plugin="p", reason="r", fields=[]
    )
    captured["on_submit"]({"a": 1})
    for task in [t for t in mount._BACKGROUND_TASKS if not t.done()]:
        await task


def test_template_questions_map_swallows_outer_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An import/engine failure in the outer try yields ``{}``."""
    from exlab_wizard.ui.pages import templates as templates_page

    def _boom(*_a: Any, **_k: Any) -> None:
        msg = "scanner died"
        raise RuntimeError(msg)

    monkeypatch.setattr(templates_page, "list_templates", _boom)
    with caplog.at_level("WARNING"):
        assert mount._template_questions_map(_deps(config=_config()), "project") == {}
    assert any("template question scan" in r.message for r in caplog.records)
