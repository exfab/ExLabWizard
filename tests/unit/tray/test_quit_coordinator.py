"""Tests for :mod:`exlab_wizard.tray.quit_coordinator`. Backend Spec §4.3.2."""

from __future__ import annotations

from typing import Any

import pytest

from exlab_wizard.tray.quit_coordinator import (
    DEFAULT_TIMEOUT_SECONDS,
    SIGTERM_TIMEOUT_SECONDS,
    QuitCoordinator,
    _safe_count,
)


class _StubServer:
    def __init__(self) -> None:
        self.stop_called = False

    def stop(self) -> None:
        self.stop_called = True


class _StubWindow:
    def __init__(self) -> None:
        self.close_called = False

    def close(self) -> None:
        self.close_called = True


class _Counted:
    def __init__(self, **counts: int) -> None:
        self.__dict__.update(counts)


def _make_coordinator(
    *,
    sessions: int = 0,
    in_flight: int = 0,
    on_force_quit_prompt: Any = None,
    timeout: float = 0.0,
    sigterm_timeout: float = 0.0,
) -> tuple[QuitCoordinator, _StubServer, _StubWindow]:
    server = _StubServer()
    window = _StubWindow()
    session_store = _Counted(active_sessions=sessions, input_required=0)
    nas_sync = _Counted(in_flight_jobs=in_flight, queue_depth=0)
    coord = QuitCoordinator(
        server_runner=server,  # type: ignore[arg-type]
        window_launcher=window,  # type: ignore[arg-type]
        session_store=session_store,
        nas_sync=nas_sync,
        on_force_quit_prompt=on_force_quit_prompt,
        timeout_seconds=timeout,
        sigterm_timeout_seconds=sigterm_timeout,
        poll_interval_seconds=0.01,
    )
    return coord, server, window


@pytest.mark.asyncio
async def test_idle_predicate_short_circuits() -> None:
    coord, server, window = _make_coordinator()
    await coord.quit()
    assert server.stop_called
    assert window.close_called


@pytest.mark.asyncio
async def test_busy_predicate_times_out_and_force_quits() -> None:
    prompts: list[bool] = []

    def _prompt() -> bool:
        prompts.append(True)
        return True  # operator chooses Force quit

    coord, server, _window = _make_coordinator(
        sessions=1,
        in_flight=2,
        on_force_quit_prompt=_prompt,
        timeout=0.05,
    )
    await coord.quit()
    assert prompts == [True]
    assert server.stop_called


@pytest.mark.asyncio
async def test_busy_predicate_wait_resets_timer() -> None:
    calls = {"count": 0}

    def _prompt() -> bool:
        calls["count"] += 1
        return False  # operator chooses Wait

    coord, server, _window = _make_coordinator(
        sessions=1,
        in_flight=0,
        on_force_quit_prompt=_prompt,
        timeout=0.02,
    )
    await coord.quit()
    # Prompt was invoked once, then we waited again, then force-quit fallback.
    assert calls["count"] == 1
    assert server.stop_called


@pytest.mark.asyncio
async def test_sigterm_uses_short_timeout() -> None:
    seen_timeouts: list[float] = []

    def _prompt() -> bool:
        return True

    coord, _server, _window = _make_coordinator(
        sessions=1,
        on_force_quit_prompt=_prompt,
        timeout=10.0,
        sigterm_timeout=0.01,
    )

    original = coord._wait_for_idle

    async def _patched(deadline_seconds: float) -> bool:
        seen_timeouts.append(deadline_seconds)
        return await original(0.01)

    coord._wait_for_idle = _patched  # type: ignore[method-assign]
    await coord.quit(sigterm=True)
    # First call uses the SIGTERM timeout; the Wait branch retries with same.
    assert seen_timeouts[0] == 0.01


@pytest.mark.asyncio
async def test_quit_handles_missing_window_launcher() -> None:
    server = _StubServer()
    coord = QuitCoordinator(
        server_runner=server,  # type: ignore[arg-type]
        window_launcher=None,
        session_store=_Counted(active_sessions=0, input_required=0),
        nas_sync=_Counted(in_flight_jobs=0, queue_depth=0),
        timeout_seconds=0.0,
        sigterm_timeout_seconds=0.0,
        poll_interval_seconds=0.01,
    )
    await coord.quit()
    assert server.stop_called


def test_safe_count_handles_callable_attribute() -> None:
    obj = _Counted()
    obj.value = lambda: 7  # type: ignore[attr-defined]
    assert _safe_count(obj, "value") == 7


def test_safe_count_handles_callable_that_raises() -> None:
    obj = _Counted()

    def _boom() -> int:
        raise RuntimeError

    obj.value = _boom  # type: ignore[attr-defined]
    assert _safe_count(obj, "value") == 0


def test_safe_count_handles_none_obj() -> None:
    assert _safe_count(None, "anything") == 0


def test_safe_count_handles_non_numeric() -> None:
    obj = _Counted()
    obj.value = "not-a-number"  # type: ignore[attr-defined]
    assert _safe_count(obj, "value") == 0


@pytest.mark.asyncio
async def test_force_quit_prompt_exception_defaults_to_force() -> None:
    def _prompt() -> bool:
        raise RuntimeError("boom")

    coord, server, _window = _make_coordinator(
        sessions=1,
        on_force_quit_prompt=_prompt,
        timeout=0.0,
    )
    await coord.quit()
    assert server.stop_called


def test_default_timeout_constants_match_spec() -> None:
    # Backend §4.3.2: 30 seconds normal, 5 seconds SIGTERM.
    assert DEFAULT_TIMEOUT_SECONDS == 30.0
    assert SIGTERM_TIMEOUT_SECONDS == 5.0


@pytest.mark.asyncio
async def test_idle_predicate_returns_true_immediately() -> None:
    coord, server, _window = _make_coordinator()
    # _is_idle is the contract surface; verify it directly.
    assert coord._is_idle() is True
    await coord.quit()
    assert server.stop_called


@pytest.mark.asyncio
async def test_predicate_eventually_becomes_true() -> None:
    """Wait loop returns True when the predicate flips mid-wait."""
    server = _StubServer()
    window = _StubWindow()

    class _Toggling:
        def __init__(self) -> None:
            self._calls = 0

        @property
        def active_sessions(self) -> int:
            self._calls += 1
            # First few reads -> busy, then idle.
            return 1 if self._calls < 3 else 0

    sessions = _Toggling()
    nas = _Counted(in_flight_jobs=0)
    coord = QuitCoordinator(
        server_runner=server,  # type: ignore[arg-type]
        window_launcher=window,  # type: ignore[arg-type]
        session_store=sessions,
        nas_sync=nas,
        timeout_seconds=1.0,
        poll_interval_seconds=0.005,
    )
    await coord.quit()
    assert server.stop_called
