"""Integration tests for the pre-transfer file-stability gate in _drive_job."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from exlab_wizard.cache.creation_writer import CreationWriter
from exlab_wizard.cache.sync_state_writer import SyncStateWriter
from exlab_wizard.sync import nas_client as nas_client_mod
from exlab_wizard.sync.nas_client import NASSyncClient
from exlab_wizard.sync.queue import SyncJobState
from exlab_wizard.sync.transports import TransportResult
from exlab_wizard.validator.engine import Validator

# Reuse the construction helpers from the main nas_client test module.
from tests.unit.sync._helpers import local_lsjson_factory
from tests.unit.sync.test_nas_client import (
    HandleState,
    _build_config,
    _populate_run,
)


def _recording_push_factory(calls: list[list[str]]):
    """A push factory that records the --files-from rel-paths of each call."""

    async def _push(local: Path, *, bwlimit_kibps=None, files_from: Path | None = None):
        if files_from is not None:
            lines = [ln for ln in files_from.read_text().splitlines() if ln.strip()]
        else:
            lines = []
        calls.append(lines)
        return TransportResult(ok=True, returncode=0)

    return lambda _eq: _push


@pytest.fixture()
async def writer() -> CreationWriter:
    return CreationWriter(lock_timeout_seconds=10.0)


async def test_gate_defers_when_no_file_is_stable(
    tmp_path: Path, writer: CreationWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All files unstable -> job stays QUEUED with a future next_attempt_at, no push."""
    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)
    calls: list[list[str]] = []

    def _all_unstable(paths, interval, checks, timeout, max_workers=8):
        return [], list(paths)

    monkeypatch.setattr(nas_client_mod, "wait_until_stable", _all_unstable)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        sync_state_writer=SyncStateWriter(),
        push_callable_factory=_recording_push_factory(calls),
        worker_poll_interval_s=0.01,
    )
    await client.init()
    try:
        handle = await client.enqueue(run_dir, ["data.bin"])
        assert handle.state == HandleState.QUEUED
        # Give the worker several ticks to pick + defer the job.
        await asyncio.sleep(0.2)
        row = await client._queue.get_by_id(handle.job_id)
        assert row is not None
        assert row.state == SyncJobState.QUEUED
        assert row.next_attempt_at  # deferred into the future
        assert row.attempts == 0  # deferral is not a failed attempt
        assert calls == []  # rclone push never invoked
    finally:
        await client.close()


async def test_gate_narrows_transfer_to_stable_subset(
    tmp_path: Path, writer: CreationWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mixed batch transfers only the stable file via --files-from."""
    cfg = _build_config(tmp_path)
    run_dir = await _populate_run(tmp_path)
    (run_dir / "other.bin").write_bytes(b"more")
    calls: list[list[str]] = []

    def _only_data_stable(paths, interval, checks, timeout, max_workers=8):
        stable = [p for p in paths if p.name == "data.bin"]
        unstable = [p for p in paths if p.name != "data.bin"]
        return stable, unstable

    monkeypatch.setattr(nas_client_mod, "wait_until_stable", _only_data_stable)

    client = NASSyncClient(
        config=cfg,
        queue_db=tmp_path / "q.db",
        validator=Validator(),
        cache_creation=writer,
        sync_state_writer=SyncStateWriter(),
        push_callable_factory=_recording_push_factory(calls),
        lsjson_callable_factory=local_lsjson_factory(),
        worker_poll_interval_s=0.01,
    )
    await client.init()
    try:
        await client.enqueue(run_dir, ["data.bin", "other.bin"])
        for _ in range(200):
            if calls:
                break
            await asyncio.sleep(0.01)
        assert calls, "push was never invoked"
        assert calls[0] == ["data.bin"]  # only the stable file shipped
    finally:
        await client.close()
