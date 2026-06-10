"""Parser for ``rclone lsjson`` output into a run-relative remote manifest.

Sole owner of the lsjson wire shape. Consumers (the NAS sync client's
routine reconcile and cleanup remote-existence probe) depend only on the
:class:`RemoteManifest` value object, never on the raw JSON.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from exlab_wizard.logging import get_logger

__all__ = ["RemoteEntry", "RemoteManifest", "parse_lsjson", "parse_rsync_listing"]

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


# ``rsync --list-only`` line: perms, size (possibly digit-grouped), the
# fixed-format timestamp, then EVERYTHING after the single separating
# space is the path — filenames containing spaces appear literally
# (spec-review blocker, 2026-06-10), so the line must be anchored on the
# timestamp, never whitespace-split.
_RSYNC_LIST_RE = re.compile(
    r"^(?P<perms>\S+)\s+(?P<size>[\d,.]+)\s+"
    r"(?P<date>\d{4}/\d{2}/\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2})\s(?P<path>.+)$"
)

# rsync escapes unusual bytes in listings as ``\#ooo`` (3 octal digits).
_RSYNC_ESCAPE_RE = re.compile(r"\\#([0-7]{3})")


def _unescape_rsync_path(path: str) -> str:
    """Decode rsync's ``\\#ooo`` octal escapes back into the real bytes."""
    if "\\#" not in path:
        return path
    out = bytearray()
    idx = 0
    for match in _RSYNC_ESCAPE_RE.finditer(path):
        out += path[idx : match.start()].encode("utf-8")
        out.append(int(match.group(1), 8))
        idx = match.end()
    out += path[idx:].encode("utf-8")
    return out.decode("utf-8", errors="replace")


def parse_rsync_listing(raw: str) -> RemoteManifest:
    """Parse ``rsync --list-only -r`` output into a :class:`RemoteManifest`.

    rsync-over-ssh NAS transport (2026-06-10). Paths are already relative
    to the listed target, so there is no ``strip_prefix``. Only regular
    files (``-`` perm prefix) are kept — directories (including the ``.``
    top entry) and symlinks are dropped, matching :func:`parse_lsjson`.
    The listing timestamp has 1 s resolution and is formatted in the
    local timezone of the process that renders the file list; it is
    parsed as local time and stored as an RFC3339 UTC string so
    :meth:`RemoteManifest.matches` works identically for both transports.
    Unparseable lines are skipped (a listing we can't parse must not
    crash the sync worker).
    """
    out: dict[str, RemoteEntry] = {}
    for raw_line in raw.splitlines():
        match = _RSYNC_LIST_RE.match(raw_line.rstrip())
        if match is None:
            continue
        if not match.group("perms").startswith("-"):
            continue
        path = _unescape_rsync_path(match.group("path"))
        if not path or path == ".":
            continue
        try:
            size = int(re.sub(r"[,.]", "", match.group("size")))
        except ValueError:
            continue
        try:
            local_dt = datetime.strptime(  # naive-as-local is the point
                f"{match.group('date')} {match.group('time')}", "%Y/%m/%d %H:%M:%S"
            )
        except ValueError:
            continue
        mod_time = local_dt.astimezone(UTC).isoformat().replace("+00:00", "Z")
        out[path] = RemoteEntry(size=size, mod_time=mod_time, is_dir=False)
    return RemoteManifest(entries=out)
