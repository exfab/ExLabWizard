"""Append-only writer for ``wizard.<hostname>.log`` files. Backend Spec §11.5, §16.2.4.

Low-level companion to the logger handler chain in ``exlab_wizard/logging/``.
This module owns the canonical line shape and the on-disk append semantics
so that both the high-level :class:`StructuredTagFormatter` (§16.4) and any
direct callers (e.g. the equipment-scoped file handler in §16.2.4) write
identical bytes.

The line format follows §11.5 verbatim:

::

    <UTC ISO 8601 timestamp> [<LEVEL:5>] [host:..] [equip:..] [proj:..] [kind:..] [run:..] <message>

Two public surfaces:

- :func:`format_log_line` -- pure function. Given a timestamp, level,
  message, and optional context tags, returns a single line WITHOUT a
  trailing newline. Truncates messages whose UTF-8 length exceeds
  ``LOG_LINE_MAX_BYTES`` with a literal ``...[truncated]`` marker
  (Backend §4.5).
- :func:`append_log_line` -- side-effecting. Appends a single line to a
  ``wizard.<hostname>.log`` file, creating the parent ``.exlab-wizard``
  directory if missing. Atomic up to ``PIPE_BUF`` on POSIX via
  ``O_APPEND``; on Windows the ``mode="a"`` open flag passes
  ``FILE_APPEND_DATA`` to the OS, which makes a single short append
  serializable against any other ``mode="a"`` writer on the same file
  (Backend §4.5 same-equipment concurrency rule).

The writer here is intentionally synchronous and side-effecting; the
non-blocking emit pipeline (``QueueHandler`` + ``QueueListener``;
§16.2.5) wraps it so the asyncio event loop is not blocked on filesystem
writes.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from exlab_wizard.constants import LOG_LINE_MAX_BYTES

__all__ = [
    "append_log_line",
    "format_log_line",
]


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------


# Mapping from kwarg name to the tag prefix that appears in the rendered
# line. Order matches §11.5 / §16.4: host, equip, proj, kind, run.
_TAG_ORDER: tuple[tuple[str, str], ...] = (
    ("host", "host"),
    ("equipment_id", "equip"),
    ("project_short_id", "proj"),
    ("run_kind", "kind"),
    ("run_id", "run"),
)


# Marker appended when a message must be truncated to satisfy
# ``LOG_LINE_MAX_BYTES``. The literal text is parsed by downstream tooling,
# so changing it is a deliberate spec change.
_TRUNCATION_MARKER: str = "...[truncated]"


def format_log_line(
    *,
    timestamp_utc: datetime,
    level: str,
    message: str,
    host: str | None = None,
    equipment_id: str | None = None,
    project_short_id: str | None = None,
    run_kind: str | None = None,
    run_id: str | None = None,
) -> str:
    """Render a structured log line per Backend Spec §11.5 / §16.4.

    Tags are omitted entirely when their argument is ``None``. The ``level``
    field is left-padded to 5 characters so columns line up across
    ``INFO`` / ``WARN`` / ``DEBUG`` / ``ERROR`` lines. The timestamp is
    rendered in UTC with a trailing ``Z`` (e.g. ``2026-04-17T14:32:00Z``)
    matching the example shapes in §11.5.

    Lines whose UTF-8 length exceeds :data:`LOG_LINE_MAX_BYTES` are
    truncated by trimming the message tail and appending the literal
    ``...[truncated]`` marker. The truncation budget is computed against
    the full line length (timestamp + level + tags + message), so the
    marker is guaranteed to fit. Messages that are already short enough
    are returned untouched.

    Returns the line WITHOUT a trailing newline; the writer adds the
    newline at append time.
    """
    timestamp = _format_utc_timestamp(timestamp_utc)
    level_padded = f"{level:<5}"
    tags = _render_tags(
        host=host,
        equipment_id=equipment_id,
        project_short_id=project_short_id,
        run_kind=run_kind,
        run_id=run_id,
    )
    prefix = f"{timestamp} [{level_padded}] {tags} " if tags else f"{timestamp} [{level_padded}] "
    line = f"{prefix}{message}"
    return _truncate_if_needed(line, prefix=prefix)


def _format_utc_timestamp(dt: datetime) -> str:
    """Render ``dt`` as UTC ISO 8601 with a trailing ``Z``.

    Naive datetimes are interpreted as already-UTC; aware datetimes are
    converted to UTC. The trailing ``Z`` is preferred over ``+00:00``
    because the example log lines in §11.5 use that form, and parsers
    downstream of the spec rely on it. Output is zero-padded so ``April``
    renders as ``04`` and so on.
    """
    if dt.tzinfo is not None:
        # Convert to UTC then drop the offset for a clean Z suffix.
        from datetime import UTC

        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _render_tags(
    *,
    host: str | None,
    equipment_id: str | None,
    project_short_id: str | None,
    run_kind: str | None,
    run_id: str | None,
) -> str:
    """Render the supplied context as ``[host:..] [equip:..] ...``.

    Returns the empty string when every argument is ``None`` so the caller
    can emit a tagless line cleanly.
    """
    values: dict[str, str | None] = {
        "host": host,
        "equipment_id": equipment_id,
        "project_short_id": project_short_id,
        "run_kind": run_kind,
        "run_id": run_id,
    }
    parts: list[str] = []
    for key, prefix in _TAG_ORDER:
        value = values[key]
        if value is not None:
            parts.append(f"[{prefix}:{value}]")
    return " ".join(parts)


def _truncate_if_needed(line: str, *, prefix: str) -> str:
    """Trim ``line`` so its UTF-8 byte length is at most ``LOG_LINE_MAX_BYTES``.

    Truncation strips bytes from the end of the message body and appends
    the literal ``...[truncated]`` marker. The marker is guaranteed to fit
    by reserving its byte length up front. If even the prefix + marker
    alone exceeds the cap (a pathological case for callers that pass
    enormous tag values), the function still returns a best-effort
    truncated line rather than raising.
    """
    encoded = line.encode("utf-8")
    if len(encoded) <= LOG_LINE_MAX_BYTES:
        return line
    marker_bytes = _TRUNCATION_MARKER.encode("utf-8")
    # Reserve room for the marker; the message body is shrunk to fit.
    budget = LOG_LINE_MAX_BYTES - len(marker_bytes)
    if budget <= 0:
        # Pathological case: even the marker alone exceeds the cap. Return
        # just the marker so the line is at least self-describing.
        return _TRUNCATION_MARKER
    prefix_bytes = prefix.encode("utf-8")
    if len(prefix_bytes) >= budget:
        # Prefix alone exhausts the budget. Emit prefix-truncated form so
        # downstream tools still see the marker and know to expect a tail
        # being missing.
        truncated_prefix = prefix_bytes[:budget].decode("utf-8", errors="ignore")
        return f"{truncated_prefix}{_TRUNCATION_MARKER}"
    # Normal path: keep the full prefix, trim the message tail to fit.
    body_budget = budget - len(prefix_bytes)
    body_bytes = encoded[len(prefix_bytes) : len(prefix_bytes) + body_budget]
    body = body_bytes.decode("utf-8", errors="ignore")
    return f"{prefix}{body}{_TRUNCATION_MARKER}"


# ---------------------------------------------------------------------------
# Append
# ---------------------------------------------------------------------------


def append_log_line(path: Path, line: str) -> None:
    """Append a single ``line`` to a ``wizard.<hostname>.log`` file.

    Creates the parent ``.exlab-wizard`` directory if missing (the cache
    directory is allowed to not yet exist on first equipment-folder
    initialization). The file is opened in text-append mode with line
    buffering so each call ends up as one ``write()`` syscall.

    Concurrency: the file is opened with ``mode="a"``. On POSIX this maps
    to ``O_APPEND``, which makes a single ``write()`` of bytes ≤
    ``PIPE_BUF`` (4096 on Linux) atomic against concurrent appenders.
    Lines longer than this cap are truncated upstream by
    :func:`format_log_line` to ``LOG_LINE_MAX_BYTES`` (1024) which is
    well under ``PIPE_BUF``. On Windows ``mode="a"`` opens with
    ``FILE_APPEND_DATA``; the OS serializes the actual append at the
    syscall boundary. See Backend §4.5 same-equipment concurrency rule.

    The line is appended verbatim; this function adds a single trailing
    newline. Callers SHOULD pass a line already truncated to
    :data:`LOG_LINE_MAX_BYTES` via :func:`format_log_line`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Open and close per-call. The persistent-handle optimization belongs
    # in the high-level handler (§16.2.4); this function is the
    # one-shot writer used by tests, fallback paths, and the default
    # handler when no equipment context is set.
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.write("\n")
