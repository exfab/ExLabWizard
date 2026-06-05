"""Tests for the Settings NAS-remote section (rclone.conf migration).

The section is editable: the operator picks the single ``nas:`` remote
from a dropdown of remotes detected via ``rclone listremotes`` (with a
Refresh affordance), sets its base root and optional ``--config`` path,
and Test-connection probes the *typed* values. A found / not-found badge
is derived from ``nas_remote_available``; there is no password input. The
section is always shown (after the equipment section) so the remote can be
configured before nas-mode equipment exists.
"""

from __future__ import annotations

import asyncio
import builtins
from typing import Any

import pytest

# Prime the api package before importing ui.pages so the pre-existing
# orchestrator <-> api import order resolves cleanly (see test_mount.py).
import exlab_wizard.api.app  # noqa: F401  -- import order matters
from exlab_wizard.config.models import (
    Config,
    EquipmentConfig,
    NasConfig,
    OrchestratorConfig,
    PathsConfig,
)
from exlab_wizard.constants import SyncMode
from exlab_wizard.ui.components.test_connection_panel import (
    # Aliased so pytest does not try to collect the dataclass as a test case.
    TestConnectionResult as ConnResult,
)
from exlab_wizard.ui.pages import settings


def _nas_equipment(equipment_id: str) -> EquipmentConfig:
    # rclone.conf migration: nas-mode carries no per-equipment transport.
    return EquipmentConfig(
        id=equipment_id,
        label=f"Equipment {equipment_id}",
        nas_root="/srv/nas",
        sync_mode=SyncMode.NAS,
    )


def _stage_equipment(equipment_id: str) -> EquipmentConfig:
    # rclone.conf migration (Phase 8): stage-mode carries no per-equipment
    # transport; the staging hop is the ``orchestrator.staging_remote``.
    return EquipmentConfig(
        id=equipment_id,
        label=f"Stage {equipment_id}",
        nas_root="/srv/nas",
        sync_mode=SyncMode.STAGE,
    )


def _config_with(*equipment: EquipmentConfig, remote: str = "nas01") -> Config:
    return Config(
        paths=PathsConfig(app_root="/srv/exlab"),
        equipment=list(equipment),
        orchestrator=OrchestratorConfig(
            label="LAB",
            staging_root="/staging",
            staging_remote="stagepc",
            staging_base_root="/staging-area",
        ),
        nas=NasConfig(remote=remote, base_root="/srv/nas"),
    )


def _testids(element: object) -> set[str]:
    return {
        tid
        for child in element.descendants()  # type: ignore[attr-defined]
        if (tid := child._props.get("data-testid"))
    }


def _section_body(element: object, section: str) -> object:
    """Return the rendered body container for ``section`` (or the page)."""
    testid = f"settings-section-{section}"
    for child in element.descendants():  # type: ignore[attr-defined]
        if child._props.get("data-testid") == testid:
            return child
    return element


def _section_testids(element: object, section: str) -> set[str]:
    """Testids confined to one section body (the whole page renders all)."""
    return _testids(_section_body(element, section))


def _text_of(element: object, testid: str) -> str:
    for child in element.descendants():  # type: ignore[attr-defined]
        if child._props.get("data-testid") == testid:
            return str(getattr(child, "text", "") or "")
    return ""


def _value_of(element: object, testid: str) -> Any:
    """Return the ``.value`` of the widget carrying ``testid`` (input/select)."""
    for child in element.descendants():  # type: ignore[attr-defined]
        if child._props.get("data-testid") == testid:
            return getattr(child, "value", None)
    return None


# ---------------------------------------------------------------------------
# Handler-invocation helpers
#
# ``render_settings_page`` builds real NiceGUI widgets and wires the
# section closures (Save, Test-connection, chip add/remove, autostart,
# quit, ...) as click / change handlers. The existing harness already
# reaches into NiceGUI internals (``_props``, ``descendants()``); these
# helpers extend that to pull a widget's user-supplied callback back out
# so a test can fire it and assert on the observable effect.
# ---------------------------------------------------------------------------


def _find(element: object, testid: str) -> Any:
    for child in element.descendants():  # type: ignore[attr-defined]
        if child._props.get("data-testid") == testid:
            return child
    return None


def _find_all(element: object, testid: str) -> list[Any]:
    return [
        child
        for child in element.descendants()  # type: ignore[attr-defined]
        if child._props.get("data-testid") == testid
    ]


