"""Generic, dependency-free helpers used across the codebase.

Backend Spec §13.4 (timestamp format), §4.7 / §13.3 (state-machine
transitions). The package is a leaf in the import graph: it depends
only on stdlib and ``constants``. Higher-level modules import from
here, never the reverse.
"""

from __future__ import annotations

from exlab_wizard.utils.state import assert_forward_transition
from exlab_wizard.utils.time import (
    dt_to_iso,
    parse_utc_iso,
    parse_utc_iso_or_none,
    utc_now,
    utc_now_iso,
    utc_now_or,
)

__all__ = [
    "assert_forward_transition",
    "dt_to_iso",
    "parse_utc_iso",
    "parse_utc_iso_or_none",
    "utc_now",
    "utc_now_iso",
    "utc_now_or",
]
