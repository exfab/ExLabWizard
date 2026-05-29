"""OS-keyring service/username conventions used for credential storage.

The wizard stores at most one LIMS credential in the OS keyring. The
service name and username are committed by the design spec so that the
same conventions hold across releases and reinstalls.

rclone.conf NAS-sync migration: NAS credentials no longer live in the
keyring -- they are defined entirely by the named remote in the
operator's ``rclone.conf`` -- so the per-equipment NAS keyring
username/template were removed.
"""

from __future__ import annotations

from exlab_wizard.constants.app import APP_NAME

# Service name used for every keyring entry the wizard creates.
# Backend Spec §7.4.1.
KEYRING_SERVICE: str = APP_NAME

# Username under which the LIMS credential is stored. Backend Spec §7.4.1.
KEYRING_USERNAME_LIMS: str = "lims"