def _click_handler(element: object, event_type: str = "click") -> Any:
    """Return the user callback registered for ``event_type`` on a widget.

    ``on_click=`` wraps the callback in a NiceGUI lambda whose closure
    keeps the real function under a ``callback`` free variable; ``.on(...)``
    stores the handler directly. Unwrap the former so tests fire the
    section closure itself rather than NiceGUI's dispatch shim.
    """
    for listener in element._event_listeners.values():  # type: ignore[attr-defined]
        if listener.type != event_type:
            continue
        handler = listener.handler
        freevars = getattr(getattr(handler, "__code__", None), "co_freevars", ()) or ()
        if "callback" in freevars:
            return handler.__closure__[freevars.index("callback")].cell_contents
        return handler
    return None


def _click(element: object, testid: str) -> None:
    target = _find(element, testid)
    assert target is not None, f"no element with testid {testid!r}"
    _click_handler(target)(None)


def _draft_of(save_button: object) -> Config:
    """Pull the in-progress draft out of the Save handler's closure."""
    handler = _click_handler(save_button)
    freevars = handler.__code__.co_freevars
    return handler.__closure__[freevars.index("draft")].cell_contents


# ---------------------------------------------------------------------------
# settings_sections_for visibility
# ---------------------------------------------------------------------------


def test_sections_always_include_nas_remote() -> None:
    # Always shown now -- even for a stage-only / no-equipment / no-config
    # install -- so the operator can configure the remote up front.
    assert "nas_remote" in settings.settings_sections_for(_config_with(_stage_equipment("STG1")))
    assert "nas_remote" in settings.settings_sections_for(_config_with())
    assert "nas_remote" in settings.settings_sections_for(None)


def test_sections_insert_nas_remote_after_equipment() -> None:
    config = _config_with(_nas_equipment("EQ1"))
    sections = settings.settings_sections_for(config)
    assert "nas_remote" in sections
    # Inserted immediately after the equipment section.
    eq_idx = sections.index("equipment")
    assert sections[eq_idx + 1] == "nas_remote"


# ---------------------------------------------------------------------------
# section rendering
# ---------------------------------------------------------------------------


def test_section_renders_editable_remote_base_root_and_config_path() -> None:
    config = _config_with(_nas_equipment("EQ1"), remote="nas01")
    config.nas.rclone_config_path = "/etc/rclone.conf"
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="nas_remote"),
        on_save=lambda s: None,
        nas_remote_available=lambda _name: True,
        on_test_connection=lambda _remote, _config_path: None,
    )
    ids = _section_testids(out, "nas_remote")
    assert "settings-nas-remote-name" in ids
    assert "settings-nas-remote-base-root" in ids
    assert "settings-nas-remote-config-path" in ids
    # Editable widgets carry the configured values (not read-only labels).
    assert _value_of(out, "settings-nas-remote-name") == "nas01"
    assert _value_of(out, "settings-nas-remote-base-root") == "/srv/nas"
    assert _value_of(out, "settings-nas-remote-config-path") == "/etc/rclone.conf"


def test_remote_dropdown_lists_detected_remotes_plus_current() -> None:
    config = _config_with(_nas_equipment("EQ1"), remote="nas01")
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="nas_remote"),
        on_save=lambda s: None,
        nas_remote_available=lambda _name: True,
        on_test_connection=lambda _remote, _config_path: None,
        # listremotes entries carry a trailing ":" -- the dropdown strips it.
        nas_remotes=("nas02:", "backup:"),
    )
    select = _find(out, "settings-nas-remote-name")
    assert type(select).__name__ == "Select"
    options = list(select.options)
    # Detected remotes (stripped) and the currently-configured one are all present.
    assert set(options) == {"nas01", "nas02", "backup"}


def test_remote_dropdown_keeps_undetected_current_remote_selectable() -> None:
    # rclone.conf currently unreadable (no detected remotes) -> the saved
    # remote must still appear as the selected option.
    config = _config_with(_nas_equipment("EQ1"), remote="ghost")
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="nas_remote"),
        on_save=lambda s: None,
        nas_remote_available=lambda _name: False,
        on_test_connection=lambda _remote, _config_path: None,
        nas_remotes=(),
    )
    select = _find(out, "settings-nas-remote-name")
    assert "ghost" in list(select.options)
    assert select.value == "ghost"


