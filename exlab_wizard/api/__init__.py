"""HTTP API package. Backend Spec §4.

Public re-exports so callers (the launcher, tests) can import the
factory and dependency types without reaching into submodules.
"""

from exlab_wizard.api.app import AppDependencies, AuditChannel, create_app

__all__ = ["AppDependencies", "AuditChannel", "create_app"]
