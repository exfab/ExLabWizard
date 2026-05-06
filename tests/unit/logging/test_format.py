"""Tests for ``exlab_wizard.logging.format``.

Backend Spec §16.4 defines the canonical log line shape; §16.10 defines
the secret-redaction contract. These tests pin both -- the formatter
output is what downstream tooling (the Detail-pane log viewer, log
aggregation scripts) parses, and a regression there silently breaks the
audit trail.
"""

from __future__ import annotations

import logging

import pytest

from exlab_wizard.logging.context import clear_run_context, set_run_context
from exlab_wizard.logging.format import StructuredTagFormatter, redact_secret


@pytest.fixture(autouse=True)
def _reset_context() -> None:
    """Ensure each test starts with no run context bleed-through."""
    clear_run_context()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record(level: int = logging.INFO, message: str = "hello") -> logging.LogRecord:
    """Build a minimal ``LogRecord`` at a known timestamp.

    The timestamp is set to ``2026-04-17T14:32:00Z`` (the example in
    §11.5) so the formatter's date string is asserted byte-for-byte.
    """
    record = logging.LogRecord(
        name="test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )
    # 2026-04-17T14:32:00Z in epoch seconds.
    record.created = 1776436320.0
    return record


# ---------------------------------------------------------------------------
# StructuredTagFormatter
# ---------------------------------------------------------------------------


def test_renders_all_tags_when_full_context_set() -> None:
    formatter = StructuredTagFormatter()
    with set_run_context(
        host="labpc-04",
        equipment_id="CONFOCAL_01",
        project_short_id="PROJ-0042",
        run_kind="experimental",
        run_id="Run_2026-04-17T14-32-00",
    ):
        line = formatter.format(_record(message="creation started"))
    assert line == (
        "2026-04-17T14:32:00Z [INFO ] "
        "[host:labpc-04] [equip:CONFOCAL_01] [proj:PROJ-0042] "
        "[kind:experimental] [run:Run_2026-04-17T14-32-00] "
        "creation started"
    )


def test_omits_tags_when_their_context_var_is_unset() -> None:
    formatter = StructuredTagFormatter()
    with set_run_context(host="labpc-04", equipment_id="CONFOCAL_01"):
        line = formatter.format(_record(message="probe ok"))
    assert line == ("2026-04-17T14:32:00Z [INFO ] [host:labpc-04] [equip:CONFOCAL_01] probe ok")
    assert "[proj:" not in line
    assert "[kind:" not in line
    assert "[run:" not in line


def test_emits_no_tags_when_context_is_empty() -> None:
    formatter = StructuredTagFormatter()
    line = formatter.format(_record(message="bare"))
    assert line == "2026-04-17T14:32:00Z [INFO ] bare"


def test_level_field_is_left_padded_to_five_chars() -> None:
    """Spec §16.4: level field is exactly 5 chars wide.

    §16.5 commits four canonical level names: ``DEBUG``, ``INFO``, ``WARN``,
    ``ERROR``. Stdlib's ``WARNING`` is mapped to ``WARN`` and ``CRITICAL``
    to ``ERROR`` so the column width is consistent.
    """
    formatter = StructuredTagFormatter()
    info_line = formatter.format(_record(level=logging.INFO, message="x"))
    warn_line = formatter.format(_record(level=logging.WARNING, message="x"))
    debug_line = formatter.format(_record(level=logging.DEBUG, message="x"))
    error_line = formatter.format(_record(level=logging.ERROR, message="x"))
    critical_line = formatter.format(_record(level=logging.CRITICAL, message="x"))

    assert "[INFO ]" in info_line  # 4-char name, left-padded with one space
    assert "[WARN ]" in warn_line  # WARNING -> WARN, left-padded
    assert "[DEBUG]" in debug_line
    assert "[ERROR]" in error_line
    assert "[ERROR]" in critical_line  # CRITICAL -> ERROR


def test_timestamp_is_utc_iso_8601_with_z_suffix() -> None:
    formatter = StructuredTagFormatter()
    line = formatter.format(_record())
    timestamp_str = line.split(" [")[0]
    assert timestamp_str == "2026-04-17T14:32:00Z"
    # Round-trip parse: strict ISO 8601 with the explicit Z suffix.
    assert timestamp_str.endswith("Z")
    assert "T" in timestamp_str
    assert "+" not in timestamp_str  # ensure not "+00:00" form


def test_message_with_format_args_is_rendered() -> None:
    formatter = StructuredTagFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="value=%s",
        args=("forty-two",),
        exc_info=None,
    )
    record.created = 1776436320.0
    line = formatter.format(record)
    assert "value=forty-two" in line


# ---------------------------------------------------------------------------
# redact_secret
# ---------------------------------------------------------------------------


def test_redact_url_userinfo_password() -> None:
    assert redact_secret("https://user:pw@host/path") == "https://user:***@host/path"


def test_redact_url_userinfo_password_with_punctuation() -> None:
    # Password with punctuation that doesn't include the literal ``@``
    # (which is the URL userinfo delimiter; an unencoded ``@`` would be
    # invalid in a URL anyway).
    assert redact_secret("smb://alice:p4ss-w0rd!@nas01/share") == "smb://alice:***@nas01/share"


def test_redact_bearer_token() -> None:
    assert redact_secret("Bearer abc123") == "Bearer ***"


def test_redact_bearer_token_lowercase() -> None:
    assert redact_secret("bearer abc123") == "bearer ***"


def test_redact_authorization_header() -> None:
    assert redact_secret("Authorization: abc123") == "Authorization: ***"


def test_redact_authorization_bearer_combined() -> None:
    # The Authorization branch swallows the whole right-hand side, so the
    # redacted result is a single ``***`` rather than ``Bearer ***``.
    assert redact_secret("Authorization: Bearer abc123") == "Authorization: ***"


def test_redact_leaves_non_secret_strings_alone() -> None:
    assert redact_secret("hello world") == "hello world"
    assert redact_secret("/data/lab/CONFOCAL_01/PROJ-0042") == "/data/lab/CONFOCAL_01/PROJ-0042"
    assert redact_secret("") == ""


def test_redact_handles_url_without_password() -> None:
    # No userinfo segment -> nothing to scrub.
    assert redact_secret("https://nas01/path") == "https://nas01/path"


def test_redact_handles_multiple_secrets_in_one_string() -> None:
    raw = "fetch https://u:p@host/, then send Bearer xyz"
    redacted = redact_secret(raw)
    assert "p@host" not in redacted
    assert "Bearer ***" in redacted
    assert "https://u:***@host/" in redacted


def test_redact_coerces_non_string_input() -> None:
    # A pathological caller passing a Path or None gets a best-effort
    # stringification rather than a TypeError.
    from pathlib import Path

    redacted = redact_secret(Path("/data/example"))
    assert redacted == "/data/example"
