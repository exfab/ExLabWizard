"""Cache package. Backend Spec §11."""

from __future__ import annotations

from pathlib import Path
from typing import Final

__all__ = ["lock_path_for"]


_LOCK_SUFFIX: Final[str] = ".lock"


def lock_path_for(path: Path) -> str:
    """Return the advisory file-lock path string for ``path``.

    Centralizes the ``str(path) + ".lock"`` convention used by every
    cache writer so the suffix lives in exactly one place.
    """
    return str(path) + _LOCK_SUFFIX
