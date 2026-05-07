"""timeout_plugin -- exercises Backend Spec §6.3.4 timeout enforcement.

The plugin's ``transform`` sleeps for 60 seconds while its manifest
declares ``isolation.timeout_seconds: 2``. The host should SIGTERM the
worker after 2s, wait the grace period, then SIGKILL; the file under
test should remain unchanged because the sleep happens *before* the
plugin's only write.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, ClassVar

from exlab_wizard.plugins import Plugin, PluginContext


class TimeoutPlugin(Plugin):
    """Hang past the declared timeout. Never reaches its file write."""

    name = "timeout_plugin"
    version = "0.1.0"
    supported_extensions: ClassVar[list[str]] = [".txt"]
    api_version = "1"

    def can_handle(self, file_path: Path, variables: dict[str, Any]) -> bool:
        return True

    def transform(self, file_path: Path, ctx: PluginContext) -> None:
        # The host's wall-clock timer (asyncio.wait_for) should kill us
        # before this returns; the file write below is intentionally
        # unreachable so the test can assert the file did not change.
        time.sleep(60)
        with file_path.open("a", encoding="utf-8") as handle:
            handle.write("UNREACHABLE\n")
