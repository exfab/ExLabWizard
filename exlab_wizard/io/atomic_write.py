"""Atomic file writes via the §4.4.5 temp-file + ``os.replace`` recipe.

This is the single canonical implementation referenced by every cache
writer, the LIMS catalogue / keyring store, the tray server-state file,
the readme generator, and the config loader.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

__all__ = ["atomic_write_bytes"]


_TMP_SUFFIX = ".tmp"


def atomic_write_bytes(path: Path, data: bytes, *, fsync: bool = True) -> None:
    """Write ``data`` to ``path`` atomically.

    Implementation follows the §4.4.5 recipe: write to a sibling temp
    file in the same directory, ``fsync`` so the bytes are durable on
    disk, then ``os.replace`` for the atomic rename. Same-directory
    placement guarantees the rename is a single inode-table update on
    every supported filesystem (POSIX rename(2); Windows MoveFileEx with
    ``MOVEFILE_REPLACE_EXISTING``).

    The temp file uses a unique ``mkstemp``-generated name with a
    ``.tmp`` suffix so that audit tools walking the cache directory can
    recognize the transient artifact if the process crashes mid-write.
    On any exception during write or replace, the temp file is unlinked
    so it does not accumulate; the original error is re-raised.

    ``fsync=False`` skips the durability call -- only useful in tests
    that prioritize speed over crash-safety.
    """
    parent = path.parent
    fd, tmp_name = tempfile.mkstemp(
        dir=str(parent),
        prefix=path.name + ".",
        suffix=_TMP_SUFFIX,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            if fsync:
                os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
