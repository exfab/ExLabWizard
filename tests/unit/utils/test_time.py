"""Tests for ``exlab_wizard.utils.time``."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone

import pytest

from exlab_wizard.utils.time import (
    dt_to_iso,
    parse_utc_iso,
    parse_utc_iso_or_none,
    utc_now_iso,
)

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def test_utc_now_iso_format() -> None:
    value = utc_now_iso()
    assert _ISO_RE.match(value), f"unexpected ISO format: {value!r}"


def test_dt_to_iso_naive_assumed_utc() -> None:
    dt = datetime(2026, 5, 9, 12, 34, 56)
    assert dt_to_iso(dt) == "2026-05-09T12:34:56Z"


def test_dt_to_iso_utc_aware() -> None:
    dt = datetime(2026, 5, 9, 12, 34, 56, tzinfo=UTC)
    assert dt_to_iso(dt) == "2026-05-09T12:34:56Z"


def test_dt_to_iso_non_utc_zone_converts() -> None:
    eastern = timezone(timedelta(hours=-5))
    dt = datetime(2026, 5, 9, 7, 34, 56, tzinfo=eastern)
    # 07:34 in UTC-5 == 12:34 in UTC.
    assert dt_to_iso(dt) == "2026-05-09T12:34:56Z"


def test_parse_utc_iso_z_form() -> None:
    parsed = parse_utc_iso("2026-05-09T12:34:56Z")
    assert parsed == datetime(2026, 5, 9, 12, 34, 56, tzinfo=UTC)


def test_parse_utc_iso_offset_form() -> None:
    parsed = parse_utc_iso("2026-05-09T12:34:56+00:00")
    assert parsed == datetime(2026, 5, 9, 12, 34, 56, tzinfo=UTC)


def test_parse_utc_iso_naive_input_tagged_utc() -> None:
    parsed = parse_utc_iso("2026-05-09T12:34:56")
    assert parsed == datetime(2026, 5, 9, 12, 34, 56, tzinfo=UTC)


def test_parse_utc_iso_round_trip_with_now() -> None:
    formatted = utc_now_iso()
    parsed = parse_utc_iso(formatted)
    assert dt_to_iso(parsed) == formatted


def test_parse_utc_iso_raises_on_garbage() -> None:
    with pytest.raises(ValueError):
        parse_utc_iso("not-a-date")


def test_parse_utc_iso_or_none_handles_none() -> None:
    assert parse_utc_iso_or_none(None) is None


def test_parse_utc_iso_or_none_handles_empty() -> None:
    assert parse_utc_iso_or_none("") is None


def test_parse_utc_iso_or_none_handles_malformed() -> None:
    assert parse_utc_iso_or_none("garbage") is None


def test_parse_utc_iso_or_none_returns_datetime_for_valid() -> None:
    parsed = parse_utc_iso_or_none("2026-05-09T12:34:56Z")
    assert parsed == datetime(2026, 5, 9, 12, 34, 56, tzinfo=UTC)
