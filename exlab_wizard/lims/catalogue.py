"""Offline LIMS project catalogue read/write. Backend Spec §7.2.9.

A catalogue is a single JSON document at a NAS-shared path that lets a
disconnected workstation populate its LIMS-project picker without
reaching the live LIMS API. The producer workstation writes the file
on every successful LIMS refresh; the consumer workstation reads it
when its local SQLite cache is empty AND the LIMS API is unreachable
(see §7.2.9.3 for the consumer trigger).

This module provides only the file-format I/O. The producer-vs-consumer
trigger logic, the warning-and-fall-through behavior on parse errors,
and the picker-badge annotation are integrated by the caller (typically
the LIMSClient or its supervising controller).

File format (§7.2.9.1):

```json
{
  "schema_version": "1.0",
  "produced_by": "LAB_STATION_01",
  "produced_at": "2026-05-05T14:23:00Z",
  "lims_endpoint": "https://lims.lab.example/api/v1",
  "projects": [ {LIMSProject row}, ... ]
}
```

Atomic write (§7.2.9.2): write to ``<path>.tmp.<pid>``, fsync, then
``os.replace`` to the final path. Concurrent producers are benign --
each rename is atomic; the last writer wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import msgspec

from exlab_wizard.constants import OFFLINE_CATALOGUE_VERSION
from exlab_wizard.errors import ConfigError
from exlab_wizard.io import atomic_write_bytes
from exlab_wizard.lims.schemas import LIMSProject
from exlab_wizard.logging import get_logger

__all__ = ["OfflineCatalogue", "read_catalogue", "write_catalogue"]

logger = get_logger(__name__)


@dataclass
class OfflineCatalogue:
    """Decoded offline catalogue. Backend Spec §7.2.9.1.

    ``schema_version`` is pinned to the constant declared in
    :mod:`exlab_wizard.constants.schema_versions`; mismatches surface
    as :class:`exlab_wizard.errors.ConfigError`.

    ``lims_endpoint`` is verified by :func:`read_catalogue` against the
    consumer's configured LIMS endpoint; mismatches are rejected per
    §7.2.9.3 to defend against accidentally pointing at a different
    lab's LIMS.
    """

    schema_version: str
    produced_by: str
    produced_at: str
    lims_endpoint: str
    projects: list[LIMSProject]


def read_catalogue(path: Path, *, expected_endpoint: str) -> OfflineCatalogue:
    """Read and validate the catalogue file.

    Raises :class:`exlab_wizard.errors.ConfigError` on any of:

    - file missing / unreadable
    - JSON parse error
    - ``schema_version`` is not the constant
      :data:`exlab_wizard.constants.OFFLINE_CATALOGUE_VERSION`
    - ``lims_endpoint`` differs from ``expected_endpoint`` (per
      §7.2.9.3 the producer's LIMS must match the consumer's
      configuration; cross-lab leakage is rejected, not warned).
    """
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        msg = f"offline catalogue not readable at {path}: {exc}"
        raise ConfigError(msg) from exc

    try:
        decoded = msgspec.json.decode(raw)
    except msgspec.DecodeError as exc:
        msg = f"offline catalogue at {path} is not valid JSON: {exc}"
        raise ConfigError(msg) from exc

    if not isinstance(decoded, dict):
        msg = f"offline catalogue at {path} is not a JSON object"
        raise ConfigError(msg)

    schema_version = decoded.get("schema_version")
    if schema_version != OFFLINE_CATALOGUE_VERSION:
        msg = (
            f"offline catalogue at {path} has schema_version "
            f"{schema_version!r}; expected {OFFLINE_CATALOGUE_VERSION!r}"
        )
        raise ConfigError(msg)

    lims_endpoint = decoded.get("lims_endpoint", "")
    if lims_endpoint != expected_endpoint:
        msg = (
            f"offline catalogue at {path} describes LIMS endpoint "
            f"{lims_endpoint!r}; expected {expected_endpoint!r}"
        )
        raise ConfigError(msg)

    project_rows = decoded.get("projects") or []
    projects = [msgspec.convert(row, LIMSProject) for row in project_rows]

    return OfflineCatalogue(
        schema_version=schema_version,
        produced_by=decoded.get("produced_by", ""),
        produced_at=decoded.get("produced_at", ""),
        lims_endpoint=lims_endpoint,
        projects=projects,
    )


def write_catalogue(path: Path, catalogue: OfflineCatalogue) -> None:
    """Atomically write ``catalogue`` to ``path``. Backend Spec §7.2.9.2.

    Protocol: serialize, write to ``<path>.tmp.<pid>``, fsync, then
    ``os.replace`` to the final path. Concurrent producers do not
    corrupt the file -- each rename is atomic; the last writer wins.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": catalogue.schema_version,
        "produced_by": catalogue.produced_by,
        "produced_at": catalogue.produced_at,
        "lims_endpoint": catalogue.lims_endpoint,
        "projects": [_project_to_dict(p) for p in catalogue.projects],
    }
    encoded = msgspec.json.encode(payload)
    atomic_write_bytes(target, encoded)


def _project_to_dict(project: LIMSProject) -> dict:
    """Re-emit a LIMSProject as a serializable dict."""
    return {
        "uid": project.uid,
        "short_id": project.short_id,
        "name": project.name,
        "description": project.description,
        "status": project.status,
        "contact_name": project.contact_name,
        "owner": project.owner,
        "metadata": project.metadata,
        "fetched_at": project.fetched_at,
    }
