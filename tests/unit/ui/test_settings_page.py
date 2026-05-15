"""Tests for the settings-page config binding helpers.

``render_settings_page`` itself renders NiceGUI widgets and needs a
running client, so the unit-testable surface is the pure draft logic:
``build_settings_draft`` (seed the editable copy) and
``finalize_settings_draft`` (re-validate a mutated draft). The full
read -> edit -> save round-trip is exercised by the Playwright e2e.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# Prime the api package before importing ui.pages so the pre-existing
# orchestrator <-> api import order resolves cleanly (see test_mount.py).
import exlab_wizard.api.app  # noqa: F401  -- import order matters
from exlab_wizard.config.models import Config
from exlab_wizard.ui.components import credential_field
from exlab_wizard.ui.pages.settings import (
    build_settings_draft,
    finalize_settings_draft,
    lims_credential_initial_state,
)


def test_build_draft_from_none_yields_defaults() -> None:
    draft = build_settings_draft(None)
    assert isinstance(draft, Config)
    # §9 defaults are present and editable.
    assert draft.logging.level == "INFO"
    assert draft.nas_cleanup.min_verify_passes == 2
    assert draft.paths.templates_dir == ""


def test_build_draft_copies_existing_config() -> None:
    source = Config()
    source.paths.templates_dir = "/srv/templates"
    source.lims.email = "operator@example"

    draft = build_settings_draft(source)

    assert draft is not source
    assert draft.paths is not source.paths
    assert draft.paths.templates_dir == "/srv/templates"
    assert draft.lims.email == "operator@example"


def test_draft_edits_do_not_leak_into_source() -> None:
    source = Config()
    draft = build_settings_draft(source)

    draft.paths.local_root = "/srv/data"
    draft.orchestrator.label = "BENCH-1"

    assert source.paths.local_root == ""
    assert source.orchestrator.label == ""


def test_finalize_coerces_widget_floats_back_to_int() -> None:
    draft = build_settings_draft(None)
    # ui.number hands back floats; finalize must coerce to the int field.
    draft.nas_cleanup.min_verify_passes = 4.0  # type: ignore[assignment]
    draft.validator.content_scan_max_mib = 12.0  # type: ignore[assignment]

    finalized = finalize_settings_draft(draft)

    assert finalized.nas_cleanup.min_verify_passes == 4
    assert isinstance(finalized.nas_cleanup.min_verify_passes, int)
    assert finalized.validator.content_scan_max_mib == 12


def test_finalize_round_trips_edited_scalar_fields() -> None:
    draft = build_settings_draft(None)
    draft.paths.templates_dir = "/srv/templates"
    draft.paths.plugin_dir = "/srv/plugins"
    draft.paths.local_root = "/srv/data"
    draft.lims.endpoint = "https://lims.example"
    draft.lims.email = "operator@example"
    draft.orchestrator.label = "BENCH-1"
    draft.orchestrator.staging_root = "/srv/staging"

    finalized = finalize_settings_draft(draft)

    assert finalized.paths.local_root == "/srv/data"
    assert finalized.lims.endpoint == "https://lims.example"
    assert finalized.orchestrator.label == "BENCH-1"


def test_finalize_raises_on_invalid_edit() -> None:
    draft = build_settings_draft(None)
    # logging.level only accepts DEBUG/INFO/WARN/ERROR.
    draft.logging.level = "VERBOSE"

    with pytest.raises(ValidationError):
        finalize_settings_draft(draft)


def test_lims_credential_initial_state_not_set_when_keyring_empty() -> None:
    state = lims_credential_initial_state(present=False)
    assert state.state == credential_field.STATE_NOT_SET


def test_lims_credential_initial_state_set_when_keyring_has_password() -> None:
    """A password already in the OS keyring opens the row in *Set*."""

    state = lims_credential_initial_state(present=True)
    assert state.state == credential_field.STATE_SET
    # The resting target matches so a cancelled Replace returns to Set.
    assert state.resting == credential_field.STATE_SET
