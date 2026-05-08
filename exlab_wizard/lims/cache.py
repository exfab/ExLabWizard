"""aiosqlite-backed cache for LIMS project rows. Backend Spec §7.2.4.

The cache lives at ``<xdg_cache_home>/exlab-wizard/lims_cache.db`` and
holds a flat row-per-project table keyed by ``(lims_endpoint, short_id)``.
Storing the endpoint as part of the primary key means a single workstation
can switch between LIMS instances during testing without colliding rows.

Access pattern:

- :meth:`LIMSCache.upsert_many` is called by ``LIMSClient.list_projects``
  on every successful refresh. The ``last_refreshed`` column is stamped
  with the wizard's current UTC ISO time at write.
- :meth:`LIMSCache.list_projects` and :meth:`LIMSCache.get_project` are
  consulted before the network for §7.2.4 cache-first lookups.
- :meth:`LIMSCache.is_fresh` returns True when the most recent
  ``last_refreshed`` for the endpoint is within ``ttl_hours``. Callers
  use this to decide whether to short-circuit a network refresh.
- The cache is **never** consulted for write paths -- there are no
  writes to LIMS in v1.

The rows we materialize back into LIMSProject use the ``fetched_at``
column from ``last_refreshed``. ``metadata`` is round-tripped through
the JSON column ``metadata_json``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite
import msgspec

from exlab_wizard.lims.schemas import LIMSProject
from exlab_wizard.logging import get_logger

__all__ = ["LIMSCache"]

logger = get_logger(__name__)


_CREATE_TABLE_SQL: str = """
CREATE TABLE IF NOT EXISTS lims_projects (
    lims_endpoint TEXT NOT NULL,
    short_id TEXT NOT NULL,
    uid TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL,
    contact_name TEXT,
    owner TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    last_refreshed TEXT NOT NULL,
    PRIMARY KEY (lims_endpoint, short_id)
);
"""

_CREATE_INDEX_SQL: str = """
CREATE INDEX IF NOT EXISTS idx_endpoint_refresh
    ON lims_projects(lims_endpoint, last_refreshed);
"""

_UPSERT_SQL: str = """
INSERT INTO lims_projects (
    lims_endpoint, short_id, uid, name, description, status,
    contact_name, owner, metadata_json, last_refreshed
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(lims_endpoint, short_id) DO UPDATE SET
    uid=excluded.uid,
    name=excluded.name,
    description=excluded.description,
    status=excluded.status,
    contact_name=excluded.contact_name,
    owner=excluded.owner,
    metadata_json=excluded.metadata_json,
    last_refreshed=excluded.last_refreshed
;
"""

_SELECT_ALL_SQL: str = """
SELECT uid, short_id, name, description, status, contact_name, owner,
       metadata_json, last_refreshed
FROM lims_projects
WHERE lims_endpoint = ?
"""

_SELECT_ONE_SQL: str = """
SELECT uid, short_id, name, description, status, contact_name, owner,
       metadata_json, last_refreshed
FROM lims_projects
WHERE lims_endpoint = ? AND (uid = ? OR short_id = ?)
LIMIT 1
"""

_MAX_REFRESH_SQL: str = """
SELECT MAX(last_refreshed) FROM lims_projects WHERE lims_endpoint = ?
"""


class LIMSCache:
    """SQLite TTL cache for LIMS project rows. Backend Spec §7.2.4.

    The cache is async-native via aiosqlite so that ``LIMSClient`` can
    interleave cache reads with httpx network calls without blocking
    the event loop.
    """

    def __init__(self, db_path: Path, *, ttl_hours: int = 24) -> None:
        self._db_path = Path(db_path)
        self._ttl_hours = ttl_hours
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Create the table and index if absent. Idempotent."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._db_path))
        await self._conn.execute(_CREATE_TABLE_SQL)
        await self._conn.execute(_CREATE_INDEX_SQL)
        await self._conn.commit()

    async def close(self) -> None:
        """Close the underlying aiosqlite connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def upsert_many(self, endpoint: str, projects: list[LIMSProject]) -> None:
        """Insert or update every row. ``last_refreshed`` is taken from
        each ``LIMSProject.fetched_at``; the caller stamps that value
        before invoking this method so a single refresh batch shares one
        timestamp.
        """
        if not projects:
            return
        conn = self._require_conn()
        rows = [
            (
                endpoint,
                project.short_id,
                project.uid,
                project.name,
                project.description,
                project.status,
                project.contact_name,
                project.owner,
                msgspec.json.encode(project.metadata).decode("utf-8"),
                project.fetched_at,
            )
            for project in projects
        ]
        await conn.executemany(_UPSERT_SQL, rows)
        await conn.commit()

    async def list_projects(
        self, endpoint: str, *, status_filter: list[str] | None = None
    ) -> list[LIMSProject]:
        """Return every cached project for ``endpoint``, optionally
        filtered by ``status_filter`` (an OR of allowed status values).
        """
        conn = self._require_conn()
        async with conn.execute(_SELECT_ALL_SQL, (endpoint,)) as cursor:
            rows = await cursor.fetchall()
        projects = [self._row_to_project(row) for row in rows]
        if status_filter:
            allowed = set(status_filter)
            projects = [p for p in projects if p.status in allowed]
        return projects

    async def get_project(self, endpoint: str, uid_or_short_id: str) -> LIMSProject | None:
        """Return one project by uid or short_id, or None if absent."""
        conn = self._require_conn()
        async with conn.execute(
            _SELECT_ONE_SQL, (endpoint, uid_or_short_id, uid_or_short_id)
        ) as cursor:
            row = await cursor.fetchone()
        return self._row_to_project(row) if row else None

    async def is_fresh(self, endpoint: str) -> bool:
        """True iff the most recent ``last_refreshed`` is within
        ``ttl_hours`` of the wizard's current UTC time. False when the
        cache has no rows for ``endpoint``.
        """
        conn = self._require_conn()
        async with conn.execute(_MAX_REFRESH_SQL, (endpoint,)) as cursor:
            row = await cursor.fetchone()
        if row is None or row[0] is None:
            return False
        try:
            most_recent = _parse_iso8601(row[0])
        except ValueError:
            logger.warning("lims_cache.invalid_timestamp", extra={"value": row[0]})
            return False
        cutoff = datetime.now(UTC) - timedelta(hours=self._ttl_hours)
        return most_recent >= cutoff

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            msg = "LIMSCache.init() has not been called"
            raise RuntimeError(msg)
        return self._conn

    @staticmethod
    def _row_to_project(row: aiosqlite.Row | tuple[Any, ...]) -> LIMSProject:
        (
            uid,
            short_id,
            name,
            description,
            status,
            contact_name,
            owner,
            metadata_json,
            last_refreshed,
        ) = row
        metadata = msgspec.json.decode(metadata_json) if metadata_json else {}
        return LIMSProject(
            uid=uid,
            short_id=short_id,
            name=name,
            description=description,
            status=status,
            contact_name=contact_name,
            owner=owner,
            metadata=metadata,
            fetched_at=last_refreshed,
        )


def _parse_iso8601(value: str) -> datetime:
    """Parse a UTC-stamped ISO 8601 string. Accepts trailing ``Z`` or
    ``+00:00`` and returns a timezone-aware datetime.
    """
    text = value.replace("Z", "+00:00") if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
