"""crash_plugin -- exercises subprocess crash containment.

The plugin's ``transform`` calls :func:`os._exit` with status 139 (the
classic SIGSEGV-equivalent exit code) before writing anything. The host
should observe a non-zero exit code, mark the plugin failed, and
continue serving the rest of the pass without itself crashing.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar

from exlab_wizard.plugins import Plugin, PluginContext


class CrashPlugin(Plugin):
    """Hard-exit the worker process before any file write."""

    name = "crash_plugin"
    version = "0.1.0"
    supported_extensions: ClassVar[list[str]] = [".txt"]
    api_version = "1"

    def can_handle(self, file_path: Path, variables: dict[str, Any]) -> bool:
        return True

    def transform(self, file_path: Path, ctx: PluginContext) -> None:
        # ``os._exit`` bypasses Python finalization, mirroring a SIGSEGV
        # or libc-level abort -- the host sees a non-zero exit status
        # and no IPC envelope on stdout. We do this *before* any file
        # write so the test can assert the file is unchanged.
        os._exit(139)
