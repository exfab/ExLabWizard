"""Tests for :class:`exlab_wizard.lims.keyring_store.KeyringStore`.

The OS-keyring path is exercised against the mock-friendly
:mod:`keyring.backends.fail` and a custom in-memory backend; the
encrypted-fallback path is exercised end-to-end against the real
Argon2id KDF + Fernet primitives (no mocks -- the test is fast enough
because Argon2's memory cost is per-operation, not per-test-fixture).
"""

from __future__ import annotations

import base64
import contextlib

import keyring
import keyring.backend
import keyring.errors
import msgspec
import pytest

from exlab_wizard.errors import KeyringUnavailableError
from exlab_wizard.lims.keyring_store import KeyringStore


class _InMemoryKeyring(keyring.backend.KeyringBackend):
    """Trivial in-memory keyring backend used to drive the OS-keyring path."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):  # type: ignore[override]
        return self._store.get((service, username))

    def set_password(self, service, username, password):  # type: ignore[override]
        self._store[(service, username)] = password

    def delete_password(self, service, username):  # type: ignore[override]
        if (service, username) not in self._store:
            raise keyring.errors.PasswordDeleteError("missing")
        del self._store[(service, username)]


class _BrokenKeyring(keyring.backend.KeyringBackend):
    """Keyring backend that raises on every operation."""

    priority = 1  # type: ignore[assignment]

    def get_password(self, service, username):  # type: ignore[override]
        raise keyring.errors.KeyringError("broken")

    def set_password(self, service, username, password):  # type: ignore[override]
        raise keyring.errors.KeyringError("broken")

    def delete_password(self, service, username):  # type: ignore[override]
        raise keyring.errors.KeyringError("broken")


@contextlib.contextmanager
def _swap_keyring(backend):
    previous = keyring.get_keyring()
    keyring.set_keyring(backend)
    try:
        yield
    finally:
        keyring.set_keyring(previous)


def test_set_get_delete_via_os_keyring(tmp_path) -> None:
    with _swap_keyring(_InMemoryKeyring()):
        store = KeyringStore(state_dir=tmp_path)
        store.set_password(username="lims", password="hunter2")
        assert store.get_password(username="lims") == "hunter2"
        store.delete_password(username="lims")
        assert store.get_password(username="lims") is None


def test_get_returns_none_for_missing(tmp_path) -> None:
    with _swap_keyring(_InMemoryKeyring()):
        store = KeyringStore(state_dir=tmp_path)
        assert store.get_password(username="missing") is None


def test_delete_missing_is_silent(tmp_path) -> None:
    with _swap_keyring(_InMemoryKeyring()):
        store = KeyringStore(state_dir=tmp_path)
        # Per the keyring API, deleting a missing entry raises
        # PasswordDeleteError; KeyringStore must swallow it.
        store.delete_password(username="missing")


def test_is_keyring_available_true(tmp_path) -> None:
    with _swap_keyring(_InMemoryKeyring()):
        store = KeyringStore(state_dir=tmp_path)
        assert store.is_keyring_available() is True


def test_is_keyring_available_false_on_broken_backend(tmp_path) -> None:
    with _swap_keyring(_BrokenKeyring()):
        store = KeyringStore(state_dir=tmp_path)
        assert store.is_keyring_available() is False


def test_fallback_round_trip(tmp_path) -> None:
    """Set + get via the encrypted fallback (no OS keyring)."""
    with _swap_keyring(_BrokenKeyring()):
        store = KeyringStore(
            state_dir=tmp_path,
            passphrase_provider=lambda: "master-passphrase",
        )
        store.set_password(username="lims", password="hunter2")
        assert (tmp_path / "secrets.enc").exists()
        # A fresh store reading the same file recovers the secret.
        store2 = KeyringStore(
            state_dir=tmp_path,
            passphrase_provider=lambda: "master-passphrase",
        )
        assert store2.get_password(username="lims") == "hunter2"


def test_fallback_delete(tmp_path) -> None:
    with _swap_keyring(_BrokenKeyring()):
        store = KeyringStore(
            state_dir=tmp_path,
            passphrase_provider=lambda: "master-passphrase",
        )
        store.set_password(username="lims", password="hunter2")
        store.delete_password(username="lims")
        # Read with a fresh instance: secret must be absent.
        store2 = KeyringStore(
            state_dir=tmp_path,
            passphrase_provider=lambda: "master-passphrase",
        )
        assert store2.get_password(username="lims") is None


def test_fallback_wrong_passphrase_raises(tmp_path) -> None:
    with _swap_keyring(_BrokenKeyring()):
        store = KeyringStore(
            state_dir=tmp_path,
            passphrase_provider=lambda: "master-passphrase",
        )
        store.set_password(username="lims", password="hunter2")
    # Re-open with a wrong passphrase; the OS keyring is already broken.
    with _swap_keyring(_BrokenKeyring()):
        store2 = KeyringStore(
            state_dir=tmp_path,
            passphrase_provider=lambda: "WRONG",
        )
        with pytest.raises(KeyringUnavailableError):
            store2.get_password(username="lims")


def test_fallback_no_passphrase_raises(tmp_path) -> None:
    with _swap_keyring(_BrokenKeyring()):
        store = KeyringStore(state_dir=tmp_path)
        with pytest.raises(KeyringUnavailableError):
            store.set_password(username="lims", password="x")


def test_fallback_empty_passphrase_raises(tmp_path) -> None:
    with _swap_keyring(_BrokenKeyring()):
        store = KeyringStore(state_dir=tmp_path, passphrase_provider=lambda: "")
        with pytest.raises(KeyringUnavailableError):
            store.set_password(username="lims", password="x")


def test_fallback_file_format_integrity(tmp_path) -> None:
    """The fallback file is a JSON envelope with version, salt, ciphertext."""
    with _swap_keyring(_BrokenKeyring()):
        store = KeyringStore(
            state_dir=tmp_path,
            passphrase_provider=lambda: "master-passphrase",
        )
        store.set_password(username="lims", password="hunter2")
    decoded = msgspec.json.decode((tmp_path / "secrets.enc").read_bytes())
    assert decoded["version"] == 1
    assert "salt" in decoded
    assert "ciphertext" in decoded
    base64.b64decode(decoded["salt"])  # round-trip-safe base64
    assert isinstance(decoded["ciphertext"], str) and len(decoded["ciphertext"]) > 0


def test_fallback_corrupt_file_raises(tmp_path) -> None:
    secrets_path = tmp_path / "secrets.enc"
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    secrets_path.write_bytes(b"garbage")
    with _swap_keyring(_BrokenKeyring()):
        store = KeyringStore(
            state_dir=tmp_path,
            passphrase_provider=lambda: "master-passphrase",
        )
        with pytest.raises(KeyringUnavailableError):
            store.get_password(username="lims")


def test_fallback_unsupported_version_raises(tmp_path) -> None:
    secrets_path = tmp_path / "secrets.enc"
    secrets_path.write_bytes(
        msgspec.json.encode({"version": 99, "salt": "AA==", "ciphertext": "x"})
    )
    with _swap_keyring(_BrokenKeyring()):
        store = KeyringStore(
            state_dir=tmp_path,
            passphrase_provider=lambda: "master-passphrase",
        )
        with pytest.raises(KeyringUnavailableError, match="version"):
            store.get_password(username="lims")


def test_fallback_missing_fields_raises(tmp_path) -> None:
    secrets_path = tmp_path / "secrets.enc"
    secrets_path.write_bytes(msgspec.json.encode({"version": 1}))
    with _swap_keyring(_BrokenKeyring()):
        store = KeyringStore(
            state_dir=tmp_path,
            passphrase_provider=lambda: "master-passphrase",
        )
        with pytest.raises(KeyringUnavailableError):
            store.get_password(username="lims")


def test_fallback_set_then_overwrite(tmp_path) -> None:
    """A second ``set_password`` overwrites the prior secret."""
    with _swap_keyring(_BrokenKeyring()):
        store = KeyringStore(
            state_dir=tmp_path,
            passphrase_provider=lambda: "master-passphrase",
        )
        store.set_password(username="lims", password="first")
        store.set_password(username="lims", password="second")
        assert store.get_password(username="lims") == "second"


def test_keyring_set_failure_falls_back(tmp_path) -> None:
    """When the OS keyring set fails, the encrypted-at-rest write is
    used and a subsequent get reads it back.
    """
    with _swap_keyring(_BrokenKeyring()):
        store = KeyringStore(
            state_dir=tmp_path,
            passphrase_provider=lambda: "master-passphrase",
        )
        store.set_password(username="lims", password="value")
        assert store.get_password(username="lims") == "value"


def test_is_keyring_available_false_on_unexpected_exception(tmp_path) -> None:
    """A backend raising a non-KeyringError is also treated as unavailable."""

    class _ExplodingKeyring(keyring.backend.KeyringBackend):
        priority = 1  # type: ignore[assignment]

        def get_password(self, service, username):  # type: ignore[override]
            raise RuntimeError("explode")

        def set_password(self, service, username, password):  # type: ignore[override]
            raise RuntimeError("explode")

        def delete_password(self, service, username):  # type: ignore[override]
            raise RuntimeError("explode")

    with _swap_keyring(_ExplodingKeyring()):
        store = KeyringStore(state_dir=tmp_path)
        assert store.is_keyring_available() is False


def test_fallback_get_when_no_secrets_file(tmp_path) -> None:
    """``get_password`` returns None (not error) when keyring is broken
    and the fallback file doesn't exist.
    """
    with _swap_keyring(_BrokenKeyring()):
        store = KeyringStore(
            state_dir=tmp_path,
            passphrase_provider=lambda: "master-passphrase",
        )
        assert store.get_password(username="lims") is None


def test_fallback_delete_when_no_file(tmp_path) -> None:
    """Deleting against a broken keyring with no fallback file is silent."""
    with _swap_keyring(_BrokenKeyring()):
        store = KeyringStore(
            state_dir=tmp_path,
            passphrase_provider=lambda: "master-passphrase",
        )
        # No file has been created yet; this must not raise.
        store.delete_password(username="missing")


def test_fallback_delete_when_username_absent(tmp_path) -> None:
    """Deleting a missing username in the fallback file is silent."""
    with _swap_keyring(_BrokenKeyring()):
        store = KeyringStore(
            state_dir=tmp_path,
            passphrase_provider=lambda: "master-passphrase",
        )
        store.set_password(username="lims", password="x")
        # Delete a different username; the file persists but the lims
        # entry remains.
        store.delete_password(username="other")
        assert store.get_password(username="lims") == "x"


def test_load_fallback_unreadable_path_raises(tmp_path, monkeypatch) -> None:
    """An OSError during fallback read surfaces as KeyringUnavailableError."""
    secrets_path = tmp_path / "secrets.enc"
    secrets_path.write_bytes(b"placeholder")

    def _always_oserror(self):
        raise OSError("locked")

    monkeypatch.setattr(type(secrets_path), "read_bytes", _always_oserror, raising=False)
    with _swap_keyring(_BrokenKeyring()):
        store = KeyringStore(
            state_dir=tmp_path,
            passphrase_provider=lambda: "passphrase",
        )
        with pytest.raises(KeyringUnavailableError):
            store.get_password(username="lims")


def test_fallback_plaintext_must_be_object(tmp_path) -> None:
    """If the decrypted plaintext is a non-object JSON value, the read raises."""
    # Build a valid envelope around a non-dict plaintext payload.
    import os

    from argon2.low_level import Type, hash_secret_raw
    from cryptography.fernet import Fernet

    salt = os.urandom(16)
    raw = hash_secret_raw(
        secret=b"passphrase",
        salt=salt,
        time_cost=3,
        memory_cost=64 * 1024,
        parallelism=4,
        hash_len=32,
        type=Type.ID,
    )
    key = base64.urlsafe_b64encode(raw)
    ciphertext = Fernet(key).encrypt(b"[1, 2, 3]").decode("ascii")
    envelope = {
        "version": 1,
        "salt": base64.b64encode(salt).decode("ascii"),
        "ciphertext": ciphertext,
    }
    secrets_path = tmp_path / "secrets.enc"
    secrets_path.write_bytes(msgspec.json.encode(envelope))
    with _swap_keyring(_BrokenKeyring()):
        store = KeyringStore(
            state_dir=tmp_path,
            passphrase_provider=lambda: "passphrase",
        )
        with pytest.raises(KeyringUnavailableError, match="JSON object"):
            store.get_password(username="lims")


def test_envelope_must_be_object(tmp_path) -> None:
    """A JSON list at the envelope layer is rejected as malformed."""
    secrets_path = tmp_path / "secrets.enc"
    secrets_path.write_bytes(msgspec.json.encode([1, 2, 3]))
    with _swap_keyring(_BrokenKeyring()):
        store = KeyringStore(
            state_dir=tmp_path,
            passphrase_provider=lambda: "passphrase",
        )
        with pytest.raises(KeyringUnavailableError, match="JSON object"):
            store.get_password(username="lims")


def test_get_consults_fallback_when_keyring_missing(tmp_path) -> None:
    """If the OS keyring returns None but the fallback file exists, the
    fallback is consulted.
    """
    # First, populate the fallback file via a broken backend.
    with _swap_keyring(_BrokenKeyring()):
        broken_store = KeyringStore(
            state_dir=tmp_path,
            passphrase_provider=lambda: "master-passphrase",
        )
        broken_store.set_password(username="lims", password="from-fallback")
    # Now switch to a healthy but empty backend and confirm we still
    # find the value via fallback.
    with _swap_keyring(_InMemoryKeyring()):
        store = KeyringStore(
            state_dir=tmp_path,
            passphrase_provider=lambda: "master-passphrase",
        )
        assert store.get_password(username="lims") == "from-fallback"
