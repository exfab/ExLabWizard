"""hello_plugin -- canonical scaffold from Backend Spec §6.5.

The ``Plugin`` re-export below is what the host's worker imports via
``from hello_plugin import Plugin``.
"""

from .plugin import HelloPlugin as Plugin

__all__ = ["Plugin"]
