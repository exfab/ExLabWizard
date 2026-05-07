"""Tests for ``exlab_wizard.sync.bandwidth``.

Backend Spec §7.1.7. The schedule evaluator returns ``None`` for
unlimited transfers and an integer KiB/s rate inside an active window.
"""

from __future__ import annotations

from datetime import datetime

from exlab_wizard.config.models import BandwidthConfig, BandwidthWindow
from exlab_wizard.sync.bandwidth import (
    DAY_NAME_TO_INDEX,
    effective_bandwidth_limit_kibps,
    is_window_active,
    mbps_to_kibps,
)


def _wed_at(hour: int, minute: int = 0) -> datetime:
    """A Wednesday in 2026 (weekday 2) at the requested local time."""
    return datetime(2026, 4, 15, hour, minute, 0)  # 2026-04-15 is a Wednesday


def test_mbps_to_kibps_uses_spec_formula() -> None:
    """Spec §7.1.7: ``K = upload_mbps * 1024 / 8``."""
    assert mbps_to_kibps(50) == round(50 * 1024 / 8)
    assert mbps_to_kibps(8) == 1024


def test_mbps_to_kibps_floor_for_tiny_input() -> None:
    """A tiny but positive input rounds up to 1 KiB/s, not 0."""
    assert mbps_to_kibps(0.001) == 1


def test_mbps_to_kibps_zero() -> None:
    assert mbps_to_kibps(0) == 0
    assert mbps_to_kibps(-5) == 0


def test_day_name_to_index_covers_all_days() -> None:
    assert set(DAY_NAME_TO_INDEX) == {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    assert DAY_NAME_TO_INDEX["mon"] == 0
    assert DAY_NAME_TO_INDEX["sun"] == 6


def test_is_window_active_inside_window() -> None:
    window = BandwidthWindow(days=["wed"], **{"from": "08:00"}, to="18:00")  # type: ignore[arg-type]
    assert is_window_active(window, _wed_at(10))


def test_is_window_active_outside_window() -> None:
    window = BandwidthWindow(days=["wed"], **{"from": "08:00"}, to="18:00")  # type: ignore[arg-type]
    assert not is_window_active(window, _wed_at(7))
    assert not is_window_active(window, _wed_at(18))


def test_is_window_active_wrong_weekday() -> None:
    """Weekday filtering rejects days not in ``window.days``."""
    window = BandwidthWindow(days=["mon"], **{"from": "00:00"}, to="23:00")  # type: ignore[arg-type]
    assert not is_window_active(window, _wed_at(10))


def test_effective_unlimited_when_upload_mbps_none() -> None:
    cfg = BandwidthConfig(upload_mbps=None)
    assert effective_bandwidth_limit_kibps(cfg, now_local=_wed_at(10)) is None


def test_effective_unlimited_when_outside_schedule() -> None:
    cfg = BandwidthConfig(
        upload_mbps=50,
        schedule=[BandwidthWindow(days=["wed"], **{"from": "08:00"}, to="18:00")],  # type: ignore[arg-type]
    )
    # Wednesday at 03:00 is outside the window; upload is unlimited.
    assert effective_bandwidth_limit_kibps(cfg, now_local=_wed_at(3)) is None


def test_effective_throttled_inside_schedule() -> None:
    cfg = BandwidthConfig(
        upload_mbps=50,
        schedule=[BandwidthWindow(days=["wed"], **{"from": "08:00"}, to="18:00")],  # type: ignore[arg-type]
    )
    assert effective_bandwidth_limit_kibps(cfg, now_local=_wed_at(10)) == mbps_to_kibps(50)


def test_effective_throttled_when_no_schedule() -> None:
    """A cap with no schedule applies always."""
    cfg = BandwidthConfig(upload_mbps=10, schedule=[])
    assert effective_bandwidth_limit_kibps(cfg, now_local=_wed_at(3)) == mbps_to_kibps(10)


def test_effective_throttled_in_first_matching_window() -> None:
    cfg = BandwidthConfig(
        upload_mbps=20,
        schedule=[
            BandwidthWindow(days=["mon"], **{"from": "08:00"}, to="18:00"),  # type: ignore[arg-type]
            BandwidthWindow(days=["wed"], **{"from": "10:00"}, to="14:00"),  # type: ignore[arg-type]
        ],
    )
    assert effective_bandwidth_limit_kibps(cfg, now_local=_wed_at(11)) == mbps_to_kibps(20)
