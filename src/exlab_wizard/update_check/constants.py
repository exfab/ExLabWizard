"""Update-channel constants. Design Spec §15.6 / §15.8 item 3.

The repository coordinates live in exactly one place so the API call (the
machine-readable ``releases/latest`` JSON endpoint) and the tray menu item
(the human-facing ``releases/latest`` browser page) cannot drift apart.
"""

from __future__ import annotations

__all__ = [
    "API_LATEST_URL",
    "GITHUB_OWNER",
    "GITHUB_REPO",
    "RELEASES_LATEST_URL",
]

GITHUB_OWNER = "exfab"
GITHUB_REPO = "ExLabWizard"

# Machine-readable latest-release metadata (tag_name, html_url, assets, ...).
API_LATEST_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

# Human-facing releases page the "Check for updates…" tray item opens.
RELEASES_LATEST_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
