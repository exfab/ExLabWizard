"""crash_plugin -- exercises subprocess crash containment (SIGSEGV-like)."""

from .plugin import CrashPlugin as Plugin

__all__ = ["Plugin"]
