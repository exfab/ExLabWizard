"""msgspec.Struct types for the LIMS read-only client. Backend Spec §7.2.

These types model the subset of LIMS data the wizard reads via Mapping B.
The LIMS itself owns project identity; ExLab-Wizard only consumes it. The
LIMSProject struct mirrors the fields documented in §7.2.3 plus a
``fetched_at`` timestamp used by the local SQLite cache for freshness
bookkeeping.

Style:
- ``kw_only=True`` on every Struct so callers always specify field names
  -- the wire-format ordering of LIMS fields is not a stable contract.
- ``forbid_unknown_fields=False`` so that LIMS payload additions in
  future versions do not break the read path. Unknown fields are
  silently dropped by ``msgspec.json.decode``; we re-emit only the
  fields we know about when writing the offline catalogue.
- ``frozen=False`` on LIMSProject because the cache layer mutates
  ``fetched_at`` when re-stamping rows; LIMSUser is frozen since users
  are read-only.
"""

from __future__ import annotations

from dataclasses import dataclass

from msgspec import Struct

from exlab_wizard.constants import LIMSProjectStatus

__all__ = [
    "HealthStatus",
    "LIMSProject",
    "LIMSUser",
]


class LIMSProject(
    Struct,
    kw_only=True,
    forbid_unknown_fields=False,
):
    """One LIMS project row. Backend Spec §7.2.3.

    The ``metadata`` field is a JSONB blob the LIMS owns; ExLab-Wizard
    does not mutate it. ``fetched_at`` is a UTC ISO 8601 timestamp set
    when the wizard pulled the row -- the local cache uses it for
    freshness bookkeeping (§7.2.4).
    """

    uid: str
    short_id: str
    name: str
    status: LIMSProjectStatus
    owner: str
    fetched_at: str
    description: str | None = None
    contact_name: str | None = None
    metadata: dict = {}


class LIMSUser(
    Struct,
    kw_only=True,
    frozen=True,
    forbid_unknown_fields=False,
):
    """One LIMS user row. Backend Spec §7.2.3.

    Mirrors the upstream ``safe_user`` contract returned by
    ``GET /api/v1/me``. Only the fields ExLab-Wizard surfaces are
    typed; everything else is dropped by msgspec.
    """

    uid: str
    email: str
    role: str


@dataclass(frozen=True)
class HealthStatus:
    """Result of ``LIMSClient.health_check()``. Backend Spec §7.2.3.

    ``ok`` is True iff the LIMS responded to ``GET /me`` with 2xx.
    ``latency_ms`` is the wall-clock duration in milliseconds (rounded
    to the nearest int). ``reason`` is None on success and carries a
    short human-readable failure summary on failure.
    """

    ok: bool
    latency_ms: int
    reason: str | None = None