def test_section_has_no_password_input() -> None:
    config = _config_with(_nas_equipment("EQ1"))
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="nas_remote"),
        on_save=lambda s: None,
        nas_remote_available=lambda _name: True,
        on_test_connection=lambda _remote, _config_path: None,
    )
    # Scoped to the NAS-remote section body (the page also renders the
    # LIMS section, which legitimately has a password field).
    ids = _section_testids(out, "nas_remote")
    assert not any("password" in tid for tid in ids)
    assert not any("credential" in tid for tid in ids)


def test_section_has_test_connection_control() -> None:
    config = _config_with(_nas_equipment("EQ1"))
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="nas_remote"),
        on_save=lambda s: None,
        nas_remote_available=lambda _name: True,
        on_test_connection=lambda _remote, _config_path: None,
    )
    assert "settings-nas-test-connection" in _section_testids(out, "nas_remote")


def test_status_badge_found_when_remote_available() -> None:
    config = _config_with(_nas_equipment("EQ1"), remote="nas01")
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="nas_remote"),
        on_save=lambda s: None,
        nas_remote_available=lambda name: name == "nas01",
        on_test_connection=lambda _remote, _config_path: None,
    )
    status = _text_of(out, "settings-nas-remote-status")
    assert "Found" in status


def test_status_badge_not_found_when_remote_absent() -> None:
    config = _config_with(_nas_equipment("EQ1"), remote="nas01")
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="nas_remote"),
        on_save=lambda s: None,
        nas_remote_available=lambda _name: False,
        on_test_connection=lambda _remote, _config_path: None,
    )
    status = _text_of(out, "settings-nas-remote-status")
    assert "Not found" in status
    assert "rclone config" in status


def test_nav_entry_present_when_nas_equipment_exists() -> None:
    config = _config_with(_nas_equipment("EQ1"))
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="paths"),
        on_save=lambda s: None,
        nas_remote_available=lambda _name: True,
    )
    assert "settings-nav-nas_remote" in _testids(out)


def test_nav_entry_present_for_stage_only_config() -> None:
    # The NAS-remote nav row is always present now (even with no nas-mode
    # equipment), so the remote can be configured before equipment is added.
    config = _config_with(_stage_equipment("STG1"))
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="paths"),
        on_save=lambda s: None,
    )
    assert "settings-nav-nas_remote" in _testids(out)


# ---------------------------------------------------------------------------
# NAS-remote Test-connection handler
# ---------------------------------------------------------------------------


def test_test_connection_handler_invokes_callback_and_renders_panel() -> None:
    config = _config_with(_nas_equipment("EQ1"), remote="nas01")
    calls: list[tuple[str, str]] = []
    result = ConnResult(
        success=True,
        headline="Connected",
        detail="reached nas01 in 42ms",
        raw="rclone about nas01: -> ok",
    )
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="nas_remote"),
        on_save=lambda s: None,
        nas_remote_available=lambda _name: True,
        on_test_connection=lambda remote, config_path: (calls.append((remote, config_path)), result)[
            1
        ],
    )
    handler = _click_handler(_find(out, "settings-nas-test-connection"))
    asyncio.new_event_loop().run_until_complete(handler())

    # Probed with the configured (typed) values.
    assert calls == [("nas01", "")]
    # The panel rendered the success headline from the result inline.
    assert any(
        getattr(child, "text", "") == "Connected"
        for child in out.descendants()  # type: ignore[attr-defined]
    )


def test_test_connection_probes_typed_unsaved_values() -> None:
    # The probe must use the *live draft* values (what the operator typed),
    # not the originally-loaded config -- so a selection can be tested before
    # saving. Mutating the draft directly stands in for editing the widgets.
    config = _config_with(_nas_equipment("EQ1"), remote="nas01")
    calls: list[tuple[str, str]] = []
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="nas_remote"),
        on_save=lambda s: None,
        nas_remote_available=lambda _name: True,
        on_test_connection=lambda remote, config_path: (
            calls.append((remote, config_path)),
            None,
        )[1],
    )
    draft = _draft_of(_find(out, "settings-save"))
    draft.nas.remote = "typed99"
    draft.nas.rclone_config_path = "/custom/rclone.conf"

    handler = _click_handler(_find(out, "settings-nas-test-connection"))
    asyncio.new_event_loop().run_until_complete(handler())

    assert calls == [("typed99", "/custom/rclone.conf")]


