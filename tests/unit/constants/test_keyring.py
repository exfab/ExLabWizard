"""Verify the keyring service / username conventions.

These literals are committed by Backend Spec §7.4.1; they MUST be stable
across releases so that a reinstall of the wizard finds existing
credentials.
"""

from __future__ import annotations

from exlab_wizard.constants import keyring


def test_keyring_service_literal() -> None:
    # Backend Spec §7.4.1.
    assert keyring.KEYRING_SERVICE == "exlab-wizard"


def test_keyring_username_lims_literal() -> None:
    # Backend Spec §7.4.1.
    assert keyring.KEYRING_USERNAME_LIMS == "lims"


def test_keyring_username_nas_template_literal() -> None:
    # Backend Spec §7.4.1.
    assert keyring.KEYRING_USERNAME_NAS_TEMPLATE == "nas:{equipment_id}"


def test_keyring_nas_username_formats_template() -> None:
    # Spec example: ``CONFOCAL_01`` -> ``nas:CONFOCAL_01``.
    assert keyring.keyring_nas_username("CONFOCAL_01") == "nas:CONFOCAL_01"


def test_keyring_nas_username_handles_various_ids() -> None:
    # The helper does not validate the equipment ID -- the field validator
    # already enforces ``EQUIPMENT_ID_PATTERN``. Just check the formatter.
    samples = {
        "MICROSCOPE": "nas:MICROSCOPE",
        "XRD_LAB_2": "nas:XRD_LAB_2",
        "A": "nas:A",
        "Z9_FOO": "nas:Z9_FOO",
    }
    for equipment_id, expected in samples.items():
        assert keyring.keyring_nas_username(equipment_id) == expected


def test_keyring_re_exported_from_package() -> None:
    from exlab_wizard import constants

    assert constants.KEYRING_SERVICE == "exlab-wizard"
    assert constants.KEYRING_USERNAME_LIMS == "lims"
    assert constants.KEYRING_USERNAME_NAS_TEMPLATE == "nas:{equipment_id}"
    assert constants.keyring_nas_username("CONFOCAL_01") == "nas:CONFOCAL_01"
