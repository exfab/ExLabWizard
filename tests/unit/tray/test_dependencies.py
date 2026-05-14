"""Tests for the production dependency factory's keyring wiring.

``build_production_dependencies`` wraps every component build in
best-effort error handling, so the parts that matter here are the ones
the GUI reaches for: the settings dialog's credential field needs
``deps.keyring_store`` to persist the LIMS password to the OS keyring,
and the read side (setup-state probe + LIMS client) must look the
password up under the same ``(exlab-wizard, lims)`` pair the dialog
writes to.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

import keyring
import keyring.backend

from exlab_wizard.config.models import Config
from exlab_wizard.constants import KEYRING_USERNAME_LIMS
from exlab_wizard.lims.keyring_store import KeyringStore
from exlab_wizard.tray.dependencies import (
    _build_lims_client,
    _check_keyring_present,
    build_production_dependencies,
)


class _InMemoryKeyring(keyring.backend.KeyringBackend):
    """Trivial in-memory keyring backend, mirroring the keyring-store tests."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):  # type: ignore[override]
        return self._store.get((service, username))

    def set_password(self, service, username, password):  # type: ignore[override]
        self._store[(service, username)] = password

    def delete_password(self, service, username):  # type: ignore[override]
        self._store.pop((service, username), None)


@contextlib.contextmanager
def _swap_keyring(backend: keyring.backend.KeyringBackend) -> Iterator[None]:
    previous = keyring.get_keyring()
    keyring.set_keyring(backend)
    try:
        yield
    finally:
        keyring.set_keyring(previous)


def test_build_production_dependencies_exposes_keyring_store(tmp_path: Path) -> None:
    """The settings GUI persists the LIMS password through deps.keyring_store."""

    deps = build_production_dependencies(tmp_path)

    assert isinstance(deps.keyring_store, KeyringStore)


def test_check_keyring_present_true_when_lims_password_stored(tmp_path: Path) -> None:
    """The probe must look up the password under KEYRING_USERNAME_LIMS.

    Regression: it used to call ``get_password(email)`` positionally,
    which raises (the store's API is keyword-only) and was silently
    swallowed -- so a configured password always read as absent.
    """

    with _swap_keyring(_InMemoryKeyring()):
        store = KeyringStore(state_dir=tmp_path)
        store.set_password(username=KEYRING_USERNAME_LIMS, password="hunter2")
        config = Config()
        config.lims.email = "operator@example"

        assert _check_keyring_present(store, config) is True


def test_lims_client_password_provider_reads_keyring_under_lims_username(
    tmp_path: Path,
) -> None:
    """The LIMS client's keyring provider resolves the stored password."""

    with _swap_keyring(_InMemoryKeyring()):
        store = KeyringStore(state_dir=tmp_path)
        store.set_password(username=KEYRING_USERNAME_LIMS, password="hunter2")
        config = Config()
        config.lims.endpoint = "https://lims.example"
        config.lims.email = "operator@example"

        client = _build_lims_client(config, store)

        assert client._password_provider() == "hunter2"