def test_refresh_relists_remotes_from_typed_config_path() -> None:
    config = _config_with(_nas_equipment("EQ1"), remote="nas01")
    seen_paths: list[str] = []

    async def _list_remotes(config_path: str) -> tuple[str, ...]:
        seen_paths.append(config_path)
        return ("nas01:", "fresh:")

    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="nas_remote"),
        on_save=lambda s: None,
        nas_remote_available=lambda _name: True,
        on_test_connection=lambda _remote, _config_path: None,
        nas_remotes=("nas01:",),
        list_remotes=_list_remotes,
    )
    select = _find(out, "settings-nas-remote-name")
    assert "fresh" not in list(select.options)

    # The refresh re-lists using the typed config path and rebuilds options.
    draft = _draft_of(_find(out, "settings-save"))
    draft.nas.rclone_config_path = "/typed.conf"
    handler = _click_handler(_find(out, "settings-nas-remote-refresh"))
    asyncio.new_event_loop().run_until_complete(handler())

    assert seen_paths == ["/typed.conf"]
    assert set(select.options) == {"nas01", "fresh"}


def test_test_connection_handler_awaits_coroutine_result() -> None:
    config = _config_with(_nas_equipment("EQ1"), remote="nas01")
    result = ConnResult(
        success=False,
        headline="Connection failed",
        detail="remote not reachable",
        raw="exit code 1",
    )

    async def probe(_remote: str, _config_path: str) -> ConnResult:
        return result

    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="nas_remote"),
        on_save=lambda s: None,
        nas_remote_available=lambda _name: True,
        on_test_connection=probe,
    )
    handler = _click_handler(_find(out, "settings-nas-test-connection"))
    asyncio.new_event_loop().run_until_complete(handler())

    assert any(
        getattr(child, "text", "") == "Connection failed"
        for child in out.descendants()  # type: ignore[attr-defined]
    )


def test_test_connection_handler_no_op_without_callback() -> None:
    config = _config_with(_nas_equipment("EQ1"), remote="nas01")
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="nas_remote"),
        on_save=lambda s: None,
        nas_remote_available=lambda _name: True,
        on_test_connection=None,
    )
    handler = _click_handler(_find(out, "settings-nas-test-connection"))
    # No wired probe -> the coroutine clears the panel and returns early.
    asyncio.new_event_loop().run_until_complete(handler())
    assert "settings-nas-test-connection" in _section_testids(out, "nas_remote")


# ---------------------------------------------------------------------------
# Save / discard handlers
# ---------------------------------------------------------------------------


def test_save_emits_validated_config() -> None:
    saved: list[Config] = []
    out = settings.render_settings_page(
        config=Config(),
        state=settings.SettingsState(active_section="paths"),
        on_save=saved.append,
    )
    _click(out, "settings-save")
    assert len(saved) == 1
    assert isinstance(saved[0], Config)


def test_save_is_noop_when_no_handler_wired() -> None:
    out = settings.render_settings_page(
        config=Config(),
        state=settings.SettingsState(active_section="paths"),
        on_save=None,
    )
    # Must not raise even though on_save is None (early return).
    _click(out, "settings-save")


def test_save_swallows_validation_error_and_does_not_emit() -> None:
    saved: list[Config] = []
    out = settings.render_settings_page(
        config=Config(),
        state=settings.SettingsState(active_section="logging"),
        on_save=saved.append,
    )
    save_btn = _find(out, "settings-save")
    draft = _draft_of(save_btn)
    # logging.level only accepts DEBUG/INFO/WARN/ERROR; an invalid draft
    # makes finalize raise -> the handler reports and swallows it.
    draft.logging.level = "VERBOSE"
    _click_handler(save_btn)(None)
    assert saved == []

    # Fixing the field lets the next Save through.
    draft.logging.level = "INFO"
    _click_handler(save_btn)(None)
    assert len(saved) == 1


def test_discard_invokes_handler_with_state() -> None:
    discarded: list[settings.SettingsState] = []
    state = settings.SettingsState(active_section="paths")
    out = settings.render_settings_page(
        config=Config(),
        state=state,
        on_save=lambda s: None,
        on_discard=discarded.append,
    )
    _click(out, "settings-discard")
    assert len(discarded) == 1


# ---------------------------------------------------------------------------
# Sidebar section selection
# ---------------------------------------------------------------------------


def test_nav_click_selects_section_and_invokes_callback() -> None:
    config = _config_with(_nas_equipment("EQ1"))
    selected: list[str] = []
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="paths"),
        on_save=lambda s: None,
        on_select_section=selected.append,
        nas_remote_available=lambda _name: True,
    )
    _click(out, "settings-nav-lims")
    assert selected == ["lims"]


