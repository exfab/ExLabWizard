"""Refresh coordinator: coalesce the 30 s tree refresh with the folder feed.

GUI/Orchestrator Redesign §5 / §9.1. Two refresh loops touch the
filesystem from the UI side — the 30 s GET /tree refresh and the
~2-3 s GET /folder folder feed. The coordinator records the last time
either kicked off a refresh and lets the other skip its tick if the
filesystem was just walked.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

# Minimum gap between two FS walks. Tunable; the spec just asks they not
# both walk "in the same tick".
COALESCE_WINDOW_S: float = 1.0


@dataclass
class RefreshCoordinator:
    """Tracks the last refresh time per source ("tree" | "folder")."""

    last_tree_refresh_s: float = 0.0
    last_folder_refresh_s: float = 0.0

    def record_tree_refresh(self) -> None:
        self.last_tree_refresh_s = time.monotonic()

    def record_folder_refresh(self) -> None:
        self.last_folder_refresh_s = time.monotonic()

    def should_skip_folder(self) -> bool:
        """True if the tree just walked and the folder feed should yield."""
        return (time.monotonic() - self.last_tree_refresh_s) < COALESCE_WINDOW_S

    def should_skip_tree(self) -> bool:
        """True if the folder feed just walked and the tree should yield."""
        return (time.monotonic() - self.last_folder_refresh_s) < COALESCE_WINDOW_S
