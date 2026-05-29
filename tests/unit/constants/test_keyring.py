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


def test_nas_keyring_username_removed() -> None:
    # rclone.conf NAS-sync migration (Phase 7): NAS credentials live in
    # the operator's rclone.conf, so the per-equipment NAS keyring
    # username/template were removed.
    assert not hasattr(keyring, "keyring_nas_username")
    assert not hasattr(keyring, "KEYRING_USERNAME_NAS_TEMPLATE")


def test_keyring_re_exported_from_package() -> None:
    from exlab_wizard import constants

    assert constants.KEYRING_SERVICE == "exlab-wizard"
    assert constants.KEYRING_USERNAME_LIMS == "lims"
    assert not hasattr(constants, "keyring_nas_username")
    assert not hasattr(constants, "KEYRING_USERNAME_NAS_TEMPLATE")
