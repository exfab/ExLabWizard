"""LIMS integration package. Backend Spec §7.2 + §7.4.

Public surface:

- :class:`LIMSClient` -- read-only REST client (Mapping B; §7.2).
- :class:`LIMSCache` -- aiosqlite TTL cache for project rows (§7.2.4).
- :class:`OfflineCatalogue` + :func:`read_catalogue` /
  :func:`write_catalogue` -- NAS-shared offline catalogue (§7.2.9).
- :class:`KeyringStore` -- OS keyring with encrypted-at-rest fallback
  for credential storage (§7.4).
- :class:`LIMSProject`, :class:`LIMSUser`, :class:`HealthStatus` --
  the typed values exchanged across the package boundary.
"""

from exlab_wizard.lims.cache import LIMSCache
from exlab_wizard.lims.catalogue import OfflineCatalogue, read_catalogue, write_catalogue
from exlab_wizard.lims.client import LIMSClient
from exlab_wizard.lims.keyring_store import KeyringStore
from exlab_wizard.lims.schemas import HealthStatus, LIMSProject, LIMSUser

__all__ = [
    "HealthStatus",
    "KeyringStore",
    "LIMSCache",
    "LIMSClient",
    "LIMSProject",
    "LIMSUser",
    "OfflineCatalogue",
    "read_catalogue",
    "write_catalogue",
]
