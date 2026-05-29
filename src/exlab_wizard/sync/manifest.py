"""Parser for ``rclone lsjson`` output into a run-relative remote manifest.

Sole owner of the lsjson wire shape. Consumers (the NAS sync client's
routine reconcile and cleanup remote-existence probe) depend only on the
:class:`RemoteManifest` value object, never on the raw JSON.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime

from exlab_wizard.logging import get_logger

__all__ = ["RemoteEntry", "RemoteManifest", "parse_lsjson"]

_log = get_logger(__name__)

# Trim fractional seconds to 6 digits: datetime.fromisoformat rejects the
# 9-digit nanosecond precision rclone can emit (e.g. "...:00.123456789Z").
_FRAC_RE = re.compile(r"\.(\d+)")


@dataclass(frozen=True, slots=True)
class RemoteEntry:
    """One file on the remote. ``mod_time`` is the RFC3339 string as emitted."""

    size: int
    mod_time: str
    is_dir: bool


@dataclass(frozen=True, slots=True)
class RemoteManifest:
    """Run-relative view of a remote subtree. Keyed by POSIX rel-path."""

    entries: dict[str, RemoteEntry]

    def size_matches(self, rel_path: str, local_size: int) -> bool:
        """True iff ``rel_path`` is present and its remote size equals local."""
        entry = self.entries.get(rel_path)
        return entry is not None and entry.size == local_size

    def has(self, rel_path: str) -> bool:
        """True iff ``rel_path`` is present on the remote (existence probe)."""
        return rel_path in self.entries

    def matches(
        self, rel_path: str, local_size: int, local_mtime: float, *, tolerance_s: float
    ) -> bool:
        """The single reconcile predicate: present AND size-equal AND modtime close.

        ``local_mtime`` is the local file's ``st_mtime`` (epoch seconds). A file
        is credited as synced only when the remote entry exists, its size equals
        ``local_size``, and its modtime is within ``tolerance_s`` of local
        (rclone preserves modtime on copy; the window absorbs SFTP/SMB rounding).
        """
        entry = self.entries.get(rel_path)
        if entry is None or entry.size != local_size:
            return False
        remote_epoch = self._to_epoch(entry.mod_time)
        if remote_epoch is None:
            return False
        return abs(remote_epoch - local_mtime) <= tolerance_s

    @staticmethod
    def _to_epoch(mod_time: str) -> float | None:
        """Parse an RFC3339 ``ModTime`` to epoch seconds, or ``None`` if unparseable."""
        s = mod_time.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        s = _FRAC_RE.sub(lambda m: "." + m.group(1)[:6], s, count=1)
        try:
            return datetime.fromisoformat(s).timestamp()
        except ValueError:
            return None


def parse_lsjson(raw: str, *, strip_prefix: str = "") -> RemoteManifest:
    """Parse ``rclone lsjson`` array text into a :class:`RemoteManifest`.

    Directory entries are dropped. ``strip_prefix`` (e.g.
    ``<base_root>/<equipment_id>/<run>``) is removed from each ``Path`` so
    keys are run-relative. Malformed / empty input yields an empty manifest
    (logged at debug) rather than raising — a listing we can't parse must
    not crash the sync worker.
    """
    text = raw.strip()
    if not text:
        return RemoteManifest(entries={})
    try:
        rows = json.loads(text)
    except json.JSONDecodeError:
        _log.debug("lsjson output was not valid JSON (%d chars)", len(text))
        return RemoteManifest(entries={})
    if not isinstance(rows, list):
        return RemoteManifest(entries={})

    prefix = strip_prefix.strip("/")
    out: dict[str, RemoteEntry] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("IsDir"):
            continue
        path = str(row.get("Path", "")).strip("/")
        if not path:
            continue
        if prefix and path.startswith(prefix + "/"):
            path = path[len(prefix) + 1 :]
        try:
            size = int(row.get("Size", -1))
        except (TypeError, ValueError):
            continue
        out[path] = RemoteEntry(
            size=size,
            mod_time=str(row.get("ModTime", "")),
            is_dir=False,
        )
    return RemoteManifest(entries=out)
