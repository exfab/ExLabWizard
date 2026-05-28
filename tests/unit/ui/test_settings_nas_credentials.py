"""Tests for the Settings NAS-credentials section (rclone-only migration).

The section renders one keyring-credential row + Test-connection button
per nas-mode equipment whose transport requires a password, and is
hidden entirely when no such equipment exists.
"""

from __future__ import annotations

# Prime the api package before importing ui.pages so the pre-existing
# orchestrator <-> api import order resolves cleanly (see test_mount.py).
import exlab_wizard.api.app  # noqa: F401  -- import order matters
from exlab_wizard.config.models import (
    Config,
    EquipmentConfig,
    OrchestratorConfig,
    OrchestratorStagingTransport,
    PathsConfig,
    RcloneSftpTransport,
)
from exlab_wizard.constants import OrchestratorTransportType, SyncMode
from exlab_wizard.ui.pages import settings


def _nas_equipment(equipment_id: str) -> EquipmentConfig:
    return EquipmentConfig(
        id=equipment_id,
        label=f"Equipment {equipment_id}",
        local_root="/data",
        nas_root="/srv/nas",
        sync_mode=SyncMode.NAS,
        transport=RcloneSftpTransport(
            type="rclone_sftp",
            host="nas.lab.example",
            user="testuser",
            remote_path=f"lab/{equipment_id}",
        ),
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


def _config_with(*equipment: EquipmentConfig) -> Config:
    return Config(
        paths=PathsConfig(templates_dir="/t", plugin_dir="/p", local_root="/d"),
        equipment=list(equipment),
        orchestrator=OrchestratorConfig(label="LAB", staging_root="/staging"),
    )


def _testids(element: object) -> set[str]:
    return {
        tid
        for child in element.descendants()  # type: ignore[attr-defined]
        if (tid := child._props.get("data-testid"))
    }


# ---------------------------------------------------------------------------
# settings_sections_for visibility
# ---------------------------------------------------------------------------


def test_sections_omit_nas_credentials_without_password_equipment() -> None:
    config = _config_with(_stage_equipment("STG1"))
    assert "nas_credentials" not in settings.settings_sections_for(config)
    # No equipment at all -> also omitted.
    assert "nas_credentials" not in settings.settings_sections_for(_config_with())
    assert "nas_credentials" not in settings.settings_sections_for(None)


def test_sections_insert_nas_credentials_after_equipment() -> None:
    config = _config_with(_nas_equipment("EQ1"))
    sections = settings.settings_sections_for(config)
    assert "nas_credentials" in sections
    # Inserted immediately after the equipment section.
    eq_idx = sections.index("equipment")
    assert sections[eq_idx + 1] == "nas_credentials"


# ---------------------------------------------------------------------------
# section rendering
# ---------------------------------------------------------------------------


def test_section_renders_one_row_per_password_equipment() -> None:
    config = _config_with(_nas_equipment("EQ1"), _nas_equipment("EQ2"))
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="nas_credentials"),
        on_save=lambda s: None,
        nas_password_present_for=lambda _id: False,
        nas_credential_handlers=lambda _id: ((lambda _v: None), (lambda: None)),
        on_test_equipment=lambda _id: None,
    )
    ids = _testids(out)
    assert "settings-nas-credential-row-EQ1" in ids
    assert "settings-nas-credential-row-EQ2" in ids
    assert "settings-nas-password-EQ1-primary" in ids
    assert "settings-nas-password-EQ2-primary" in ids
    assert "settings-nas-test-EQ1" in ids
    assert "settings-nas-test-EQ2" in ids


def test_section_nav_entry_present_when_password_equipment_exists() -> None:
    config = _config_with(_nas_equipment("EQ1"))
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="paths"),
        on_save=lambda s: None,
        nas_password_present_for=lambda _id: False,
    )
    ids = _testids(out)
    assert "settings-nav-nas_credentials" in ids


def test_section_nav_entry_absent_for_stage_only_config() -> None:
    config = _config_with(_stage_equipment("STG1"))
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="paths"),
        on_save=lambda s: None,
    )
    ids = _testids(out)
    assert "settings-nav-nas_credentials" not in ids


def test_present_password_opens_set_state_with_clear_action() -> None:
    config = _config_with(_nas_equipment("EQ1"))
    out = settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="nas_credentials"),
        on_save=lambda s: None,
        nas_password_present_for=lambda equipment_id: equipment_id == "EQ1",
        nas_credential_handlers=lambda _id: ((lambda _v: None), (lambda: None)),
    )
    ids = _testids(out)
    # The Clear action only exists in the resting "set" state.
    assert "settings-nas-password-EQ1-secondary" in ids


def test_handlers_factory_called_per_equipment_id() -> None:
    config = _config_with(_nas_equipment("EQ1"), _nas_equipment("EQ2"))
    seen: list[str] = []

    def _handlers(equipment_id: str) -> tuple:
        seen.append(equipment_id)
        return (lambda _v: None), (lambda: None)

    settings.render_settings_page(
        config=config,
        state=settings.SettingsState(active_section="nas_credentials"),
        on_save=lambda s: None,
        nas_password_present_for=lambda _id: False,
        nas_credential_handlers=_handlers,
    )
    # Each equipment gets its own handler closure built with the right id.
    assert set(seen) == {"EQ1", "EQ2"}
