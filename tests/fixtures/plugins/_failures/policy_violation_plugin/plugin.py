"""policy_violation_plugin -- exercises Backend Spec §6.1.5 enforcement.

The plugin's ``transform`` writes to ``ctx.dst_root / "README.md"``,
which is on the host's forbidden-prefix list. The host's snapshot diff
should detect the write, mark the plugin's ``status`` as
``policy_violation``, and revert the file system to the pre-plugin
state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from exlab_wizard.plugins import Plugin, PluginContext


class PolicyViolationPlugin(Plugin):
    """Write into ``README.md`` (a wizard-controlled file)."""

    name = "policy_violation_plugin"
    version = "0.1.0"
    supported_extensions: ClassVar[list[str]] = [".txt"]
    api_version = "1"

    def can_handle(self, file_path: Path, variables: dict[str, Any]) -> bool:
        return True

    def transform(self, file_path: Path, ctx: PluginContext) -> None:
        # README.md sits at the top of the run directory; the host owns
        # it (Backend Spec §6.1.5) and rejects any plugin write here.
        forbidden_path = ctx.dst_root / "README.md"
        forbidden_path.write_text(
            "# tampered by policy_violation_plugin\n",
            encoding="utf-8",
        )
