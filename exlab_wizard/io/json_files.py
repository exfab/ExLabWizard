"""msgspec JSON readers with optional schema-major gating.

Centralizes the ``path.read_bytes()`` + ``msgspec.json.decode(...)``
pattern that previously appeared in ten cache/validator/api/orchestrator
modules, plus the §11.9.2 "reader must reject a different major" check.
``require_schema_major`` is the canonical schema-major gate; it lives
here (rather than in ``cache/equipment.py`` where it was first written)
so every cache reader -- including ones outside the ``cache`` package
-- can share it without an upward import.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import msgspec

from exlab_wizard.errors import SchemaMajorMismatchError

__all__ = [
    "read_msgspec_json",
    "read_msgspec_json_raw",
    "require_schema_major",
]


def require_schema_major(version: str | None, *, expected_major: int) -> None:
    """Raise ``SchemaMajorMismatchError`` when ``version`` is a different major.

    Backend Spec §11.9.2 rule 3: a reader at major ``R`` MUST refuse any
    file at major ``M != R`` with a structured error. The error carries
    ``expected_major`` and ``found`` so the caller can report it via the
    §4.6.3 error envelope.

    A ``None``, empty, or non-``MAJOR.MINOR`` version string is treated
    as a major mismatch (the reader cannot tell what version the file
    claims to be). Callers that want to be lenient about a missing
    version (e.g. a partially-written or corrupt registry file that
    should be recovered by rewriting) check that condition themselves
    before invoking this helper.
    """
    if version is None or not version:
        raise SchemaMajorMismatchError(expected_major=expected_major, found=str(version))
    try:
        found_major = int(version.split(".", 1)[0])
    except ValueError as exc:
        raise SchemaMajorMismatchError(expected_major=expected_major, found=version) from exc
    if found_major != expected_major:
        raise SchemaMajorMismatchError(expected_major=expected_major, found=version)


def read_msgspec_json[T](
    path: Path,
    type_: type[T],
    *,
    expected_major: int | None = None,
) -> T:
    """Read ``path`` and decode it as ``type_``, optionally gating on major.

    When ``expected_major`` is supplied, peeks ``schema_version`` from
    the bytes first and raises :class:`SchemaMajorMismatchError` on a
    different major before attempting the typed decode. A malformed
    JSON file or one without ``schema_version`` is passed through to
    the typed decoder, which surfaces the precise validation error.
    """
    data = path.read_bytes()
    if expected_major is not None:
        _peek_and_check_major(data, expected_major=expected_major)
    return msgspec.json.decode(data, type=type_)


def read_msgspec_json_raw(
    path: Path,
    *,
    expected_major: int | None = None,
) -> dict[str, Any]:
    """Two-pass read returning the raw dict for migration-default patching.

    Some readers need to inspect or mutate the decoded dict (e.g. to
    fill in default fields added in a schema-minor bump) before passing
    it to ``msgspec.convert``. Use this helper for those cases; for
    plain typed reads call :func:`read_msgspec_json` instead.
    """
    data = path.read_bytes()
    raw: dict[str, Any] = msgspec.json.decode(data, type=dict)
    if expected_major is not None:
        version = str(raw.get("schema_version", ""))
        if version:
            require_schema_major(version, expected_major=expected_major)
    return raw


def _peek_and_check_major(data: bytes, *, expected_major: int) -> None:
    try:
        head = msgspec.json.decode(data, type=dict)
    except (msgspec.DecodeError, msgspec.ValidationError):
        return
    version = str(head.get("schema_version", ""))
    if not version:
        return
    require_schema_major(version, expected_major=expected_major)