def test_nav_click_without_callback_does_not_raise() -> None:
    out = settings.render_settings_page(
        config=Config(),
        state=settings.SettingsState(active_section="paths"),
        on_save=lambda s: None,
        on_select_section=None,
    )
    # Visibility toggle still runs; the optional callback branch is skipped.
    _click(out, "settings-nav-lims")


# ---------------------------------------------------------------------------
# Equipment add sub-form
# ---------------------------------------------------------------------------


def test_equipment_add_appends_row() -> None:
    config = _config_with(_nas_equipment("EQ1"))
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="equipment"),
        on_save=lambda s: None,
        nas_remote_available=lambda _name: True,
    )
    _find(out, "settings-equipment-id").value = "EQ2"
    _find(out, "settings-equipment-label").value = "Bench 2"
    _find(out, "settings-equipment-nas-root").value = "/srv/nas2"
    _click(out, "settings-equipment-add")

    rows = [getattr(r, "text", "") for r in _find_all(out, "settings-equipment-row")]
    assert any("EQ2" in r for r in rows)
    # Inputs reset after a successful add.
    assert _find(out, "settings-equipment-id").value == ""


def test_equipment_add_rejects_duplicate_id() -> None:
    config = _config_with(_nas_equipment("EQ1"))
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="equipment"),
        on_save=lambda s: None,
        nas_remote_available=lambda _name: True,
    )
    _find(out, "settings-equipment-id").value = "EQ1"
    _find(out, "settings-equipment-label").value = "dup"
    _find(out, "settings-equipment-nas-root").value = "/srv/nas"
    _click(out, "settings-equipment-add")

    assert len(_find_all(out, "settings-equipment-row")) == 1


def test_equipment_add_rejects_invalid_id() -> None:
    config = _config_with(_nas_equipment("EQ1"))
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="equipment"),
        on_save=lambda s: None,
        nas_remote_available=lambda _name: True,
    )
    # Lowercase/hyphen id fails the ^[A-Z][A-Z0-9_]*$ pattern in build.
    _find(out, "settings-equipment-id").value = "bad-id"
    _find(out, "settings-equipment-label").value = "x"
    _find(out, "settings-equipment-nas-root").value = "/n"
    _click(out, "settings-equipment-add")

    assert len(_find_all(out, "settings-equipment-row")) == 1


# ---------------------------------------------------------------------------
# Chip editor (operators / scanned extensions)
# ---------------------------------------------------------------------------


def test_scan_ext_chip_remove_drops_entry() -> None:
    out = settings.render_settings_page(
        config=Config(),
        state=settings.SettingsState(active_section="validator"),
        on_save=lambda s: None,
    )
    before = len(_find_all(out, "settings-scan-ext-chip"))
    assert before > 0
    first_chip = _find_all(out, "settings-scan-ext-chip")[0]
    delete_btn = next(d for d in first_chip.descendants() if type(d).__name__ == "Button")
    _click_handler(delete_btn)(None)
    assert len(_find_all(out, "settings-scan-ext-chip")) == before - 1


def test_scan_ext_chip_reset_restores_defaults() -> None:
    out = settings.render_settings_page(
        config=Config(),
        state=settings.SettingsState(active_section="validator"),
        on_save=lambda s: None,
    )
    before = len(_find_all(out, "settings-scan-ext-chip"))
    first_chip = _find_all(out, "settings-scan-ext-chip")[0]
    delete_btn = next(d for d in first_chip.descendants() if type(d).__name__ == "Button")
    _click_handler(delete_btn)(None)
    assert len(_find_all(out, "settings-scan-ext-chip")) == before - 1
    # Reset re-runs _reset_extensions and re-renders the full default set.
    _click(out, "settings-scan-ext-reset")
    assert len(_find_all(out, "settings-scan-ext-chip")) == before


def test_scan_ext_chip_add_validates_leading_dot() -> None:
    out = settings.render_settings_page(
        config=Config(),
        state=settings.SettingsState(active_section="validator"),
        on_save=lambda s: None,
    )
    before = len(_find_all(out, "settings-scan-ext-chip"))
    inp = _find(out, "settings-scan-ext-input")

    # Missing leading dot -> rejected by the validate hook.
    inp.value = "txt"
    _click(out, "settings-scan-ext-add")
    assert len(_find_all(out, "settings-scan-ext-chip")) == before

    # Valid extension -> appended.
    inp.value = ".xyz"
    _click(out, "settings-scan-ext-add")
    assert len(_find_all(out, "settings-scan-ext-chip")) == before + 1

    # Duplicate -> rejected.
    inp.value = ".xyz"
    _click(out, "settings-scan-ext-add")
    assert len(_find_all(out, "settings-scan-ext-chip")) == before + 1

    # Blank / whitespace -> ignored.
    inp.value = "   "
    _click(out, "settings-scan-ext-add")
    assert len(_find_all(out, "settings-scan-ext-chip")) == before + 1


