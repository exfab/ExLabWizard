"""E2E flow 06: Problems view + override / revoke round trip.

Frontend Spec §6.6 (problems panel), Backend Spec §4.6.2
(``/problems/events`` audit channel) + §11.5 (override flow).

The flow:

1. Seed a hard-tier finding via the test app (``?seed=hard&reset=1``).
2. Click ``Override and allow sync`` -- assert the row's state flips
   to ``Override active``.
3. Click ``Revoke override`` -- assert the row flips back to ``Active``.
"""

from __future__ import annotations

from tests.e2e.page_objects.problems_page import ProblemsPage


def test_flow_06_problems(page, server_url) -> None:
    problems = ProblemsPage(page)
    page.goto(f"{server_url}/problems?seed=hard&reset=1")
    page.wait_for_load_state("networkidle")

    problems.table.wait_for(state="visible", timeout=10_000)
    problems.row(0).wait_for(state="visible", timeout=5_000)

    # Initial state is Active and the override CTA is rendered.
    assert "Active" in problems.row_state(0).inner_text()
    problems.row_override(0).click()
    page.wait_for_load_state("networkidle")
    # State flips to Override active (the row state label is the source
    # of truth -- it includes the words "Override active").
    problems.row_state(0).wait_for(state="visible", timeout=5_000)
    assert "Override active" in problems.row_state(0).inner_text()

    # Revoke flips the state back to Active.
    problems.row_revoke(0).wait_for(state="visible", timeout=5_000)
    problems.row_revoke(0).click()
    page.wait_for_load_state("networkidle")
    problems.row(0).wait_for(state="visible", timeout=5_000)
    assert "Active" in problems.row_state(0).inner_text()
