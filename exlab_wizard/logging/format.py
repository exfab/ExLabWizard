"""Structured log formatter and secret-redaction helpers. Backend Spec §16.4, §16.10.

This module owns the canonical ``wizard.<hostname>.log`` line shape:

::

    <UTC ISO 8601 timestamp> [<LEVEL:5>] [host:..] [equip:..] [proj:..] [kind:..] [run:..] <message>

The format string is fixed -- downstream tooling (log aggregation scripts,
the Detail-pane log viewer, the Frontend-spec recovery flows) parses this
shape. Adding a new structured tag is a deliberate spec change to §16.4 and
this module.

Two public surfaces:

- :class:`StructuredTagFormatter` -- a :class:`logging.Formatter` subclass
  that consults the per-task ``contextvars`` from ``logging/context.py`` at
  emit time and renders only the tags whose values are set.
- :func:`redact_secret` -- masks credential-bearing substrings (URL
  user:password segments, ``Bearer ...`` tokens, ``Authorization: ...``
  headers) before they reach a log line. Component authors are required by
  §16.10 to wrap any URL or auth-bearing string they log through this
  helper.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from exlab_wizard.logging.context import get_run_context
from exlab_wizard.utils.time import dt_to_iso

__all__ = [
    "StructuredTagFormatter",
    "redact_secret",
]


# ---------------------------------------------------------------------------
# StructuredTagFormatter
# ---------------------------------------------------------------------------


# The mapping from context-var key (as returned by ``get_run_context``) to the
# tag prefix that appears in the log line. Order matters -- it fixes the
# ``[host:][equip:][proj:][kind:][run:]`` ordering committed by §16.4.
_TAG_ORDER: tuple[tuple[str, str], ...] = (
    ("host", "host"),
    ("equipment_id", "equip"),
    ("project_short_id", "proj"),
    ("run_kind", "kind"),
    ("run_id", "run"),
)

# Stdlib uses ``WARNING`` and ``CRITICAL`` for level names. §16.4 commits the
# four canonical short names ``INFO``, ``WARN``, ``DEBUG``, ``ERROR`` so the
# 5-char level field aligns. We map the long forms to the short ones at format
# time; see §16.5 ("Standard Python logging levels: DEBUG, INFO, WARN, ERROR").
_LEVEL_NAME_OVERRIDES: dict[str, str] = {
    "WARNING": "WARN",
    "CRITICAL": "ERROR",
}


class StructuredTagFormatter(logging.Formatter):
    """Formatter that renders the §16.4 structured-tag log line.

    Reads the active per-task context vars at format time so a logger
    instance created at module import (with no run context) still emits
    correctly tagged lines once a ``set_run_context`` block wraps the
    actual log call.

    Output shape:

    ::

        2026-04-17T14:31:55Z [INFO ] [host:labpc-04] [equip:CONFOCAL_01] ...

    Tags are omitted entirely (no empty placeholder) when their context var
    is unset. The level field is left-padded to 5 characters so columns
    line up across ``INFO`` / ``WARN`` / ``DEBUG`` / ``ERROR`` lines.
    """

    def format(self, record: logging.LogRecord) -> str:
        timestamp = _format_utc_timestamp(record.created)
        level_name = _LEVEL_NAME_OVERRIDES.get(record.levelname, record.levelname)
        level = f"{level_name:<5}"
        message = record.getMessage()
        if record.exc_info:
            # Append exception text on a new line, matching stdlib behavior.
            message = f"{message}\n{self.formatException(record.exc_info)}"
        tags = _render_tags()
        if tags:
            return f"{timestamp} [{level}] {tags} {message}"
        return f"{timestamp} [{level}] {message}"


def _format_utc_timestamp(epoch_seconds: float) -> str:
    """Render ``epoch_seconds`` as a UTC ISO 8601 timestamp with ``Z`` suffix.

    Example output: ``2026-04-17T14:32:00Z``. The trailing ``Z`` is preferred
    over ``+00:00`` because the example log lines in §11.5 use that form, and
    parsers downstream of the spec rely on it.
    """
    dt = datetime.fromtimestamp(epoch_seconds, tz=UTC)
    return dt_to_iso(dt)


def _render_tags() -> str:
    """Render the active context as ``[host:..] [equip:..] ...``.

    Returns the empty string when no context vars are set (so the formatter
    can emit a tagless line cleanly).
    """
    snapshot = get_run_context()
    parts: list[str] = []
    for key, prefix in _TAG_ORDER:
        value = snapshot.get(key)
        if value is not None:
            parts.append(f"[{prefix}:{value}]")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# redact_secret
# ---------------------------------------------------------------------------


# URL ``://user:password@`` segment. Captures the scheme + user up to the
# colon, then the password until the ``@``. We replace the password only.
_URL_USERINFO_RE = re.compile(
    r"(?P<prefix>[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s/:@]+):(?P<password>[^\s/@]*)@",
)

# ``Bearer <token>`` -- the token runs to the next whitespace. Word-boundary
# anchored on the left so we don't mangle words ending in "bearer".
_BEARER_RE = re.compile(r"(?P<keyword>\b[Bb]earer\s+)(?P<token>\S+)")

# ``Authorization: <value>`` headers -- value runs to end-of-line or the
# next comma (in case the header is embedded in a request log). The keyword
# is matched case-insensitively to mirror HTTP header semantics.
_AUTHORIZATION_RE = re.compile(r"(?P<keyword>(?i:Authorization)\s*:\s*)(?P<value>[^\r\n,]+)")


def redact_secret(value: str) -> str:
    """Mask credential-bearing substrings inside ``value``.

    Replaces three classes of secret with literal ``***``:

    1. URL user-info password: ``https://user:pw@host`` -> ``https://user:***@host``.
    2. ``Bearer <token>`` tokens -> ``Bearer ***``.
    3. ``Authorization: <value>`` headers -> ``Authorization: ***``.

    The function is intentionally idempotent and safe to call on strings
    that contain no secrets -- it returns those unchanged. Component authors
    are required by §16.10 to wrap any URL or auth-bearing string in this
    helper before logging.

    The returned string preserves the rest of the input verbatim so log
    lines remain readable around the redacted segment.
    """
    if not isinstance(value, str):
        # Defensive: callers occasionally pass non-strings (e.g. a Path).
        # Coerce so we still scrub a stringified secret.
        value = str(value)
    redacted = _URL_USERINFO_RE.sub(r"\g<prefix>:***@", value)
    # Order matters: scrub the explicit ``Authorization:`` header first so
    # that an ``Authorization: Bearer ...`` line collapses to a single
    # ``Authorization: ***`` rather than ``Authorization: Bearer ***``.
    redacted = _AUTHORIZATION_RE.sub(r"\g<keyword>***", redacted)
    redacted = _BEARER_RE.sub(r"\g<keyword>***", redacted)
    return redacted
