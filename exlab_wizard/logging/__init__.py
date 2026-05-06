"""Canonical logger package. Backend Spec §16.

This package owns the runtime logger architecture for ExLab-Wizard:

- :func:`get_logger` is the single entry point for logger creation
  (§16.2.1; the only allowed call site for ``logging.getLogger``).
- :func:`configure_logging` installs the §16.2.5 queue-based handler
  chain on FastAPI lifespan startup.
- :func:`set_run_context` is a context manager that pushes structured
  tags (host / equipment / project / run-kind / run-id) onto per-task
  ``contextvars`` so subsequent log calls within the block carry them.

Component authors only need ``get_logger`` for normal logging; the
launcher and the creation controller use ``configure_logging`` and
``set_run_context`` respectively. See §16.2 for the full architecture.
"""

from exlab_wizard.logging.context import (
    clear_run_context,
    get_run_context,
    set_run_context,
)
from exlab_wizard.logging.manager import configure_logging, get_logger

__all__ = [
    "clear_run_context",
    "configure_logging",
    "get_logger",
    "get_run_context",
    "set_run_context",
]
