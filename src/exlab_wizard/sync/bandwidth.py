"""Bandwidth schedule evaluator. Backend Spec §7.1.7.

The §7.1.7 bandwidth limiter is per-equipment and per-window: an operator
declares an ``upload_mbps`` cap and an optional list of ``schedule``
windows. The cap applies inside any active window; outside the windows
the transport runs unthrottled.

This module is the pure evaluator: given a :class:`BandwidthConfig` and a
local-time ``datetime``, return the effective bandwidth limit in KiB/s
(``--bwlimit`` units) or ``None`` for unlimited. The conversion is
``upload_mbps * 1024 / 8`` per §7.1.7; the spec uses **megabits**, rclone
expects **kibibytes/s**, and the helper rounds to the nearest integer.
"""

from __future__ import annotations

from datetime import datetime, time

from exlab_wizard.config.models import BandwidthConfig, BandwidthWindow

__all__ = [
    "DAY_NAME_TO_INDEX",
    "effective_bandwidth_limit_kibps",
    "is_window_active",
    "mbps_to_kibps",
]


# Map a §9 short day name to ``datetime.weekday()`` (0 = Monday).
DAY_NAME_TO_INDEX: dict[str, int] = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def mbps_to_kibps(upload_mbps: float) -> int:
    """Convert ``upload_mbps`` (megabits/s) into ``--bwlimit`` KiB/s.

    Per §7.1.7: ``K = upload_mbps * 1024 / 8``. Rounded to the nearest
    integer. Returns 1 KiB/s as the floor when the input is positive but
    rounds to zero so a configured limit always throttles something.
    """
    if upload_mbps <= 0:
        return 0
    raw = upload_mbps * 1024.0 / 8.0
    rounded = round(raw)
    return max(1, int(rounded))


def _parse_hhmm(value: str) -> time:
    """Parse an ``HH:MM`` string. Pydantic validated it on input;
    :func:`is_window_active` revalidates so the helper is usable on
    non-Pydantic inputs (e.g. unit tests with hand-built dicts).
    """
    return time.fromisoformat(value)


def is_window_active(window: BandwidthWindow, now_local: datetime) -> bool:
    """Return True iff ``now_local`` is inside ``window``.

    The window is defined by ``days`` (a list of three-letter day names)
    and ``from``/``to`` HH:MM strings (interpreted as workstation-local
    time). The window is half-open: ``from <= t < to``. Cross-midnight
    windows are not supported by §9 (validated at config load).
    """
    weekday_index = now_local.weekday()
    weekday_name = next(
        (name for name, idx in DAY_NAME_TO_INDEX.items() if idx == weekday_index),
        None,
    )
    if weekday_name is None or weekday_name not in window.days:
        return False
    from_t = _parse_hhmm(window.from_)
    to_t = _parse_hhmm(window.to)
    cur_t = now_local.time().replace(microsecond=0)
    return from_t <= cur_t < to_t


def effective_bandwidth_limit_kibps(
    cfg: BandwidthConfig,
    *,
    now_local: datetime,
) -> int | None:
    """Return the effective ``--bwlimit`` in KiB/s for ``now_local``.

    Decision tree per §7.1.7:

    1. If ``cfg.upload_mbps`` is ``None`` -> unlimited (``None``).
    2. Else if ``cfg.schedule`` is empty -> the cap applies always.
    3. Else if ``now_local`` falls inside any schedule window -> the cap
       applies for this transfer.
    4. Else -> unlimited (``None``).
    """
    if cfg.upload_mbps is None:
        return None
    if not cfg.schedule:
        return mbps_to_kibps(cfg.upload_mbps)
    for window in cfg.schedule:
        if is_window_active(window, now_local):
            return mbps_to_kibps(cfg.upload_mbps)
    return None
