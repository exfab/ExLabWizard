"""GitHub Releases lookup for the update notifier. Design Spec §15.6 / §15.8 item 3.

Mirrors the :class:`exlab_wizard.lims.client.LIMSClient` httpx shape (15-second
timeout, ``raise_for_status``, structured logging). The single call hits the
GitHub ``releases/latest`` API. Every failure mode -- offline, DNS, 403 rate
limit, 5xx, malformed JSON -- is swallowed and returns ``None`` so a constrained
workstation degrades to "no prompt" rather than surfacing an error.
"""

from __future__ import annotations

import httpx

from exlab_wizard import __version__
from exlab_wizard.logging import get_logger
from exlab_wizard.update_check.constants import API_LATEST_URL

__all__ = ["fetch_latest_tag"]

logger = get_logger(__name__)

_DEFAULT_TIMEOUT_SECONDS: float = 15.0

# GitHub returns 403 to unidentified clients, so a User-Agent is mandatory.
# The Accept + API-version headers pin the documented v3 JSON response shape.
_GITHUB_HEADERS: dict[str, str] = {
    "User-Agent": f"ExLab-Wizard/{__version__}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


async def fetch_latest_tag(client: httpx.AsyncClient | None = None) -> tuple[str, str] | None:
    """Return ``(tag_name, html_url)`` for the latest release, or ``None``.

    ``client`` is injectable so tests can pass an ``ASGITransport``-backed
    client; production passes ``None`` and we build (and close) a throwaway
    :class:`httpx.AsyncClient`. Any network / parse failure returns ``None``.
    """
    owns_client = client is None
    http = client if client is not None else httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS)
    try:
        response = await http.get(API_LATEST_URL, headers=_GITHUB_HEADERS)
        response.raise_for_status()
        data = response.json()
        # ``releases/latest`` returns the most recent non-prerelease, non-draft
        # release, so a pre-release never reaches an operator as an update
        # prompt. Re-check the flags defensively in case the endpoint or its
        # payload shape ever changes -- a pre-release must never notify.
        if data.get("prerelease") or data.get("draft"):
            logger.info("update_check.skipped_prerelease", extra={"tag": data.get("tag_name")})
            return None
        return data["tag_name"], data["html_url"]
    except httpx.HTTPError as exc:
        # Offline / DNS / rate-limited (403) / 5xx -- expected on constrained
        # hosts; info-level so it doesn't read as an error in the central log.
        logger.info("update_check.fetch_failed", extra={"error": str(exc)})
        return None
    except (KeyError, ValueError) as exc:
        # Unexpected JSON shape (missing keys / non-JSON body).
        logger.warning("update_check.fetch_bad_payload", extra={"error": str(exc)})
        return None
    finally:
        if owns_client:
            await http.aclose()
