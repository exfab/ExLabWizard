"""Plugins package. Backend Spec §6.

Re-exports the public plugin contract surface so plugin authors can write
``from exlab_wizard.plugins import Plugin, PluginContext, FileChange,
PluginError, PluginInputRequired`` without reaching into private modules.
The host- and worker-side machinery (``host``, ``_worker``, ``logger``)
remains namespaced under the package and is not part of the plugin-author
public API.
"""

from exlab_wizard.plugins.base import (
    FileChange,
    Plugin,
    PluginContext,
    PluginError,
    PluginInputRequired,
)
from exlab_wizard.plugins.logger import (
    HostPluginLogger,
    PluginLogFrame,
    PluginLogger,
    WorkerPluginLogger,
)

__all__ = [
    "FileChange",
    "HostPluginLogger",
    "Plugin",
    "PluginContext",
    "PluginError",
    "PluginInputRequired",
    "PluginLogFrame",
    "PluginLogger",
    "WorkerPluginLogger",
]
