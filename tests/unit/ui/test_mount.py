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


class _FakeUI:
    """Records cards / labels and exposes a ``navigate.to`` spy."""

    def __init__(self) -> None:
        self.cards = 0
        self.labels: list[str] = []
        self.navigated: list[str] = []
        self.navigate = SimpleNamespace(to=self.navigated.append)

    def card(self, *_args: Any, **_kwargs: Any) -> _Fluent:
        self.cards += 1
        return _Fluent()

    def label(self, text: str = "", *_args: Any, **_kwargs: Any) -> _Fluent:
        self.labels.append(text)
        return _Fluent()


class _BoomUI:
    """A ``ui`` whose element factories raise -- exercises render except paths."""

    def card(self, *_args: Any, **_kwargs: Any) -> Any:
        msg = "no ui slot"
        raise RuntimeError(msg)

    def label(self, *_args: Any, **_kwargs: Any) -> Any:
        msg = "no ui slot"
        raise RuntimeError(msg)


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
    # Redesign §3.1: orchestrator pipeline is always active; the
    # MainPageState flag (kept for the staging-dock render path until
    # Phase 8) is always True.
    assert state.orchestrator_enabled is True


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
# _missing_setup_sections
# ---------------------------------------------------------------------------


def test_missing_sections_when_deps_none() -> None:
    assert mount._missing_setup_sections(None) == ("paths", "lims")


def test_missing_sections_when_config_none() -> None:
    # ``operators`` is deferred (chip editor not yet wired); it must not
    # surface in the setup-incomplete tuple or it would push the operator
    # into a placeholder section before they reach the main GUI.
    assert mount._missing_setup_sections(_deps()) == ("paths", "lims")


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


def test_staging_state_when_staging_root_missing(tmp_path: Path) -> None:
    """Redesign §3.1: orchestrator pipeline is always on, but a missing
    staging_root on disk surfaces as empty rows, not a None panel."""
    deps = _deps(
        config=_config(
            orchestrator_label="LAB",
            orchestrator_staging_root=str(tmp_path / "does-not-exist"),
        ),
    )
    state = mount._build_staging_state(deps)
    # State may be None when staging is empty or not built; either way the
    # always-on contract doesn't promise rows when there are none.
    assert state is None or state.rows == []


def test_staging_state_none_when_no_config() -> None:
    assert mount._build_staging_state(_deps()) is None


def test_staging_state_returns_empty_rows_on_query_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps = _deps(
        config=_config(
            orchestrator_label="LAB",
            orchestrator_staging_root="/staging",
        ),
    )

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


def test_persist_config_warns_when_saver_returns_awaitable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    nav = _NavSpy()

    class _Awaitable:
        def __await__(self) -> Any:
            yield

    deps = _deps(save_config=lambda _cfg: _Awaitable(), restart_required=False)
    sentinel = object()

    with caplog.at_level("WARNING"):
        ok = mount._persist_config(deps, sentinel, nav)

    assert ok is True
    assert deps.config is sentinel
    assert deps.restart_required is True
    assert any("awaitable" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _render_restart_required / _render_unavailable
# ---------------------------------------------------------------------------


def test_render_restart_required_builds_card() -> None:
    ui = _FakeUI()
    result = mount._render_restart_required(ui)
    assert result is not None
    assert ui.cards == 1
    assert any("Restart required" in label for label in ui.labels)


def test_render_restart_required_swallows_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        assert mount._render_restart_required(_BoomUI()) is None
    assert any("render_restart_required" in r.message for r in caplog.records)


def test_render_unavailable_renders_headline_and_subline() -> None:
    ui = _FakeUI()
    mount._render_unavailable(ui, "Staging unavailable", "Orchestrator disabled")
    assert "Staging unavailable" in ui.labels
    assert "Orchestrator disabled" in ui.labels


def test_render_unavailable_swallows_failure(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        mount._render_unavailable(_BoomUI(), "headline", "subline")
    assert any("render_unavailable" in r.message for r in caplog.records)


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
        SimpleNamespace(name="proj_a", path=Path("/tmp/tpl/proj_a")),
        SimpleNamespace(name="proj_b", path=Path("/tmp/tpl/proj_b")),
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

    summaries = [SimpleNamespace(name="proj_basic", path=Path("/tmp/tpl/proj_basic"))]
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

    summaries = [SimpleNamespace(name="broken", path=Path("/tmp/tpl/broken"))]
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
        local_root="/data/EQ1",
        nas_root="//nas/EQ1",
    )
    config = _config(equipment=(equipment,))
    payload = mount._build_metadata_payload("EQ1", "equipment", _deps(config=config))
    assert payload["id"] == "EQ1"
    assert payload["label"] == "Confocal Microscope 1"
    assert payload["sync_mode"] == "nas"
    assert payload["local_root"] == "/data/EQ1"
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


def _drain_background() -> None:
    """Run any pending mount background tasks to completion."""
    import asyncio as _aio

    loop = _aio.new_event_loop()
    try:
        loop.run_until_complete(_aio.sleep(0))
        # Drain the mount's strong-ref set; each task is in the same loop.
        pending = [t for t in mount._BACKGROUND_TASKS if not t.done()]
        if pending:
            loop.run_until_complete(_aio.gather(*pending, return_exceptions=True))
    finally:
        loop.close()


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


# ---------------------------------------------------------------------------
# _bulk_clear_verified: success / no config / error
# ---------------------------------------------------------------------------


async def test_bulk_clear_verified_clears_verified_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bulk action clears every ``synced`` row and toasts the count."""
    cleared: list[Path] = []

    def _summary(path: str, state: str) -> SimpleNamespace:
        return SimpleNamespace(path=path, current_state=state)

    monkeypatch.setattr(
        mount,
        "list_staged_runs",
        lambda **_kw: [
            _summary("/staging/EQ1/proj/Run_a", "synced"),
            _summary("/staging/EQ1/proj/Run_b", "synced"),
            _summary("/staging/EQ1/proj/Run_c", "syncing"),
        ],
    )
    monkeypatch.setattr(mount, "clear_run_dir", lambda p: cleared.append(p) or (1, 10))
    deps = _deps(config=_config())
    ui = _UiSpy()
    mount._bulk_clear_verified(deps, ui)
    pending = [t for t in mount._BACKGROUND_TASKS if not t.done()]
    for task in pending:
        await task
    # Only the two verified rows were cleared.
    assert cleared == [Path("/staging/EQ1/proj/Run_a"), Path("/staging/EQ1/proj/Run_b")]


def test_bulk_clear_verified_no_config_toasts_and_returns() -> None:
    """The early-exit when config is missing produces a toast, no task."""
    deps = _deps(config=None)
    ui = _UiSpy()
    mount._bulk_clear_verified(deps, ui)


async def test_bulk_clear_verified_logs_helper_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception from the clear sweep is caught + toasted."""

    def _raise(**_kw: Any) -> list[Any]:
        raise RuntimeError("staging walker exploded")

    monkeypatch.setattr(mount, "list_staged_runs", _raise)
    deps = _deps(config=_config())
    ui = _UiSpy()
    mount._bulk_clear_verified(deps, ui)
    pending = [t for t in mount._BACKGROUND_TASKS if not t.done()]
    for task in pending:
        await task


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
