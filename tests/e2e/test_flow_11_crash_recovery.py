"""E2E flow 11: Crash-recovery prompt on relaunch.

Frontend Spec §6.10 (crash recovery dialog), Backend Spec §7.7
(session persistence + restart hand-off).

A directory with no ``creation.json`` is an orphan; the audit detects
it and surfaces it in the Problems tab. This flow seeds an orphan
finding and verifies the row carries the right path + finding class.
"""

from __future__ import annotations

from tests.e2e.page_objects.problems_page import ProblemsPage


def test_flow_11_crash_recovery(page, server_url) -> None:
    problems = ProblemsPage(page)
    page.goto(f"{server_url}/problems?seed=orphan&reset=1")
    page.wait_for_load_state("networkidle")

    problems.table.wait_for(state="visible", timeout=10_000)
    problems.row(0).wait_for(state="visible", timeout=5_000)
    row_text = problems.row(0).inner_text()
    assert "Orphan" in row_text
    assert "Run_2026-05-07-orphan" in row_text
