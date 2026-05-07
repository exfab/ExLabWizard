"""hello_plugin -- minimal real plugin used by the host integration suite.

Backend Spec §6.5. The plugin is intentionally trivial: every ``.txt``
file under the rendered destination has the literal string ``hello\\n``
appended to it. That is enough to:

- exercise the worker's ``transform`` path end-to-end,
- prove the plugin actually wrote to ``ctx.dst_root``,
- give the host's snapshot diff a real mtime/content change to detect.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from exlab_wizard.plugins import Plugin, PluginContext


class HelloPlugin(Plugin):
    """Append ``hello\\n`` to every ``.txt`` file the host hands us."""

    name = "hello_plugin"
    version = "0.1.0"
    supported_extensions: ClassVar[list[str]] = [".txt"]
    api_version = "1"

    def can_handle(self, file_path: Path, variables: dict[str, Any]) -> bool:
        # Every file the dispatcher passed (already filtered on suffix).
        return True

    def transform(self, file_path: Path, ctx: PluginContext) -> None:
        ctx.log.info(
            "hello_plugin appending greeting",
            file=str(file_path),
            operator=ctx.variables.get("operator"),
        )
        with file_path.open("a", encoding="utf-8") as handle:
            handle.write("hello\n")
