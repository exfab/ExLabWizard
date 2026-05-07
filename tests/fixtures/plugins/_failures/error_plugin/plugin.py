"""error_plugin -- raises PluginError mid-transform.

Exercises the Backend Spec §6.1.3 expected-failure path. The host
should report status="failed" with exit_code=1 and surface the
raised message back to the caller without aborting the whole pass.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from exlab_wizard.errors import PluginError
from exlab_wizard.plugins import Plugin, PluginContext


class ErrorPlugin(Plugin):
    """Always raises PluginError on transform."""

    name = "error_plugin"
    version = "0.1.0"
    supported_extensions: ClassVar[list[str]] = [".txt"]
    api_version = "1"

    def can_handle(self, file_path: Path, variables: dict[str, Any]) -> bool:
        return True

    def transform(self, file_path: Path, ctx: PluginContext) -> None:
        raise PluginError("error_plugin always fails by design")
