"""Unit tests for ``build_rclone_env`` and ``_resolve_env_for_equipment``.

The new env-injection path is the production replacement for the
deleted rclone.conf-based credential map. These tests pin:

- the exact ``RCLONE_CONFIG_<remote>_*`` key names per backend
  (a typo here would silently disable env injection without rclone
  emitting a clear error),
- the conditional ``_DOMAIN`` key on the SMB transport,
- the AUTH-class ``TransportError`` raised when the keyring entry is
  missing, ``None``, the empty string, or unreachable.
"""

from __future__ import annotations

import pytest

from exlab_wizard.config.models import (
    BandwidthConfig,
    EquipmentConfig,
    RcloneSftpTransport,
    RcloneSmbTransport,
)
from exlab_wizard.sync.nas_client import _resolve_env_for_equipment
from exlab_wizard.sync.transports import TransportError, TransportErrorKind
from exlab_wizard.sync.transports.rclone import (
    build_rclone_env,
    pass_env_keys_for,
)

# ---------------------------------------------------------------------------
# build_rclone_env
# ---------------------------------------------------------------------------


def test_build_rclone_env_sftp_emits_all_keys() -> None:
    transport = RcloneSftpTransport(
        type="rclone_sftp",
        host="nas01.lab.example",
        port=2222,
        user="labuser",
        remote_path="/srv/lab/EQ1",
        bandwidth=BandwidthConfig(),
    )
    env = build_rclone_env(
        transport=transport,
        password_obscured="OBS_PW",
        remote_name="exlab_eq1",
    )
    assert env == {
        "RCLONE_CONFIG_EXLAB_EQ1_TYPE": "sftp",
        "RCLONE_CONFIG_EXLAB_EQ1_HOST": "nas01.lab.example",
        "RCLONE_CONFIG_EXLAB_EQ1_PORT": "2222",
        "RCLONE_CONFIG_EXLAB_EQ1_USER": "labuser",
        "RCLONE_CONFIG_EXLAB_EQ1_PASS": "OBS_PW",
    }


def test_build_rclone_env_smb_minimal_omits_domain() -> None:
    transport = RcloneSmbTransport(
        type="rclone_smb",
        host="nas01.lab.example",
        share="lab",
        user="labuser",
        domain="",
        bandwidth=BandwidthConfig(),
    )
    env = build_rclone_env(
        transport=transport,
        password_obscured="OBS_PW",
        remote_name="exlab_eq1",
    )
    assert env == {
        "RCLONE_CONFIG_EXLAB_EQ1_TYPE": "smb",
        "RCLONE_CONFIG_EXLAB_EQ1_HOST": "nas01.lab.example",
        "RCLONE_CONFIG_EXLAB_EQ1_USER": "labuser",
        "RCLONE_CONFIG_EXLAB_EQ1_PASS": "OBS_PW",
    }
    # The conditional DOMAIN key must NOT appear when domain is empty.
    assert "RCLONE_CONFIG_EXLAB_EQ1_DOMAIN" not in env


def test_build_rclone_env_smb_with_domain_includes_domain_key() -> None:
    transport = RcloneSmbTransport(
        type="rclone_smb",
        host="nas01.lab.example",
        share="lab",
        user="labuser",
        domain="LAB",
        bandwidth=BandwidthConfig(),
    )
    env = build_rclone_env(
        transport=transport,
        password_obscured="OBS_PW",
        remote_name="exlab_eq1",
    )
    assert env["RCLONE_CONFIG_EXLAB_EQ1_DOMAIN"] == "LAB"


def test_pass_env_keys_for_names_only_the_password_key() -> None:
    """The only secret in build_rclone_env's output is _PASS."""
    keys = pass_env_keys_for("exlab_eq1")
    assert keys == ("RCLONE_CONFIG_EXLAB_EQ1_PASS",)


# ---------------------------------------------------------------------------
# _resolve_env_for_equipment AUTH paths
# ---------------------------------------------------------------------------


class _Keyring:
    """In-memory keyring stub used by the AUTH-path tests below."""

    def __init__(self, password: str | None = "topsecret") -> None:
        self._password = password

    def get_password(self, *, username: str) -> str | None:
        del username
        return self._password


class _BrokenKeyring:
    """Keyring stub whose ``get_password`` raises an exception."""

    def get_password(self, *, username: str) -> str | None:
        del username
        msg = "OS keyring backend unavailable"
        raise RuntimeError(msg)


def _equipment(transport_id: str = "rclone_sftp") -> EquipmentConfig:
    if transport_id == "rclone_smb":
        transport = RcloneSmbTransport(
            type="rclone_smb",
            host="nas",
            share="lab",
            user="u",
            bandwidth=BandwidthConfig(),
        )
    else:
        transport = RcloneSftpTransport(  # type: ignore[assignment]
            type="rclone_sftp",
            host="nas",
            user="u",
            remote_path="/srv",
            bandwidth=BandwidthConfig(),
        )
    return EquipmentConfig(
        id="EQ1",
        label="Eq 1",
        local_root="/data",
        nas_root="/nas",
        transport=transport,
    )


async def test_resolve_env_raises_auth_when_keyring_store_is_none() -> None:
    eq = _equipment()
    with pytest.raises(TransportError) as excinfo:
        await _resolve_env_for_equipment(eq, keyring_store=None)
    assert excinfo.value.error_kind is TransportErrorKind.AUTH


async def test_resolve_env_raises_auth_when_password_is_none() -> None:
    eq = _equipment()
    with pytest.raises(TransportError) as excinfo:
        await _resolve_env_for_equipment(eq, keyring_store=_Keyring(password=None))
    assert excinfo.value.error_kind is TransportErrorKind.AUTH


async def test_resolve_env_raises_auth_when_password_is_empty_string() -> None:
    eq = _equipment()
    with pytest.raises(TransportError) as excinfo:
        await _resolve_env_for_equipment(eq, keyring_store=_Keyring(password=""))
    assert excinfo.value.error_kind is TransportErrorKind.AUTH


async def test_resolve_env_raises_auth_when_keyring_backend_explodes() -> None:
    """A keyring backend failure must surface as AUTH with the real cause attached.

    Earlier code swallowed the exception and reported "password not set",
    which made a broken keyring indistinguishable from an unconfigured
    one. The fix preserves the underlying ``__cause__`` for diagnosis.
    """
    eq = _equipment()
    with pytest.raises(TransportError) as excinfo:
        await _resolve_env_for_equipment(eq, keyring_store=_BrokenKeyring())
    assert excinfo.value.error_kind is TransportErrorKind.AUTH
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert "OS keyring backend unavailable" in str(excinfo.value)
