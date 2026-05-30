"""Semver comparison for the update notifier. Design Spec §15.6 / §15.8 item 3.

GitHub release tags carry a leading ``v`` by convention (``v0.2.0``) while the
package version is plain semver (``0.1.0``, see
:data:`exlab_wizard.__version__`). :func:`is_newer` normalizes both sides and
delegates ordering to :class:`packaging.version.Version` so pre-release
ordering (``1.0.0a1`` < ``1.0.0``) is handled correctly.
"""

from __future__ import annotations

from packaging.version import InvalidVersion, Version

from exlab_wizard.logging import get_logger

__all__ = ["is_newer"]

_log = get_logger(__name__)


def _strip_v_prefix(value: str) -> str:
    """Drop a single leading ``v``/``V`` so ``v0.2.0`` parses as ``0.2.0``."""
    if value[:1] in ("v", "V"):
        return value[1:]
    return value


def is_newer(current: str, latest_tag: str) -> bool:
    """Return ``True`` when ``latest_tag`` is a strictly newer release.

    Both arguments may carry a leading ``v``/``V``. On any parse failure we
    log a warning and return ``False`` -- a workstation must never be falsely
    prompted to update because of a malformed or non-semver tag.
    """
    try:
        current_v = Version(_strip_v_prefix(current))
        latest_v = Version(_strip_v_prefix(latest_tag))
    except InvalidVersion:
        _log.warning(
            "update_check.version_parse_failed",
            extra={"current": current, "latest_tag": latest_tag},
        )
        return False
    return latest_v > current_v
