"""UTC ISO-8601 timestamp helpers.

Backend Spec §13.4 fixes the wire format used in every cache file and
on the LIMS REST API: ``%Y-%m-%dT%H:%M:%SZ`` (seconds resolution, ``Z``
suffix). Inline ``datetime.now(...).strftime(...)`` calls that drift
from this format are the most common silent wire-format bug in the
codebase, so all timestamp formatting funnels through this module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

__all__ = [
    "dt_to_iso",
    "parse_utc_iso",
    "parse_utc_iso_or_none",
    "utc_now",
    "utc_now_iso",
    "utc_now_or",
]


_ISO_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%SZ"


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware ``datetime``.

    Use this when callers need a ``datetime`` object (e.g. to compute a
    cutoff via :func:`timedelta`); use :func:`utc_now_iso` when callers
    need the formatted string. Centralizing the call keeps the test
    monkey-patching surface narrow.
    """
    return datetime.now(tz=UTC)


def utc_now_or(dt: datetime | None) -> datetime:
    """Return ``dt`` when supplied, otherwise the current UTC time.

    Mirrors the ``now = now or datetime.now(tz=UTC)`` idiom that recurs
    throughout the codebase whenever a function accepts an optional
    ``now=...`` parameter for deterministic tests.
    """
    return dt if dt is not None else datetime.now(tz=UTC)


def utc_now_iso() -> str:
    """Return the current UTC time as ``YYYY-MM-DDTHH:MM:SSZ``.

    Seconds-resolution per Backend Spec §13.4. Subsecond precision is
    not part of any cross-component contract.
    """
    return datetime.now(tz=UTC).strftime(_ISO_FORMAT)


def dt_to_iso(dt: datetime) -> str:
    """Format a UTC-aware ``datetime`` as ``YYYY-MM-DDTHH:MM:SSZ``.

    Naive datetimes are assumed to already represent UTC. Aware
    datetimes in a non-UTC zone are converted before formatting.
    """
    if dt.tzinfo is not None and dt.tzinfo.utcoffset(dt) != UTC.utcoffset(dt):
        dt = dt.astimezone(UTC)
    return dt.strftime(_ISO_FORMAT)


def parse_utc_iso(value: str) -> datetime:
    """Parse an ISO-8601 string into a UTC-aware ``datetime``.

    Accepts both the canonical ``Z`` suffix produced by :func:`utc_now_iso`
    and the ``+00:00`` form emitted by ``datetime.isoformat()``. Naive
    parses are tagged with ``UTC`` so the returned value is always
    timezone-aware. Raises ``ValueError`` on malformed input.
    """
    normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def parse_utc_iso_or_none(value: str | None) -> datetime | None:
    """Like :func:`parse_utc_iso` but returns ``None`` on missing or bad input.

    Use when callers want to silently degrade rather than raise -- e.g.
    when reading optional last-activity timestamps from a partially
    written cache file.
    """
    if not value:
        return None
    try:
        return parse_utc_iso(value)
    except ValueError:
        return None
