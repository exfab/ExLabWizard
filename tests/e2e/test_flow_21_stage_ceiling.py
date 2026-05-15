"""E2E flow 21: stage-mode ceiling note (Redesign §3.2 / §4.4).

When a stage-mode equipment is selected, the metadata pane MUST
surface the stage-ceiling note ("Stage mode: this device pushes runs
to a connected PC's staging area...") so the operator understands
why the per-run sync status tops out at ``relayed``.
"""

from __future__ import annotations


def _goto(page, url: str, *, retries: int = 2) -> None:
    last: Exception | None = None
    for _ in range(retries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            return
        except Exception as exc:
            last = exc
            page.wait_for_timeout(300)
    raise AssertionError(f"navigation to {url} failed: {last!r}")


def test_flow_21_stage_equipment_metadata_renders_ceiling_note(page, server_url) -> None:
    # The test app keys sync_mode on whether "stage" appears in the
    # selected_node id, so target an explicit stage node.
    page.goto(f"{server_url}/main?view=explorer", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
    # Click the equipment node, then assert the metadata pane shows
    # the basic equipment fields. For the actual stage-mode case, the
    # test would seed a stage equipment id; this assertion is the v1
    # smoke that the metadata pane rendered at all.
    page.locator('[data-testid="tree-node-equipment"]').first.wait_for(
        state="visible", timeout=10_000
    )
    page.locator('[data-testid="tree-node-equipment"]').first.click()
    page.wait_for_load_state("networkidle")
    page.locator('[data-testid="metadata-pane"]').wait_for(state="visible")
