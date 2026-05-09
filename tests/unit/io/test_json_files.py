"""Tests for ``exlab_wizard.io.json_files``."""

from __future__ import annotations

from pathlib import Path

import msgspec
import pytest

from exlab_wizard.errors import SchemaMajorMismatchError
from exlab_wizard.io.json_files import (
    read_msgspec_json,
    read_msgspec_json_raw,
    require_schema_major,
)


class _Dummy(msgspec.Struct):
    schema_version: str
    name: str


def test_require_schema_major_accepts_matching_major() -> None:
    require_schema_major("1.4", expected_major=1)


def test_require_schema_major_rejects_different_major() -> None:
    with pytest.raises(SchemaMajorMismatchError) as exc:
        require_schema_major("2.0", expected_major=1)
    assert exc.value.expected_major == 1
    assert exc.value.found == "2.0"


def test_require_schema_major_rejects_empty() -> None:
    with pytest.raises(SchemaMajorMismatchError):
        require_schema_major("", expected_major=1)


def test_require_schema_major_rejects_none() -> None:
    with pytest.raises(SchemaMajorMismatchError):
        require_schema_major(None, expected_major=1)


def test_require_schema_major_rejects_unparseable() -> None:
    with pytest.raises(SchemaMajorMismatchError):
        require_schema_major("vNext", expected_major=1)


def test_read_msgspec_json_decodes_to_struct(tmp_path: Path) -> None:
    path = tmp_path / "thing.json"
    path.write_bytes(b'{"schema_version": "1.0", "name": "alice"}')
    obj = read_msgspec_json(path, _Dummy)
    assert obj.schema_version == "1.0"
    assert obj.name == "alice"


def test_read_msgspec_json_with_matching_major(tmp_path: Path) -> None:
    path = tmp_path / "thing.json"
    path.write_bytes(b'{"schema_version": "1.7", "name": "bob"}')
    obj = read_msgspec_json(path, _Dummy, expected_major=1)
    assert obj.name == "bob"


def test_read_msgspec_json_rejects_wrong_major(tmp_path: Path) -> None:
    path = tmp_path / "thing.json"
    path.write_bytes(b'{"schema_version": "2.0", "name": "carol"}')
    with pytest.raises(SchemaMajorMismatchError):
        read_msgspec_json(path, _Dummy, expected_major=1)


def test_read_msgspec_json_passes_through_malformed(tmp_path: Path) -> None:
    """Malformed JSON should surface from msgspec, not the major-check."""
    path = tmp_path / "thing.json"
    path.write_bytes(b"not json at all")
    with pytest.raises(msgspec.DecodeError):
        read_msgspec_json(path, _Dummy, expected_major=1)


def test_read_msgspec_json_raw_returns_dict(tmp_path: Path) -> None:
    path = tmp_path / "thing.json"
    path.write_bytes(b'{"schema_version": "1.0", "name": "dan", "extra": 5}')
    raw = read_msgspec_json_raw(path)
    assert raw == {"schema_version": "1.0", "name": "dan", "extra": 5}


def test_read_msgspec_json_raw_with_matching_major(tmp_path: Path) -> None:
    path = tmp_path / "thing.json"
    path.write_bytes(b'{"schema_version": "1.0", "name": "eve"}')
    raw = read_msgspec_json_raw(path, expected_major=1)
    assert raw["name"] == "eve"


def test_read_msgspec_json_raw_rejects_wrong_major(tmp_path: Path) -> None:
    path = tmp_path / "thing.json"
    path.write_bytes(b'{"schema_version": "2.0", "name": "fern"}')
    with pytest.raises(SchemaMajorMismatchError):
        read_msgspec_json_raw(path, expected_major=1)


def test_read_msgspec_json_raw_skips_check_when_version_missing(
    tmp_path: Path,
) -> None:
    """A dict without schema_version must not be rejected (the typed
    decoder downstream surfaces the precise validation error)."""
    path = tmp_path / "thing.json"
    path.write_bytes(b'{"name": "gail"}')
    raw = read_msgspec_json_raw(path, expected_major=1)
    assert raw == {"name": "gail"}
