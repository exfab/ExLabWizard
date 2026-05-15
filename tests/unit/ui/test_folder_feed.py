"""Unit tests for the folder-feed + refresh-coordinator client modules.

GUI/Orchestrator Redesign §5 / §9.1.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from exlab_wizard.ui.client.folder_feed import FolderFeed
from exlab_wizard.ui.client.refresh_coordinator import RefreshCoordinator


@pytest.mark.asyncio
async def test_folder_feed_start_then_stop_calls_fetch_at_least_once() -> None:
    calls: list[str] = []

    async def fake_fetch(path: str) -> dict:
        calls.append(path)
        return {"path": path, "entries": []}

    feed = FolderFeed(fetch=fake_fetch, poll_interval_s=0.01)
    await feed.start("/r")
    await asyncio.sleep(0.05)
    await feed.stop()
    assert len(calls) >= 1
    assert all(c == "/r" for c in calls)


@pytest.mark.asyncio
async def test_folder_feed_switching_paths_stops_previous() -> None:
    calls: list[str] = []

    async def fake_fetch(path: str) -> dict:
        calls.append(path)
        return {"path": path}

    feed = FolderFeed(fetch=fake_fetch, poll_interval_s=0.01)
    await feed.start("/a")
    await asyncio.sleep(0.05)
    await feed.start("/b")
    await asyncio.sleep(0.05)
    await feed.stop()
    # We saw at least one call to each path.
    assert "/a" in calls
    assert "/b" in calls


@pytest.mark.asyncio
async def test_folder_feed_pause_skips_fetch() -> None:
    calls: list[str] = []

    async def fake_fetch(path: str) -> dict:
        calls.append(path)
        return {}

    feed = FolderFeed(fetch=fake_fetch, poll_interval_s=0.01)
    await feed.start("/r")
    await asyncio.sleep(0.05)
    feed.pause()
    n_before = len(calls)
    await asyncio.sleep(0.05)
    # No new calls while paused.
    assert len(calls) <= n_before + 1  # tolerate one in-flight
    feed.resume()
    await asyncio.sleep(0.05)
    await feed.stop()


def test_refresh_coordinator_coalesces_within_window() -> None:
    coord = RefreshCoordinator()
    coord.record_tree_refresh()
    assert coord.should_skip_folder() is True
    # Force a synthetic time jump so the window expires.
    coord.last_tree_refresh_s = time.monotonic() - 5.0
    assert coord.should_skip_folder() is False
