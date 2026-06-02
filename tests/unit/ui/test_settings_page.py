"""Tests for the settings-page config binding helpers.

``render_settings_page`` itself renders NiceGUI widgets and needs a
running client, so the unit-testable surface is the pure draft logic:
``build_settings_draft`` (seed the editable copy) and
``finalize_settings_draft`` (re-validate a mutated draft). The full
read -> edit -> save round-trip is exercised by the Playwright e2e.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

# Prime the api package before importing ui.pages so the pre-existing
# orchestrator <-> api import order resolves cleanly (see test_mount.py).
import exlab_wizard.api.app  # noqa: F401  -- import order matters
from exlab_wizard.config.models import Config
from exlab_wizard.ui.components import credential_field
from exlab_wizard.ui.pages.settings import (
    SECTION_TITLES,
    SETTINGS_SECTIONS,
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
    # templates_dir is now derived from the single app_root.
    assert draft.paths.templates_dir == str(Path(draft.paths.app_root) / "templates")


def test_build_draft_copies_existing_config() -> None:
    source = Config()
    source.paths.app_root = "/srv/exlab"
    source.lims.email = "operator@example"

    draft = build_settings_draft(source)

    assert draft is not source
    assert draft.paths is not source.paths
    assert draft.paths.app_root == "/srv/exlab"
    # The derived templates_dir tracks the copied app_root.
    assert draft.paths.templates_dir == "/srv/exlab/templates"
    assert draft.lims.email == "operator@example"


def test_draft_edits_do_not_leak_into_source() -> None:
    source = Config()
    draft = build_settings_draft(source)

    draft.paths.app_root = "/srv/exlab"
    draft.orchestrator.label = "BENCH-1"

    assert source.paths.app_root != "/srv/exlab"
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
    draft.paths.app_root = "/srv/exlab"
    draft.lims.endpoint = "https://lims.example"
    draft.lims.email = "operator@example"
    draft.orchestrator.label = "BENCH-1"
    draft.orchestrator.staging_root = "/srv/staging"

    finalized = finalize_settings_draft(draft)

    assert finalized.paths.app_root == "/srv/exlab"
    # The data root is derived from the single app_root.
    assert finalized.paths.local_root == "/srv/exlab/data"
    assert finalized.lims.endpoint == "https://lims.example"
    assert finalized.orchestrator.label == "BENCH-1"


def test_finalize_allows_blank_staging_root() -> None:
    """staging_root is opt-in: a blank value finalizes cleanly (the field is
    optional). The greyed placeholder suggestion is a render concern covered
    by the Playwright e2e, not this pure-logic layer."""
    draft = build_settings_draft(None)
    draft.orchestrator.label = "BENCH-1"
    draft.orchestrator.staging_root = ""

    finalized = finalize_settings_draft(draft)

    assert finalized.orchestrator.staging_root == ""
    assert finalized.orchestrator.label == "BENCH-1"


def test_orchestrator_section_is_titled_workstation() -> None:
    """Orchestrator/staging is hidden at the UI layer (see
    docs/superpowers/specs/2026-05-29-hide-orchestrator-staging-design.md): the
    section id stays ``"orchestrator"`` so the setup gate and section routing
    are untouched, but its operator-visible title is "Workstation" and only the
    label is collected (the staging-root input is gone). Guards against a revert
    to the old "Orchestrator Mode" title or dropping the section entirely."""
    assert SECTION_TITLES["orchestrator"] == "Workstation"
    assert "orchestrator" in SETTINGS_SECTIONS


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
