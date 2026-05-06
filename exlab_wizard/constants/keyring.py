"""OS-keyring service/username conventions used for credential storage.

The wizard stores at most one LIMS credential plus one credential per
configured equipment in the OS keyring. The service name and username
templates are committed by the design spec so that the same conventions
hold across releases and reinstalls.
"""

from __future__ import annotations

# Service name used for every keyring entry the wizard creates.
# Backend Spec §7.4.1.
KEYRING_SERVICE: str = "exlab-wizard"

# Username under which the LIMS credential is stored. Backend Spec §7.4.1.
KEYRING_USERNAME_LIMS: str = "lims"

# Template for the per-equipment NAS credential username. Format the
# template via ``keyring_nas_username`` rather than ``str.format`` directly.
# Backend Spec §7.4.1.
KEYRING_USERNAME_NAS_TEMPLATE: str = "nas:{equipment_id}"


def keyring_nas_username(equipment_id: str) -> str:
    """Return the keyring username for the given equipment's NAS credential.

    Equipment IDs are not validated here -- callers feed in an ID that has
    already passed ``EQUIPMENT_ID_PATTERN`` via the field validator. See
    Backend Spec §7.4.1.
    """

    return KEYRING_USERNAME_NAS_TEMPLATE.format(equipment_id=equipment_id)
