"""Unit tests for the startup update runner. Design Spec §15.6 / §15.8 item 3."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from exlab_wizard.update_check.runner import UpdateChecker


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _fetcher(
    result: tuple[str, str] | None,
) -> Callable[[], Awaitable[tuple[str, str] | None]]:
    async def _fetch() -> tuple[str, str] | None:
        return result

    return _fetch


def test_newer_tag_fires_notification() -> None:
    recorder = _Recorder()
    checker = UpdateChecker(
        current_version="0.1.0",
        notifier=recorder,
        fetcher=_fetcher(("v0.2.0", "https://example/releases/v0.2.0")),
    )
    asyncio.run(checker._check())

    assert len(recorder.calls) == 1
    assert "v0.2.0" in recorder.calls[0]["message"]
    assert checker.latest_tag == "v0.2.0"
    assert checker.latest_url == "https://example/releases/v0.2.0"


def test_equal_or_older_tag_does_not_notify() -> None:
    recorder = _Recorder()
    checker = UpdateChecker(
        current_version="0.2.0",
        notifier=recorder,
        fetcher=_fetcher(("v0.2.0", "https://example/releases/v0.2.0")),
    )
    asyncio.run(checker._check())

    assert recorder.calls == []
    # The probe still records what it discovered, even when not newer.
    assert checker.latest_tag == "v0.2.0"


def test_none_result_does_not_notify_or_raise() -> None:
    recorder = _Recorder()
    checker = UpdateChecker(
        current_version="0.1.0",
        notifier=recorder,
        fetcher=_fetcher(None),
    )
    asyncio.run(checker._check())

    assert recorder.calls == []
    assert checker.latest_tag is None


def test_start_runs_check_and_stop_joins() -> None:
    recorder = _Recorder()
    checker = UpdateChecker(
        current_version="0.1.0",
        notifier=recorder,
        fetcher=_fetcher(("v9.9.9", "https://example/releases/v9.9.9")),
    )
    checker.start()
    checker.stop()  # bounded join; the thread is one-shot

    assert len(recorder.calls) == 1
    assert "v9.9.9" in recorder.calls[0]["message"]
