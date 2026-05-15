"""Per-installation NiceGUI ``storage_secret`` token. Backend Spec §15.3.

NiceGUI's ``ui.run_with`` requires a non-empty secret to sign the
Starlette ``SessionMiddleware`` that backs ``app.storage.user`` /
``app.storage.browser``. The codebase doesn't read ``app.storage.*``
today, so the secret is functionally vestigial -- but mounting NiceGUI
demands one. We generate a 32-byte hex token on first boot, write it
to ``<state_dir>/storage_secret`` (mode 0600) atomically, and reuse it
on subsequent boots so a future feature using ``app.storage`` doesn't
silently lose state across restarts.
"""

from __future__ import annotations

import contextlib
import os
import secrets
from pathlib import Path

from exlab_wizard.io import atomic_write_bytes
from exlab_wizard.logging import get_logger

__all__ = ["STORAGE_SECRET_FILE", "load_or_create_storage_secret"]

_log = get_logger(__name__)

STORAGE_SECRET_FILE = "storage_secret"
_SECRET_BYTES = 32


def load_or_create_storage_secret(state_dir: Path) -> str:
    """Return the per-installation storage secret, generating it if absent.

    The file is created with mode 0600 so other local users cannot read
    it. On a corrupted / empty file the secret is regenerated and the
    event is logged WARN -- losing the secret only resets browser
    storage scopes, which we don't currently use.
    """
    path = Path(state_dir) / STORAGE_SECRET_FILE
    if path.exists():
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            _log.warning("storage_secret unreadable; regenerating [path=%s] %s", path, exc)
            value = ""
        if value:
            return value
        _log.warning("storage_secret file empty; regenerating [path=%s]", path)

    secret = secrets.token_hex(_SECRET_BYTES)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, secret.encode("utf-8"))
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    return secret
