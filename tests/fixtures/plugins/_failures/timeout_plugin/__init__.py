"""Failure fixture: plugin that hangs in ``transform`` past its timeout."""

from .plugin import TimeoutPlugin as Plugin

__all__ = ["Plugin"]
