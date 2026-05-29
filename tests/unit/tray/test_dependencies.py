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
import pytest

from exlab_wizard.config.models import (
    BandwidthConfig,
    Config,
    EquipmentConfig,
    PathsConfig,
    RcloneSftpTransport,
)
from exlab_wizard.constants import KEYRING_USERNAME_LIMS, SyncMode
from exlab_wizard.constants.keyring import keyring_nas_username
from exlab_wizard.lims.keyring_store import KeyringStore
from exlab_wizard.sync.nas_client import NASSyncClient
from exlab_wizard.tray import dependencies as deps_module
from exlab_wizard.tray.dependencies import (
    _build_lims_client,
    _check_keyring_present,
    _check_nas_passwords_present,
    _make_equipment_probe,
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


def test_build_production_dependencies_nas_sync_is_a_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``deps.nas_sync`` must be a :class:`NASSyncClient`, not a bare queue.

    Regression: ``_build_nas_sync`` once returned a ``SyncQueue``, which
    has neither ``enqueue`` nor ``status`` -- the poller sweep and the
    force-sync route call both, so the bug surfaced only as a silent dead
    sync loop in production. This is the test that catches it.
    """
    local_root = tmp_path / "lab-data"
    local_root.mkdir()
    config = Config(
        paths=PathsConfig(local_root=str(local_root)),
        equipment=[
            EquipmentConfig(
                id="EQNAS",
                label="Nas Equipment",
                local_root=str(local_root),
                nas_root="/nas",
                sync_mode=SyncMode.NAS,
                transport=RcloneSftpTransport(
                    type="rclone_sftp",
                    host="nas.lab.example",
                    user="testuser",
                    remote_path="/srv/nas",
                    bandwidth=BandwidthConfig(),
                ),
            ),
        ],
    )
    monkeypatch.setattr(deps_module, "_load_config_safely", lambda: config)

    deps = build_production_dependencies(tmp_path)

    assert isinstance(deps.nas_sync, NASSyncClient)
    assert callable(deps.nas_sync.enqueue)
    assert callable(deps.nas_sync.status)


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


# ---------------------------------------------------------------------------
# NAS password presence + equipment probe
# ---------------------------------------------------------------------------


def _nas_config_with_two_equipment() -> Config:
    """Build a two-equipment nas-mode config for the NAS-presence tests."""
    return Config(
        paths=PathsConfig(local_root="/data"),
        equipment=[
            EquipmentConfig(
                id="EQ1",
                label="One",
                local_root="/data",
                nas_root="/srv/nas",
                sync_mode=SyncMode.NAS,
                transport=RcloneSftpTransport(
                    type="rclone_sftp",
                    host="nas.lab.example",
                    user="alice",
                    remote_path="lab/EQ1",
                ),
            ),
            EquipmentConfig(
                id="EQ2",
                label="Two",
                local_root="/data",
                nas_root="/srv/nas",
                sync_mode=SyncMode.NAS,
                transport=RcloneSftpTransport(
                    type="rclone_sftp",
                    host="nas.lab.example",
                    user="bob",
                    remote_path="lab/EQ2",
                ),
            ),
        ],
    )


def test_check_nas_passwords_present_returns_only_populated_ids(
    tmp_path: Path,
) -> None:
    with _swap_keyring(_InMemoryKeyring()):
        store = KeyringStore(state_dir=tmp_path)
        # Only EQ1 has its keyring entry; EQ2 must not appear.
        store.set_password(username=keyring_nas_username("EQ1"), password="hunter2")
        config = _nas_config_with_two_equipment()

        present = _check_nas_passwords_present(store, config)

        assert present == {"EQ1"}


def test_check_nas_passwords_present_handles_none_store_and_config() -> None:
    config = _nas_config_with_two_equipment()
    assert _check_nas_passwords_present(None, config) == set()
    assert _check_nas_passwords_present(object(), None) == set()


def _nas_config_with_remote(remote: str = "nas01") -> Config:
    """``_nas_config_with_two_equipment`` plus a configured ``nas:`` block."""
    from exlab_wizard.config.models import NasConfig

    config = _nas_config_with_two_equipment()
    return config.model_copy(update={"nas": NasConfig(remote=remote, base_root="/srv/nas")})


class _StubAbout:
    def __init__(self, *, ok: bool, reason: str | None = None) -> None:
        self.ok = ok
        self.reason = reason
        self.info: dict[str, int] = {}


def test_make_equipment_probe_targets_nas_remote_not_keyring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe must call ``rclone about <nas.remote>:`` and ignore the keyring."""
    import asyncio

    from exlab_wizard.sync.transports import rclone as rclone_module

    calls: list[str] = []

    class _StubDriver:
        def __init__(self, *, config_path: str | None = None) -> None:
            self.config_path = config_path

        async def about(self, remote: str, **_kwargs: object) -> _StubAbout:
            calls.append(remote)
            return _StubAbout(ok=True)

    monkeypatch.setattr(rclone_module, "RcloneDriver", _StubDriver)

    deps = build_production_dependencies(tmp_path)
    # No keyring password is set; the new probe must not consult it.
    deps.keyring_store = None
    deps.config = _nas_config_with_remote("nas01")

    probe = _make_equipment_probe(deps)
    result = asyncio.run(probe(deps.config.equipment[0]))

    assert result["ok"] is True, result
    assert calls == ["nas01:"]


def test_make_equipment_probe_short_circuits_without_remote(tmp_path: Path) -> None:
    """No configured ``nas.remote`` must not spawn rclone."""
    import asyncio

    deps = build_production_dependencies(tmp_path)
    deps.config = _nas_config_with_two_equipment()  # default NasConfig() -> blank remote

    probe = _make_equipment_probe(deps)
    result = asyncio.run(probe(deps.config.equipment[0]))

    assert result["ok"] is False
    assert "no NAS remote configured" in (result["reason"] or "")


def test_make_equipment_probe_surfaces_about_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing ``rclone about`` surfaces as ``ok=False`` with the reason."""
    import asyncio

    from exlab_wizard.sync.transports import rclone as rclone_module

    class _StubDriver:
        def __init__(self, *, config_path: str | None = None) -> None:
            self.config_path = config_path

        async def about(self, remote: str, **_kwargs: object) -> _StubAbout:
            return _StubAbout(ok=False, reason="auth_error: 401 Unauthorized")

    monkeypatch.setattr(rclone_module, "RcloneDriver", _StubDriver)

    deps = build_production_dependencies(tmp_path)
    deps.config = _nas_config_with_remote("nas01")

    probe = _make_equipment_probe(deps)
    result = asyncio.run(probe(deps.config.equipment[0]))

    assert result["ok"] is False
    assert "401" in (result["reason"] or "")


def test_nas_remote_available_reflects_listremotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Boot-time hydration drives ``deps.nas_remote_available`` from listremotes."""
    from exlab_wizard.sync.transports import rclone as rclone_module
    from exlab_wizard.tray import dependencies as deps_mod

    class _StubDriver:
        def __init__(self, *, config_path: str | None = None) -> None:
            self.config_path = config_path

        async def listremotes(self) -> tuple[str, ...]:
            return ("nas01:", "archive:")

    monkeypatch.setattr(rclone_module, "RcloneDriver", _StubDriver)

    config = _nas_config_with_remote("nas01")
    remotes = deps_mod._hydrate_nas_remotes(config)
    assert remotes == ("nas01:", "archive:")

    # The predicate the API setup gate reads matches on ``<remote>:``.
    available = lambda remote: f"{remote}:" in remotes  # noqa: E731
    assert available("nas01") is True
    assert available("missing") is False
