"""input_required_plugin -- exercises Backend Spec §6.4 suspend/resume.

On the first invocation (when no ``color`` value is present in
``ctx.variables`` / the resume payload) the plugin raises
:class:`PluginInputRequired` to ask the operator for their preferred
colour. The host re-spawns the worker with the operator's response
exposed via the ``__extra_inputs__`` channel on ``ctx.variables`` (the
worker convention from ``_worker.py``); the second call writes
``color={value}\\n`` to ``file_path`` and returns normally.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from exlab_wizard.plugins import Plugin, PluginContext, PluginInputRequired


class InputRequiredPlugin(Plugin):
    """Demand a user-supplied ``color`` value before completing."""

    name = "input_required_plugin"
    version = "0.1.0"
    supported_extensions: ClassVar[list[str]] = [".txt"]
    api_version = "1"

    def can_handle(self, file_path: Path, variables: dict[str, Any]) -> bool:
        return True

    def transform(self, file_path: Path, ctx: PluginContext) -> None:
        # On a resume the worker stashes the operator's reply under the
        # ``__extra_inputs__`` sentinel (see plugins/_worker.py); on the
        # first call neither sentinel nor the bare key is set.
        extra = ctx.variables.get("__extra_inputs__") or {}
        color = extra.get("color") or ctx.variables.get("color")
        if not color:
            raise PluginInputRequired(
                fields=[
                    {
                        "id": "color",
                        "label": "Favorite color?",
                        "type": "string",
                        "required": True,
                    }
                ],
                reason="Need user color preference",
            )

        with file_path.open("a", encoding="utf-8") as handle:
            handle.write(f"color={color}\n")
