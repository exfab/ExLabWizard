"""Logger factory. Backend Section 16.2.1.

Phase 1 stub: returns a stdlib logger named via the package hierarchy.
Phase 3 will replace this with the full handler chain.
"""
import logging

def get_logger(name: str) -> logging.Logger:
    """Return a logger for the given name. The ONLY place in the codebase that may call logging.getLogger."""
    return logging.getLogger(name)
