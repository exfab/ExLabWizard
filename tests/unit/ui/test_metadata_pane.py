"""Unit tests for the metadata pane's selected-file/folder sub-card.

GUI/Orchestrator Redesign §4.4, Phase 4 / Option B. The pane renders into
the NiceGUI auto-index page outside a ``@ui.page`` context, so it can be
rendered directly here and its element tree inspected (mirrors the
framed-pane / file-list tests).
"""

from __future__ import annotations

from typing import Any

from exlab_wizard.ui.components.metadata_pane import (
    MetadataPaneState,
    render_metadata_pane,
    selected_file_card_title,
)


def _walk(element: Any) -> Any:
    yield element
    for slot in (getattr(element, "slots", None) or {}).values():
        for child in slot.children:
            yield from _walk(child)


def _by_testid(container: Any, testid: str) -> Any:
    return next((el for el in _walk(container) if el._props.get("data-testid") == testid), None)


def _texts(element: Any) -> list[str]:
    return [t for el in _walk(element) if (t := getattr(el, "_text", None))]


def _file_payload(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "kind": "file",
        "name": "scan.tif",
        "path": "/r/scan.tif",
        "size": "2.0 KB",
        "modified": "2026-05-20T10:00:00Z",
        "sync_status": "synced",
        "tombstone": False,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# selected_file_card_title (pure)
# ---------------------------------------------------------------------------


def test_selected_file_card_title_maps_kind() -> None:
    assert selected_file_card_title({"kind": "file"}) == "Selected file"
    assert selected_file_card_title({"kind": "folder"}) == "Selected folder"
    # An absent / unknown kind defaults to the file heading.
    assert selected_file_card_title({}) == "Selected file"


# ---------------------------------------------------------------------------
# File sub-card
# ---------------------------------------------------------------------------


def test_file_sub_card_renders_fields() -> None:
    state = MetadataPaneState(selected_node=None, node_kind=None, selected_file=_file_payload())
    container = render_metadata_pane(state=state)
    card = _by_testid(container, "metadata-selected-file")
    assert card is not None
    texts = _texts(card)
    assert "SELECTED FILE" in texts
    assert "scan.tif" in texts
    assert "Size:" in texts
    assert "2.0 KB" in texts
    assert "Modified:" in texts
    assert "Path:" in texts
    assert "/r/scan.tif" in texts


def test_tombstone_file_sub_card_omits_size_and_notes_on_nas() -> None:
    payload = _file_payload(sync_status="on_nas", tombstone=True, size=None)
    state = MetadataPaneState(selected_node=None, node_kind=None, selected_file=payload)
    container = render_metadata_pane(state=state)
    card = _by_testid(container, "metadata-selected-file")
    assert card is not None
    # A tombstone has no on-disk copy, so the Size row is omitted...
    assert "Size:" not in _texts(card)
    # ...and an "On NAS" note explains the cleared local copy.
    assert _by_testid(card, "metadata-selected-file-tombstone-note") is not None


# ---------------------------------------------------------------------------
# Folder sub-card
# ---------------------------------------------------------------------------


def test_folder_sub_card_renders_count_no_size() -> None:
    payload = {
        "kind": "folder",
        "name": "Runs",
        "path": "/r/Runs",
        "item_count": 7,
        "rollup": "failed",
    }
    state = MetadataPaneState(selected_node=None, node_kind=None, selected_file=payload)
    container = render_metadata_pane(state=state)
    card = _by_testid(container, "metadata-selected-folder")
    assert card is not None
    texts = _texts(card)
    assert "SELECTED FOLDER" in texts
    assert "Runs" in texts
    assert "Items:" in texts
    assert "7" in texts
    # Path is part of the folder card (RESUME step 8: Name/Items/Sync/Path).
    assert "Path:" in texts
    assert "/r/Runs" in texts
    # No size row for a folder (recursive total deferred, spec §11).
    assert "Size:" not in texts


def test_folder_sub_card_tolerates_degraded_scan() -> None:
    """A degraded folder scan (item_count=None / rollup=None) still renders."""
    payload = {
        "kind": "folder",
        "name": "Runs",
        "path": "/r/Runs",
        "item_count": None,
        "rollup": None,
    }
    state = MetadataPaneState(selected_node=None, node_kind=None, selected_file=payload)
    container = render_metadata_pane(state=state)
    card = _by_testid(container, "metadata-selected-folder")
    assert card is not None
    # The Items row renders a neutral dash for the unknown count.
    assert "-" in _texts(card)


# ---------------------------------------------------------------------------
# Placement: the sub-card appends BELOW node content / the empty state
# ---------------------------------------------------------------------------


def test_sub_card_appends_after_empty_state() -> None:
    """With no node selected, the sub-card sits beneath the empty-state hint."""
    state = MetadataPaneState(selected_node=None, node_kind=None, selected_file=_file_payload())
    container = render_metadata_pane(state=state)
    assert _by_testid(container, "metadata-pane-empty") is not None
    assert _by_testid(container, "metadata-selected-file") is not None


def test_sub_card_appends_after_populated_node() -> None:
    """A selected run node's content stays; the sub-card appends below it."""
    run_payload = {"name": "Run_2026-05-07", "run_kind": "experimental", "operator": "asmith"}
    state = MetadataPaneState(
        selected_node="EQ1/Cortex/Run_2026-05-07",
        node_kind="run",
        payload=run_payload,
        selected_file=_file_payload(),
    )
    container = render_metadata_pane(state=state)
    texts = _texts(container)
    # The run dispatch's content is present (operator row)...
    assert "asmith" in texts
    # ...and the sub-card is appended rather than replacing it.
    assert _by_testid(container, "metadata-selected-file") is not None


def test_no_sub_card_without_selected_file() -> None:
    state = MetadataPaneState(selected_node=None, node_kind=None, selected_file=None)
    container = render_metadata_pane(state=state)
    assert _by_testid(container, "metadata-selected-file") is None
    assert _by_testid(container, "metadata-selected-folder") is None
    assert _by_testid(container, "metadata-pane-empty") is not None
