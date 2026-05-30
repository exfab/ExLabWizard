"""Unit tests for the shared empty-state placeholder (Phase 5).

The helper renders into the NiceGUI auto-index page outside a ``@ui.page``
context, so it can be rendered directly and its element tree inspected
(mirrors the framed-pane / metadata-pane tests).
"""

from __future__ import annotations

from typing import Any

from exlab_wizard.ui.components.empty_state import empty_state


def _walk(element: Any) -> Any:
    yield element
    for slot in (getattr(element, "slots", None) or {}).values():
        for child in slot.children:
            yield from _walk(child)


def _by_testid(container: Any, testid: str) -> Any:
    return next((el for el in _walk(container) if el._props.get("data-testid") == testid), None)


def _texts(element: Any) -> list[str]:
    return [t for el in _walk(element) if (t := getattr(el, "_text", None))]


def test_empty_state_carries_testid_and_message() -> None:
    """The placeholder keeps the caller's testid (once) and shows the hint."""

    column = empty_state(icon="folder_open", message="Select a folder.", testid="file-list-empty")
    assert column is not None
    # The testid lands on the outer column exactly once (no duplicate clash
    # that would break a Playwright strict-mode get_by_test_id).
    assert _by_testid(column, "file-list-empty") is column
    assert sum(1 for el in _walk(column) if el._props.get("data-testid") == "file-list-empty") == 1
    assert "Select a folder." in _texts(column)


def test_empty_state_renders_the_icon() -> None:
    """The icon name reaches the rendered icon element."""

    column = empty_state(icon="folder_open", message="x", testid="t")
    icon_names = [el._props.get("name") for el in _walk(column)]
    assert "folder_open" in icon_names
