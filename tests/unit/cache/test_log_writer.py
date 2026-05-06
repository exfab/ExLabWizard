"""Unit tests for ``exlab_wizard.cache.log_writer``.

The line shape is committed by Backend Spec §11.5; the truncation cap and
concurrency rules are committed by §4.5. These tests pin both surfaces so
any refactor of the formatter or the file-append path keeps the contract.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timezone
from pathlib import Path

from exlab_wizard.cache.log_writer import append_log_line, format_log_line
from exlab_wizard.constants import LOG_LINE_MAX_BYTES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A canonical timestamp matching the §11.5 example log lines. Reused across
# the format-line tests so the rendered output is deterministic.
_CANONICAL_TS = datetime(2026, 4, 17, 14, 31, 55, tzinfo=UTC)


# ---------------------------------------------------------------------------
# format_log_line
# ---------------------------------------------------------------------------


def test_format_log_line_with_all_tags_matches_spec_example() -> None:
    """The rendered line must match the §11.5 example shape verbatim."""
    line = format_log_line(
        timestamp_utc=_CANONICAL_TS,
        level="INFO",
        message="Creation started: new_run on CONFOCAL_01/PROJ-0042",
        host="labpc-04",
        equipment_id="CONFOCAL_01",
        project_short_id="PROJ-0042",
        run_kind="experimental",
    )
    expected = (
        "2026-04-17T14:31:55Z [INFO ] [host:labpc-04] [equip:CONFOCAL_01] "
        "[proj:PROJ-0042] [kind:experimental] "
        "Creation started: new_run on CONFOCAL_01/PROJ-0042"
    )
    assert line == expected


def test_format_log_line_with_run_id_includes_run_tag() -> None:
    """When ``run_id`` is set the ``[run:..]`` tag appears after ``[kind:..]``."""
    line = format_log_line(
        timestamp_utc=_CANONICAL_TS,
        level="INFO",
        message="hello",
        host="labpc-04",
        equipment_id="CONFOCAL_01",
        project_short_id="PROJ-0042",
        run_kind="experimental",
        run_id="Run_2026-04-17T14-32-00",
    )
    assert (
        "[host:labpc-04] [equip:CONFOCAL_01] [proj:PROJ-0042] "
        "[kind:experimental] [run:Run_2026-04-17T14-32-00]" in line
    )


def test_format_log_line_omits_unset_tags() -> None:
    """Tags whose argument is ``None`` are absent from the output entirely."""
    line = format_log_line(
        timestamp_utc=_CANONICAL_TS,
        level="INFO",
        message="bare message",
    )
    # No bracket-prefixed tag is present; only the timestamp + level + message.
    assert "[host:" not in line
    assert "[equip:" not in line
    assert "[proj:" not in line
    assert "[kind:" not in line
    assert "[run:" not in line
    assert line == "2026-04-17T14:31:55Z [INFO ] bare message"


def test_format_log_line_partial_tags_skipped_individually() -> None:
    """Only the tags whose values are supplied appear; others are skipped."""
    line = format_log_line(
        timestamp_utc=_CANONICAL_TS,
        level="WARN",
        message="partial",
        host="labpc-04",
        equipment_id="CONFOCAL_01",
    )
    assert "[host:labpc-04]" in line
    assert "[equip:CONFOCAL_01]" in line
    assert "[proj:" not in line
    assert "[kind:" not in line
    assert "[run:" not in line


def test_format_log_line_level_is_padded_to_five_chars() -> None:
    """Levels shorter than 5 chars are padded with trailing spaces."""
    info_line = format_log_line(timestamp_utc=_CANONICAL_TS, level="INFO", message="m")
    warn_line = format_log_line(timestamp_utc=_CANONICAL_TS, level="WARN", message="m")
    debug_line = format_log_line(timestamp_utc=_CANONICAL_TS, level="DEBUG", message="m")
    error_line = format_log_line(timestamp_utc=_CANONICAL_TS, level="ERROR", message="m")
    # All four levels render with a 5-char wide bracketed field.
    assert "[INFO ]" in info_line
    assert "[WARN ]" in warn_line
    assert "[DEBUG]" in debug_line
    assert "[ERROR]" in error_line


def test_format_log_line_timestamp_is_zero_padded_iso_with_z() -> None:
    """Single-digit fields render as ``04`` etc. and end in ``Z``."""
    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    line = format_log_line(timestamp_utc=when, level="INFO", message="m")
    # Strict equality on the timestamp portion.
    assert line.startswith("2026-01-02T03:04:05Z ")


def test_format_log_line_naive_datetime_treated_as_utc() -> None:
    """A naive datetime is assumed already-UTC and rendered with ``Z``."""
    naive = datetime(2026, 4, 17, 14, 31, 55)  # no tzinfo
    line = format_log_line(timestamp_utc=naive, level="INFO", message="m")
    assert line.startswith("2026-04-17T14:31:55Z ")


def test_format_log_line_aware_non_utc_datetime_converts_to_utc() -> None:
    """Aware datetimes in non-UTC zones are converted to UTC before rendering."""
    eastern = timezone.utcoffset.__self__ if False else None  # noqa: F841
    from datetime import timedelta

    plus_two = timezone(timedelta(hours=2))
    when = datetime(2026, 4, 17, 16, 31, 55, tzinfo=plus_two)
    # 16:31:55 +02:00 == 14:31:55 UTC.
    line = format_log_line(timestamp_utc=when, level="INFO", message="m")
    assert line.startswith("2026-04-17T14:31:55Z ")


def test_format_log_line_short_message_unchanged() -> None:
    """Lines well under the cap are returned without a truncation marker."""
    line = format_log_line(
        timestamp_utc=_CANONICAL_TS,
        level="INFO",
        message="ok",
    )
    assert "...[truncated]" not in line


def test_format_log_line_truncates_long_messages_with_marker() -> None:
    """A message that pushes the line past LOG_LINE_MAX_BYTES is truncated."""
    big_message = "x" * (LOG_LINE_MAX_BYTES * 2)
    line = format_log_line(
        timestamp_utc=_CANONICAL_TS,
        level="INFO",
        message=big_message,
    )
    assert line.endswith("...[truncated]")
    encoded = line.encode("utf-8")
    assert len(encoded) <= LOG_LINE_MAX_BYTES


def test_format_log_line_truncation_preserves_prefix_and_tags() -> None:
    """Truncation trims the message body, never the prefix or tags."""
    line = format_log_line(
        timestamp_utc=_CANONICAL_TS,
        level="ERROR",
        message="x" * (LOG_LINE_MAX_BYTES * 2),
        host="labpc-04",
        equipment_id="CONFOCAL_01",
    )
    # Prefix + tags survive truncation; the body is what gets shrunk.
    assert line.startswith("2026-04-17T14:31:55Z [ERROR] [host:labpc-04] [equip:CONFOCAL_01] ")
    assert line.endswith("...[truncated]")


def test_format_log_line_truncation_at_exact_boundary() -> None:
    """A line whose UTF-8 length equals LOG_LINE_MAX_BYTES is NOT truncated."""
    # Compute the message that brings the line exactly to the cap.
    base = format_log_line(timestamp_utc=_CANONICAL_TS, level="INFO", message="")
    base_bytes = len(base.encode("utf-8"))
    fill = LOG_LINE_MAX_BYTES - base_bytes
    line = format_log_line(
        timestamp_utc=_CANONICAL_TS,
        level="INFO",
        message="x" * fill,
    )
    assert "...[truncated]" not in line
    assert len(line.encode("utf-8")) == LOG_LINE_MAX_BYTES


# ---------------------------------------------------------------------------
# append_log_line
# ---------------------------------------------------------------------------


def test_append_log_line_creates_parent_cache_dir(tmp_path: Path) -> None:
    """Parent ``.exlab-wizard`` directory is created if missing."""
    log_path = tmp_path / "CONFOCAL_01" / ".exlab-wizard" / "wizard.host.log"
    assert not log_path.parent.exists()
    append_log_line(log_path, "first line")
    assert log_path.parent.is_dir()
    assert log_path.is_file()


def test_append_log_line_writes_single_line_with_newline(tmp_path: Path) -> None:
    log_path = tmp_path / "wizard.host.log"
    append_log_line(log_path, "hello")
    content = log_path.read_text(encoding="utf-8")
    assert content == "hello\n"


def test_append_log_line_appends_to_existing_file(tmp_path: Path) -> None:
    """Multi-write: each call appends after prior content."""
    log_path = tmp_path / "wizard.host.log"
    append_log_line(log_path, "line one")
    append_log_line(log_path, "line two")
    append_log_line(log_path, "line three")
    content = log_path.read_text(encoding="utf-8")
    assert content == "line one\nline two\nline three\n"


def test_append_log_line_concurrent_writes_do_not_interleave(tmp_path: Path) -> None:
    """N concurrent threads each writing one short line must produce N
    intact lines in the file, with no torn / interleaved bytes.

    Backend Spec §4.5 commits to ``O_APPEND``-atomic writes on POSIX for
    lines ≤ ``PIPE_BUF``. ``LOG_LINE_MAX_BYTES`` (1024) is well under
    Linux's ``PIPE_BUF`` (4096), so each ``write()`` is a single atomic
    syscall.
    """
    log_path = tmp_path / "wizard.host.log"
    n = 20
    # Distinct payloads so we can assert each made it through intact.
    payloads = [f"thread-{i:02d}-PAYLOAD-{'x' * 16}" for i in range(n)]

    def _emit(payload: str) -> None:
        append_log_line(log_path, payload)

    with ThreadPoolExecutor(max_workers=n) as executor:
        futures = [executor.submit(_emit, p) for p in payloads]
        for fut in as_completed(futures):
            fut.result()

    content = log_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    # Exactly N lines, each line equal to one of the payloads. Order is
    # not guaranteed under concurrency, but the SET must match.
    assert len(lines) == n
    assert sorted(lines) == sorted(payloads)


def test_append_log_line_idempotent_directory_creation(tmp_path: Path) -> None:
    """Calling append twice does not fail even though the dir already exists."""
    log_path = tmp_path / ".exlab-wizard" / "wizard.host.log"
    append_log_line(log_path, "a")
    append_log_line(log_path, "b")
    assert log_path.read_text(encoding="utf-8") == "a\nb\n"
