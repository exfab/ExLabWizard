"""E2E flow 10: Schema-mismatch / migration prompt.

Frontend Spec §6.9 (schema-mismatch dialog), Backend Spec §3.4 (cache
schema versioning).

When a future-version ``creation.json`` is detected, the audit raises
a hard-tier finding with code ``schema_major_mismatch``. This flow
seeds a schema-mismatch finding via ``/problems?seed=schema_mismatch``
and asserts the row renders.
"""

from __future__ import annotations

from tests.e2e.page_objects.problems_page import ProblemsPage


def test_flow_10_schema_mismatch(page, server_url) -> None:
    problems = ProblemsPage(page)
    page.goto(f"{server_url}/problems?seed=schema_mismatch&reset=1")
    page.wait_for_load_state("networkidle")

    problems.table.wait_for(state="visible", timeout=10_000)
    problems.row(0).wait_for(state="visible", timeout=5_000)
    row_text = problems.row(0).inner_text()
    # The schema-mismatch finding's matched_token captures the version.
    assert "creation.json" in row_text
    assert "Active" in problems.row_state(0).inner_text()
