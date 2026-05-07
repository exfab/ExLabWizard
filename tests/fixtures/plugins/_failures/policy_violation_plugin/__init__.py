"""policy_violation_plugin -- writes to a forbidden path (Backend Spec §6.1.5)."""

from .plugin import PolicyViolationPlugin as Plugin

__all__ = ["Plugin"]
