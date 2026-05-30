"""Unit tests for tree node-type icons."""

from __future__ import annotations

from exlab_wizard.ui.components.tree import (
    KIND_EQUIPMENT,
    KIND_PROJECT,
    KIND_RECEIVED_EQUIPMENT,
    KIND_RUN_EXPERIMENTAL,
    KIND_RUN_TEST,
    TreeNode,
    _node_type_props,
    to_nicegui_nodes,
)


def test_node_type_props_by_kind() -> None:
    assert _node_type_props(KIND_EQUIPMENT)[0] == "mdi-microscope"
    assert _node_type_props(KIND_RECEIVED_EQUIPMENT) == ("mdi-microscope", "--oi-blue")
    assert _node_type_props(KIND_PROJECT)[0] == "mdi-folder"
    assert _node_type_props(KIND_RUN_EXPERIMENTAL)[0] == "mdi-file-document"
    assert _node_type_props(KIND_RUN_TEST)[0] == "mdi-flask-outline"


def test_run_node_carries_type_icon() -> None:
    node = TreeNode(node_id="EQ1/P/Run_x", label="Run_x", kind=KIND_RUN_EXPERIMENTAL)
    payload = to_nicegui_nodes([node])
    assert payload[0]["type_icon"] == "mdi-file-document"
    assert payload[0]["type_color"] == "--oi-sky"


def test_to_nicegui_nodes_carries_type_icon() -> None:
    node = TreeNode(node_id="EQ1", label="EQ1", kind=KIND_EQUIPMENT)
    payload = to_nicegui_nodes([node])
    assert payload[0]["type_icon"] == "mdi-microscope"
    assert payload[0]["type_color"] == "--oi-blue"
