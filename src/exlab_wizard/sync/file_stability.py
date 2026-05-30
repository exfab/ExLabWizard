"""Pre-rclone file-stability guard: confirm files have stopped growing.

Polls ``st_size`` on a fixed interval and reports whether a file's size has
been identical across N consecutive observations. Used as a pre-flight check
before ``rclone`` sync invocations so only complete files transfer.

The module is intentionally stdlib-only and free of project imports so it can
be reused unchanged by external job scripts / a CLI as well as by the
in-process sync worker.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_log = logging.getLogger(__name__)

__all__ = ["is_stable", "wait_until_stable"]


def _poll_size(path: Path) -> int | None:
    """Return ``path``'s current size, or ``None`` if it cannot be stat-ed."""
    try:
        return path.stat().st_size
    except OSError:
        return None


def is_stable(path: Path, interval: float, checks: int, timeout: float) -> bool:
    """Return True once ``path``'s size is constant across ``checks`` polls.

    Args:
        path: File to observe (``stat()`` follows symlinks).
        interval: Seconds between polls. Must be > 0.
        checks: Consecutive equal observations required. Must be >= 2.
        timeout: Wall-clock budget in seconds; exceeding it returns False.

    Returns:
        True if the size was identical across ``checks`` consecutive polls
        (an all-zero-size file counts as stable). False if the file
        disappears at any poll or the timeout elapses first.

    Raises:
        ValueError: If ``interval <= 0`` or ``checks < 2``.

    Notes:
        On NFS mounts set ``interval >= actimeo`` (typically >= 30 s) so the
        attribute cache does not mask a still-growing file. On Windows an
        exclusively-locked file may report ``size == 0`` via ``stat()``;
        account for that at the call site. No busy-wait: ``time.sleep`` is
        used between polls. Thread-safe: no shared mutable state.
    """
    if interval <= 0:
        raise ValueError("interval must be > 0")
    if checks < 2:
        raise ValueError("checks must be >= 2")

    deadline = time.monotonic() + timeout
    last = _poll_size(path)
    if last is None:
        _log.debug("unstable (missing): %s", path)
        return False

    stable_count = 1
    while stable_count < checks:
        if time.monotonic() >= deadline:
            _log.debug("unstable (timeout): %s", path)
            return False
        time.sleep(interval)
        size = _poll_size(path)
        if size is None:
            _log.debug("unstable (vanished): %s", path)
            return False
        if size == last:
            stable_count += 1
        else:
            stable_count = 1
            last = size
    _log.debug("stable: %s (size=%d)", path, last)
    return True


def wait_until_stable(
    paths: Iterable[Path],
    interval: float,
    checks: int,
    timeout: float,
    max_workers: int = 8,
) -> tuple[list[Path], list[Path]]:
    """Poll ``paths`` concurrently; return ``(stable, unstable)``.

    Args:
        paths: Files to observe.
        interval: Seconds between polls (forwarded to :func:`is_stable`).
        checks: Consecutive equal observations required.
        timeout: Per-file wall-clock budget in seconds.
        max_workers: Thread cap (one thread per file, bounded) to avoid NFS
            overload. Defaults to 8.

    Returns:
        Two lists: files that reached stability, and files that did not
        (missing, still growing, or timed out).

    Raises:
        ValueError: If ``interval <= 0`` or ``checks < 2`` (validated per file
            by :func:`is_stable`).
    """
    if interval <= 0:
        raise ValueError("interval must be > 0")
    if checks < 2:
        raise ValueError("checks must be >= 2")
    items = list(paths)
    if not items:
        return [], []
    workers = max(1, min(max_workers, len(items)))  # never more threads than files
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(
            pool.map(lambda p: (p, is_stable(p, interval, checks, timeout)), items)
        )
    stable = [p for p, ok in results if ok]
    unstable = [p for p, ok in results if not ok]
    _log.debug("wait_until_stable: %d stable, %d unstable", len(stable), len(unstable))
    return stable, unstable
