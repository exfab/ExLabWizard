"""Travelling problem-badge pure function. GUI/Orchestrator Redesign §4.5.

Given the validator finding set and per-node fold (expand) state, return
for each node the badge to render. The badge for each finding sits on
the **shallowest collapsed node** on the path to that finding, travelling
inward as nodes expand. If a node aggregates both red and amber findings
the red wins; the count is the total.

Pure function; no NiceGUI dependency. The tree renderer consumes the
returned ``BadgeProps`` to paint the badges.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Literal

from exlab_wizard.constants.enums import Tier


BadgeColor = Literal["red", "amber"]


@dataclass(frozen=True)
class BadgeProps:
    """Render properties for a single tree node's badge."""

    color: BadgeColor
    count: int


@dataclass(frozen=True)
class FindingLocation:
    """A validator finding pinned to a tree-node path.

    ``path`` is the slash-joined node id of the deepest tree node the
    finding belongs to (e.g. ``"EQUIP_A/PROJ-0001/Runs/Run_2026-04-17T14-32"``).
    ``tier`` is "hard" or "soft".
    """

    path: str
    tier: str  # "hard" | "soft"


def travelling_badges(
    findings: Iterable[FindingLocation],
    fold_state: dict[str, bool],
) -> dict[str, BadgeProps]:
    """Compute ``{node_id: BadgeProps}`` for every node that gets a badge.

    ``fold_state[node_id] == True`` means the node is **expanded**; False
    or missing means collapsed. For each finding, walk from the leaf
    toward the root and find the **shallowest** collapsed ancestor — that
    ancestor receives the badge contribution. If every ancestor on the
    path is expanded, the finding lives on its own node.
    """
    per_node_red: dict[str, int] = defaultdict(int)
    per_node_amber: dict[str, int] = defaultdict(int)

    for finding in findings:
        target = _shallowest_collapsed_ancestor(finding.path, fold_state)
        if finding.tier == Tier.HARD.value:
            per_node_red[target] += 1
        else:
            per_node_amber[target] += 1

    out: dict[str, BadgeProps] = {}
    for node_id in set(per_node_red) | set(per_node_amber):
        red = per_node_red.get(node_id, 0)
        amber = per_node_amber.get(node_id, 0)
        if red > 0:
            # Red wins; total count includes amber siblings on the same
            # collapsed ancestor.
            out[node_id] = BadgeProps(color="red", count=red + amber)
        else:
            out[node_id] = BadgeProps(color="amber", count=amber)
    return out


def _shallowest_collapsed_ancestor(
    path: str,
    fold_state: dict[str, bool],
) -> str:
    """Walk root → leaf along ``path``; return the first collapsed ancestor.

    If every ancestor is expanded, return ``path`` itself (the finding
    lives on its own node).
    """
    parts = [p for p in path.split("/") if p]
    cumulative: str = ""
    for part in parts:
        cumulative = f"{cumulative}/{part}" if cumulative else part
        if cumulative == path:
            # We reached the finding's own node without finding a collapsed
            # ancestor; the badge lives on the leaf.
            return path
        if not fold_state.get(cumulative, False):
            # Collapsed (or unknown) — this is the shallowest collapsed
            # ancestor on the path.
            return cumulative
    return path
