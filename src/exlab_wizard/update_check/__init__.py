"""Startup update-notifier channel. Design Spec §15.6 / §15.8 item 3.

§15.8 item 3 commits ExLab-Wizard to a *self-update channel*. This package
is the **notifier-only** first stage of that channel: on launch it asks the
GitHub Releases API whether a newer tag exists and, if so, raises one OS
notification plus surfaces a "Check for updates…" tray item that opens the
releases page in the browser. There is deliberately **no auto-install** here
-- downloading and swapping the bundle is a later stage of §15.8 item 3.

Everything degrades silently: an offline, firewalled, or rate-limited
workstation simply never prompts (see :func:`fetch_latest_tag`), and a
malformed tag never falsely prompts (see :func:`is_newer`).
"""

from __future__ import annotations

from exlab_wizard.update_check.checker import fetch_latest_tag
from exlab_wizard.update_check.constants import (
    API_LATEST_URL,
    GITHUB_OWNER,
    GITHUB_REPO,
    RELEASES_LATEST_URL,
)
from exlab_wizard.update_check.runner import UpdateChecker
from exlab_wizard.update_check.version import is_newer

__all__ = [
    "API_LATEST_URL",
    "GITHUB_OWNER",
    "GITHUB_REPO",
    "RELEASES_LATEST_URL",
    "UpdateChecker",
    "fetch_latest_tag",
    "is_newer",
]
