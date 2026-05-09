"""Filesystem I/O helpers shared across the codebase.

Backend Spec §4.4 (file IO discipline) and §11.9 (schema-version policy).
The package is a leaf in the import graph: it depends only on stdlib,
``constants``, and ``errors``. Higher-level modules (``cache``, ``api``,
``validator``, ``orchestrator``) import from here, never the reverse.
"""

from __future__ import annotations

from exlab_wizard.io.atomic_write import atomic_write_bytes
from exlab_wizard.io.json_files import (
    read_msgspec_json,
    read_msgspec_json_raw,
    require_schema_major,
)
from exlab_wizard.io.yaml_files import load_yaml_manifest

__all__ = [
    "atomic_write_bytes",
    "load_yaml_manifest",
    "read_msgspec_json",
    "read_msgspec_json_raw",
    "require_schema_major",
]
