"""OS keyring storage with encrypted-at-rest fallback. Backend Spec §7.4.

Two backends, automatic dispatch:

1. **OS keyring** -- the :mod:`keyring` package routes to Keychain
   (macOS), Credential Manager (Windows), or Secret Service (Linux
   desktop) per §7.4. This is the preferred path because the OS
   already owns lifecycle (encryption-at-rest, scoping to the OS user).
2. **Encrypted-at-rest fallback** -- when no keyring backend is
   available (Linux headless acquisition machines per §7.4.4), the
   store writes a single Fernet-encrypted JSON document at
   ``<state_dir>/exlab-wizard/secrets.enc``. The Fernet key is derived
   from a master passphrase via Argon2id (``time_cost=3``,
   ``memory_cost=64 MiB``, ``parallelism=4``). The salt lives next to
   the ciphertext in the same file (the salt is not secret; the
   passphrase is).

The store API is intentionally credential-agnostic: it indexes secrets
by ``(KEYRING_SERVICE, username)`` so the LIMS credential
(``username="lims"``) and per-equipment NAS credentials
(``username="nas:<equipment_id>"``) share one store.

The ``passphrase_provider`` callable is invoked lazily on the first
fallback-mode operation so that working keyring environments never
prompt the operator. The launcher wires it to ``getpass.getpass`` per
§7.4.4 step 3; tests can pass a constant-returning lambda.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import keyring
import keyring.errors
import msgspec
from argon2.low_level import Type, hash_secret_raw
from cryptography.fernet import Fernet, InvalidToken

from exlab_wizard.constants import KEYRING_SERVICE, SECRETS_FILE
from exlab_wizard.errors import KeyringUnavailableError
from exlab_wizard.logging import get_logger

__all__ = ["KeyringStore"]

logger = get_logger(__name__)

# Argon2id KDF parameters per Backend Spec §7.4.4 step 2.
_ARGON2_TIME_COST: int = 3
_ARGON2_MEMORY_KIB: int = 64 * 1024  # 64 MiB; argon2 takes KiB
_ARGON2_PARALLELISM: int = 4
_ARGON2_HASH_LEN: int = 32  # Fernet wants a 32-byte key (base64-encoded)
_ARGON2_SALT_BYTES: int = 16  # 8 KiB referenced in the spec is per-store -- 16 B is the standard salt length for the KDF inputs

_FALLBACK_FORMAT_VERSION: int = 1


class KeyringStore:
    """OS keyring with encrypted-at-rest fallback.

    Backend Spec §7.4. Tries the OS keyring first via the :mod:`keyring`
    module. When that backend raises :class:`keyring.errors.KeyringError`
    (or one of its subclasses for "no backend installed"), falls back
    to ``<state_dir>/exlab-wizard/secrets.enc`` encrypted with Fernet
    (AES-128-CBC + HMAC-SHA256), keyed by an Argon2id KDF over a
    user-supplied master passphrase.

    The fallback path requires a ``passphrase_provider`` callable; if
    the keyring is available this argument may be ``None`` -- it is
    only invoked when the fallback engages.
    """

    def __init__(
        self,
        *,
        state_dir: Path,
        passphrase_provider: Callable[[], str] | None = None,
    ) -> None:
        self._state_dir = Path(state_dir)
        self._secrets_path = self._state_dir / SECRETS_FILE
        self._passphrase_provider = passphrase_provider

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_keyring_available(self) -> bool:
        """Best-effort probe of the OS keyring backend.

        Implemented as a round-trip ``set`` + ``delete`` of a sentinel
        value. A backend that raises on either side is considered
        unavailable. Per §7.4.4 the ``keyring`` package's "fail" backends
        raise :class:`keyring.errors.KeyringError` from these calls.
        """
        sentinel_username = "_exlab_probe"
        try:
            keyring.set_password(KEYRING_SERVICE, sentinel_username, "ok")
            keyring.delete_password(KEYRING_SERVICE, sentinel_username)
        except keyring.errors.KeyringError:
            return False
        except Exception:
            return False
        return True

    def get_password(self, *, username: str) -> str | None:
        """Look up the secret for ``(KEYRING_SERVICE, username)``.

        Returns ``None`` when the entry is absent in both backends.
        Errors from the OS keyring are caught and the fallback is
        consulted; errors from the fallback propagate as
        :class:`exlab_wizard.errors.KeyringUnavailableError`.
        """
        try:
            value = keyring.get_password(KEYRING_SERVICE, username)
        except keyring.errors.KeyringError:
            return self._fallback_get(username)
        if value is not None:
            return value
        # Even when the keyring is healthy, an entry can be absent. For
        # the no-OS-keyring path the fallback file is still consulted so
        # a partially-migrated store does not appear empty.
        if self._secrets_path.exists():
            return self._fallback_get(username)
        return None

    def set_password(self, *, username: str, password: str) -> None:
        """Persist ``password`` under ``(KEYRING_SERVICE, username)``.

        On a keyring backend error the fallback file is written; on
        fallback failure this raises
        :class:`exlab_wizard.errors.KeyringUnavailableError`.
        """
        try:
            keyring.set_password(KEYRING_SERVICE, username, password)
            return
        except keyring.errors.KeyringError:
            logger.warning("keyring.fallback_engaged", extra={"op": "set"})
        self._fallback_set(username, password)

    def delete_password(self, *, username: str) -> None:
        """Remove the secret stored under ``(KEYRING_SERVICE, username)``.

        Missing entries are silent in both backends. Keyring errors
        defer to the fallback.
        """
        try:
            keyring.delete_password(KEYRING_SERVICE, username)
        except keyring.errors.PasswordDeleteError:
            # Entry was never set in the OS keyring. The fallback may
            # still hold it, so fall through.
            pass
        except keyring.errors.KeyringError:
            logger.warning("keyring.fallback_engaged", extra={"op": "delete"})
        if self._secrets_path.exists():
            self._fallback_delete(username)

    # ------------------------------------------------------------------
    # Fallback (encrypted-at-rest) implementation
    # ------------------------------------------------------------------

    def _fallback_get(self, username: str) -> str | None:
        if not self._secrets_path.exists():
            return None
        store = self._load_fallback()
        return store.get(username)

    def _fallback_set(self, username: str, password: str) -> None:
        store = self._load_fallback() if self._secrets_path.exists() else {}
        store[username] = password
        self._save_fallback(store)

    def _fallback_delete(self, username: str) -> None:
        if not self._secrets_path.exists():
            return
        store = self._load_fallback()
        if username in store:
            del store[username]
            self._save_fallback(store)

    def _passphrase(self) -> str:
        if self._passphrase_provider is None:
            msg = (
                "OS keyring is unavailable and no passphrase provider "
                "was supplied; cannot reach the encrypted fallback"
            )
            raise KeyringUnavailableError(msg)
        passphrase = self._passphrase_provider()
        if not passphrase:
            msg = "passphrase provider returned an empty value"
            raise KeyringUnavailableError(msg)
        return passphrase

    def _derive_key(self, passphrase: str, salt: bytes) -> bytes:
        raw = hash_secret_raw(
            secret=passphrase.encode("utf-8"),
            salt=salt,
            time_cost=_ARGON2_TIME_COST,
            memory_cost=_ARGON2_MEMORY_KIB,
            parallelism=_ARGON2_PARALLELISM,
            hash_len=_ARGON2_HASH_LEN,
            type=Type.ID,
        )
        return base64.urlsafe_b64encode(raw)

    def _load_fallback(self) -> dict[str, str]:
        try:
            raw = self._secrets_path.read_bytes()
        except OSError as exc:
            msg = f"could not read encrypted secrets file: {exc}"
            raise KeyringUnavailableError(msg) from exc
        envelope = self._decode_envelope(raw)
        salt = base64.b64decode(envelope["salt"])
        ciphertext = envelope["ciphertext"].encode("ascii")
        key = self._derive_key(self._passphrase(), salt)
        try:
            plaintext = Fernet(key).decrypt(ciphertext)
        except InvalidToken as exc:
            msg = "encrypted secrets file failed authentication (wrong passphrase?)"
            raise KeyringUnavailableError(msg) from exc
        decoded = msgspec.json.decode(plaintext)
        if not isinstance(decoded, dict):
            msg = "encrypted secrets file does not contain a JSON object"
            raise KeyringUnavailableError(msg)
        return {str(k): str(v) for k, v in decoded.items()}

    def _save_fallback(self, store: dict[str, str]) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        salt = self._existing_salt() or os.urandom(_ARGON2_SALT_BYTES)
        key = self._derive_key(self._passphrase(), salt)
        plaintext = msgspec.json.encode(store)
        ciphertext = Fernet(key).encrypt(plaintext).decode("ascii")
        envelope = {
            "version": _FALLBACK_FORMAT_VERSION,
            "salt": base64.b64encode(salt).decode("ascii"),
            "ciphertext": ciphertext,
        }
        encoded = msgspec.json.encode(envelope)
        tmp = self._secrets_path.with_name(f"{self._secrets_path.name}.tmp.{os.getpid()}")
        with tmp.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self._secrets_path)

    def _existing_salt(self) -> bytes | None:
        if not self._secrets_path.exists():
            return None
        try:
            raw = self._secrets_path.read_bytes()
        except OSError:
            return None
        try:
            envelope = self._decode_envelope(raw)
        except KeyringUnavailableError:
            return None
        return base64.b64decode(envelope["salt"])

    @staticmethod
    def _decode_envelope(raw: bytes) -> dict[str, Any]:
        try:
            decoded = msgspec.json.decode(raw)
        except msgspec.DecodeError as exc:
            msg = f"encrypted secrets file is not valid JSON: {exc}"
            raise KeyringUnavailableError(msg) from exc
        if not isinstance(decoded, dict):
            msg = "encrypted secrets file is not a JSON object"
            raise KeyringUnavailableError(msg)
        if decoded.get("version") != _FALLBACK_FORMAT_VERSION:
            msg = (
                f"encrypted secrets file version {decoded.get('version')!r} "
                f"is unsupported; expected {_FALLBACK_FORMAT_VERSION}"
            )
            raise KeyringUnavailableError(msg)
        if "salt" not in decoded or "ciphertext" not in decoded:
            msg = "encrypted secrets file missing salt or ciphertext"
            raise KeyringUnavailableError(msg)
        return decoded
