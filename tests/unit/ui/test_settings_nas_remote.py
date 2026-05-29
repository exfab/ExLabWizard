"""Tests for the Settings NAS-remote section (rclone.conf migration).

The section is read-only: it shows the single configured ``nas:`` remote
plus its base root, a found / not-found badge derived from
``nas_remote_available``, and a single Test-connection control. There is
no password input. The section is shown only when nas-mode equipment
exist and is hidden entirely for a stage-only / no-equipment install.
"""

from __future__ import annotations

# Prime the api package before importing ui.pages so the pre-existing
# orchestrator <-> api import order resolves cleanly (see test_mount.py).
import exlab_wizard.api.app  # noqa: F401  -- import order matters
from exlab_wizard.config.models import (
    Config,
    EquipmentConfig,
    NasConfig,
    OrchestratorConfig,
    OrchestratorStagingTransport,
    PathsConfig,
)
from exlab_wizard.constants import OrchestratorTransportType, SyncMode
from exlab_wizard.ui.pages import settings


def _nas_equipment(equipment_id: str) -> EquipmentConfig:
    # rclone.conf migration: nas-mode carries no per-equipment transport.
    return EquipmentConfig(
        id=equipment_id,
        label=f"Equipment {equipment_id}",
        local_root="/data",
        nas_root="/srv/nas",
        sync_mode=SyncMode.NAS,
    )


def _stage_equipment(equipment_id: str) -> EquipmentConfig:
    return EquipmentConfig(
        id=equipment_id,
        label=f"Stage {equipment_id}",
        local_root="/data",
        nas_root="/srv/nas",
        sync_mode=SyncMode.STAGE,
        orchestrator_staging_transport=OrchestratorStagingTransport(
            type=OrchestratorTransportType.FILE_TRANSFER,
            mount_point="/mnt/stage",
            staging_subpath="incoming",
        ),
    )


def _config_with(*equipment: EquipmentConfig, remote: str = "nas01") -> Config:
    return Config(
        paths=PathsConfig(templates_dir="/t", plugin_dir="/p", local_root="/d"),
        equipment=list(equipment),
        orchestrator=OrchestratorConfig(label="LAB", staging_root="/staging"),
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


# ---------------------------------------------------------------------------
# settings_sections_for visibility
# ---------------------------------------------------------------------------


def test_sections_omit_nas_remote_without_nas_equipment() -> None:
    config = _config_with(_stage_equipment("STG1"))
    assert "nas_remote" not in settings.settings_sections_for(config)
    # No equipment at all -> also omitted.
    assert "nas_remote" not in settings.settings_sections_for(_config_with())
    assert "nas_remote" not in settings.settings_sections_for(None)


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


def test_section_renders_configured_remote_and_base_root() -> None:
    config = _config_with(_nas_equipment("EQ1"), remote="nas01")
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="nas_remote"),
        on_save=lambda s: None,
        nas_remote_available=lambda _name: True,
        on_test_connection=lambda: None,
    )
    ids = _section_testids(out, "nas_remote")
    assert "settings-nas-remote-name" in ids
    assert "settings-nas-remote-base-root" in ids
    assert "nas01" in _text_of(out, "settings-nas-remote-name")
    assert "/srv/nas" in _text_of(out, "settings-nas-remote-base-root")


def test_section_has_no_password_input() -> None:
    config = _config_with(_nas_equipment("EQ1"))
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="nas_remote"),
        on_save=lambda s: None,
        nas_remote_available=lambda _name: True,
        on_test_connection=lambda: None,
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
        on_test_connection=lambda: None,
    )
    assert "settings-nas-test-connection" in _section_testids(out, "nas_remote")


def test_status_badge_found_when_remote_available() -> None:
    config = _config_with(_nas_equipment("EQ1"), remote="nas01")
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="nas_remote"),
        on_save=lambda s: None,
        nas_remote_available=lambda name: name == "nas01",
        on_test_connection=lambda: None,
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
        on_test_connection=lambda: None,
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


def test_nav_entry_absent_for_stage_only_config() -> None:
    config = _config_with(_stage_equipment("STG1"))
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="paths"),
        on_save=lambda s: None,
    )
    assert "settings-nav-nas_remote" not in _testids(out)
