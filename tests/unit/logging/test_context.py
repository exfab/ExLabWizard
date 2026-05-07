"""Tests for ``exlab_wizard.logging.context``.

Backend Spec §16.2.3 commits the per-task context-var contract: a
``set_run_context`` block pushes the supplied vars on entry and resets
them on exit, ``contextvars`` are async-safe, and ``get_run_context``
returns a snapshot dict the formatter can iterate over.

These tests pin all four behaviors plus the ``clear_run_context``
escape hatch used by test fixtures.
"""

from __future__ import annotations

import asyncio

import pytest

from exlab_wizard.logging.context import (
    clear_run_context,
    equipment_id_var,
    get_run_context,
    host_var,
    project_short_id_var,
    run_id_var,
    run_kind_var,
    set_run_context,
)


@pytest.fixture(autouse=True)
def _reset_context() -> None:
    """Wipe context vars before each test to prevent cross-test leakage."""
    clear_run_context()


# ---------------------------------------------------------------------------
# Push / pop semantics
# ---------------------------------------------------------------------------


def test_set_run_context_pushes_supplied_vars() -> None:
    with set_run_context(
        host="labpc-04",
        equipment_id="CONFOCAL_01",
        project_short_id="PROJ-0042",
        run_kind="experimental",
        run_id="Run_2026-04-17T14-32-00",
    ):
        assert host_var.get() == "labpc-04"
        assert equipment_id_var.get() == "CONFOCAL_01"
        assert project_short_id_var.get() == "PROJ-0042"
        assert run_kind_var.get() == "experimental"
        assert run_id_var.get() == "Run_2026-04-17T14-32-00"


def test_unsupplied_vars_are_unchanged() -> None:
    # Outer block establishes a baseline; inner block widens with run_id.
    with (
        set_run_context(host="labpc-04", equipment_id="CONFOCAL_01"),
        set_run_context(run_id="Run_2026-04-17T14-32-00"),
    ):
        assert host_var.get() == "labpc-04"
        assert equipment_id_var.get() == "CONFOCAL_01"
        assert run_id_var.get() == "Run_2026-04-17T14-32-00"
        # Vars never set anywhere remain None.
        assert project_short_id_var.get() is None
        assert run_kind_var.get() is None


def test_context_exit_restores_prior_values() -> None:
    with set_run_context(host="outer-host"):
        assert host_var.get() == "outer-host"
        with set_run_context(host="inner-host"):
            assert host_var.get() == "inner-host"
        # Inner block restores the outer value, NOT None.
        assert host_var.get() == "outer-host"
    # Outer block restores the original (None / pre-context) value.
    assert host_var.get() is None


def test_context_exit_clears_when_no_prior_value() -> None:
    assert host_var.get() is None
    with set_run_context(host="labpc-04"):
        assert host_var.get() == "labpc-04"
    assert host_var.get() is None


def test_set_run_context_with_no_args_is_noop() -> None:
    # No arguments means no var changes.
    with set_run_context(host="labpc-04"):
        with set_run_context():
            assert host_var.get() == "labpc-04"
        assert host_var.get() == "labpc-04"


# ---------------------------------------------------------------------------
# Async / contextvars safety
# ---------------------------------------------------------------------------


async def test_concurrent_asyncio_tasks_have_independent_contexts() -> None:
    """Two ``asyncio.gather``-ed tasks must not see each other's context.

    This is the §16.2.3 / §16.2.5 concurrency contract: orchestrator mode
    runs multiple equipment sessions concurrently; their log lines must
    carry the right ``[equip:..]`` / ``[run:..]`` tags without bleed.
    """
    seen_a: dict[str, str | None] = {}
    seen_b: dict[str, str | None] = {}

    async def task_a() -> None:
        with set_run_context(equipment_id="CONFOCAL_01", run_id="Run_A"):
            await asyncio.sleep(0)  # yield to task_b
            seen_a.update(get_run_context())

    async def task_b() -> None:
        with set_run_context(equipment_id="CONFOCAL_02", run_id="Run_B"):
            await asyncio.sleep(0)  # yield to task_a
            seen_b.update(get_run_context())

    await asyncio.gather(task_a(), task_b())

    assert seen_a["equipment_id"] == "CONFOCAL_01"
    assert seen_a["run_id"] == "Run_A"
    assert seen_b["equipment_id"] == "CONFOCAL_02"
    assert seen_b["run_id"] == "Run_B"


# ---------------------------------------------------------------------------
# Snapshot accessor + clear helper
# ---------------------------------------------------------------------------


def test_get_run_context_returns_snapshot_dict() -> None:
    with set_run_context(host="labpc-04", equipment_id="CONFOCAL_01"):
        snapshot = get_run_context()
    # It's a dict, not a live view -- mutating it after the with-block
    # exits doesn't affect the context vars.
    assert isinstance(snapshot, dict)
    assert snapshot == {
        "host": "labpc-04",
        "equipment_id": "CONFOCAL_01",
        "project_short_id": None,
        "run_kind": None,
        "run_id": None,
    }


def test_get_run_context_returns_all_keys_even_when_empty() -> None:
    snapshot = get_run_context()
    # Caller can rely on the full set of keys being present so it can
    # iterate without ``KeyError``.
    assert set(snapshot.keys()) == {
        "host",
        "equipment_id",
        "project_short_id",
        "run_kind",
        "run_id",
    }
    assert all(v is None for v in snapshot.values())


def test_clear_run_context_resets_every_var() -> None:
    # Force every var to a non-None value via direct ``.set`` (mirrors
    # what an old test fixture might have left behind).
    host_var.set("leftover")
    equipment_id_var.set("leftover")
    project_short_id_var.set("leftover")
    run_kind_var.set("leftover")
    run_id_var.set("leftover")

    clear_run_context()

    assert host_var.get() is None
    assert equipment_id_var.get() is None
    assert project_short_id_var.get() is None
    assert run_kind_var.get() is None
    assert run_id_var.get() is None