def test_operators_chip_add_appends_value() -> None:
    config = _config_with(_nas_equipment("EQ1"))
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="operators"),
        on_save=lambda s: None,
        nas_remote_available=lambda _name: True,
    )
    _find(out, "settings-operators-input").value = "alice"
    _click(out, "settings-operators-add")
    chips = _find_all(out, "settings-operators-chip")
    assert len(chips) == 1
    labels = [
        getattr(d, "text", "")
        for chip in chips
        for d in chip.descendants()
        if getattr(d, "text", "")
    ]
    assert "alice" in labels


# ---------------------------------------------------------------------------
# Application section: autostart + quit
# ---------------------------------------------------------------------------


class _ChangeEvent:
    def __init__(self, value: bool) -> None:
        self.value = value


def _fire_change(widget: object, value: bool) -> None:
    for handler in widget._change_handlers:  # type: ignore[attr-defined]
        handler(_ChangeEvent(value))


def test_autostart_toggle_invokes_setter() -> None:
    seen: list[bool] = []
    out = settings.render_settings_page(
        config=Config(),
        state=settings.SettingsState(active_section="application"),
        on_save=lambda s: None,
        autostart_registered=False,
        on_set_autostart=lambda v: (seen.append(v), v)[1],
    )
    _fire_change(_find(out, "settings-autostart"), True)
    assert seen == [True]


def test_autostart_toggle_reverts_when_setter_disagrees() -> None:
    seen: list[bool] = []

    def setter(value: bool) -> bool:
        seen.append(value)
        return False  # report the op failed -> handler reverts the box

    out = settings.render_settings_page(
        config=Config(),
        state=settings.SettingsState(active_section="application"),
        on_save=lambda s: None,
        autostart_registered=False,
        on_set_autostart=setter,
    )
    box = _find(out, "settings-autostart")
    _fire_change(box, True)
    assert seen == [True]
    # The guard collapses the re-entrant change fired by the revert assignment.
    assert seen.count(True) == 1


def test_autostart_disabled_when_no_setter_wired() -> None:
    out = settings.render_settings_page(
        config=Config(),
        state=settings.SettingsState(active_section="application"),
        on_save=lambda s: None,
        on_set_autostart=None,
    )
    box = _find(out, "settings-autostart")
    assert box._props.get("disable")
    # Guarded early-return: firing change with no setter is a no-op.
    _fire_change(box, True)


def test_quit_disabled_when_no_handler_wired() -> None:
    out = settings.render_settings_page(
        config=Config(),
        state=settings.SettingsState(active_section="application"),
        on_save=lambda s: None,
        on_quit=None,
    )
    assert _find(out, "settings-quit")._props.get("disable")


def test_quit_confirm_dialog_runs_callback() -> None:
    from nicegui import context

    quits: list[int] = []
    out = settings.render_settings_page(
        config=Config(),
        state=settings.SettingsState(active_section="application"),
        on_save=lambda s: None,
        on_quit=lambda: quits.append(1),
    )
    # Clicking Quit opens the confirm dialog (built in its own ui.dialog
    # context, so it lives in the client registry rather than under `out`).
    _click(out, "settings-quit")
    confirm_btn = next(
        el
        for el in context.client.elements.values()
        if el._props.get("data-testid") == "settings-quit-confirm"
    )
    _click_handler(confirm_btn)(None)
    assert quits == [1]


# ---------------------------------------------------------------------------
# Headless fallback (no NiceGUI app context)
# ---------------------------------------------------------------------------


def test_render_returns_payload_dict_when_nicegui_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config_with(_nas_equipment("EQ1"))
    real_import = builtins.__import__

    def blocking_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "nicegui":
            raise ImportError("nicegui unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocking_import)

    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="nas_remote"),
        on_save=lambda s: None,
        nas_remote_available=lambda _name: True,
    )
    assert isinstance(out, dict)
    assert out["active"] == "nas_remote"
    assert "nas_remote" in out["sections"]
