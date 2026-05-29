"""Unit tests for ``build_rclone_env`` and ``pass_env_keys_for``.

The env-injection helpers remain the building blocks for the tray's
inline equipment-probe path (``tray/dependencies.py``). These tests pin:

- the exact ``RCLONE_CONFIG_<remote>_*`` key names per backend
  (a typo here would silently disable env injection without rclone
  emitting a clear error),
- the conditional ``_DOMAIN`` key on the SMB transport,
- the single redacted ``_PASS`` key surfaced by ``pass_env_keys_for``.

The keyring-resolution AUTH paths previously exercised here moved out of
``nas_client`` with the rclone-named-remote migration; credential
handling now lives entirely in the operator's ``rclone.conf``.
"""

from __future__ import annotations

from exlab_wizard.config.models import (
    BandwidthConfig,
    RcloneSftpTransport,
    RcloneSmbTransport,
)
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
