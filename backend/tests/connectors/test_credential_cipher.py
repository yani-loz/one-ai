"""Unit tests for CredentialCipher — AES-256-GCM round-trip, tamper/key detection, fail-closed."""

from __future__ import annotations

import base64
import os

import pytest

from app.connectors.exceptions import ConnectorConfigurationError, ConnectorSecretError
from app.connectors.security.credential_cipher import CredentialCipher

_DEV_DEFAULT = "dev-only-insecure-connector-secret-change-me"
_STRONG_KEY = "a-strong-random-enough-passphrase-0123456789"


def test_round_trip_returns_original_plaintext() -> None:
    cipher = CredentialCipher(_STRONG_KEY, require_secure=False)

    blob = cipher.encrypt("imap-app-password-hunter2")

    assert cipher.decrypt(blob) == "imap-app-password-hunter2"


def test_encrypt_uses_a_fresh_nonce_each_call() -> None:
    cipher = CredentialCipher(_STRONG_KEY, require_secure=False)

    assert cipher.encrypt("same-secret") != cipher.encrypt("same-secret")


def test_decrypt_tampered_blob_raises_secret_error() -> None:
    cipher = CredentialCipher(_STRONG_KEY, require_secure=False)
    blob = bytearray(cipher.encrypt("secret"))

    blob[-1] ^= 0x01  # flip a ciphertext bit -> GCM auth must fail

    with pytest.raises(ConnectorSecretError):
        cipher.decrypt(bytes(blob))


def test_decrypt_with_a_different_key_raises_secret_error() -> None:
    writer = CredentialCipher(_STRONG_KEY, require_secure=False)
    other = CredentialCipher("a-completely-different-passphrase-9876543210", require_secure=False)

    with pytest.raises(ConnectorSecretError):
        other.decrypt(writer.encrypt("secret"))


def test_decrypt_truncated_blob_raises_secret_error() -> None:
    cipher = CredentialCipher(_STRONG_KEY, require_secure=False)

    with pytest.raises(ConnectorSecretError):
        cipher.decrypt(b"short")


def test_dev_default_key_rejected_when_secure_required() -> None:
    with pytest.raises(ConnectorConfigurationError):
        CredentialCipher(_DEV_DEFAULT, require_secure=True)


def test_short_key_rejected_when_secure_required() -> None:
    with pytest.raises(ConnectorConfigurationError):
        CredentialCipher("too-short", require_secure=True)


def test_strong_passphrase_accepted_when_secure_required() -> None:
    cipher = CredentialCipher(_STRONG_KEY, require_secure=True)

    assert cipher.decrypt(cipher.encrypt("x")) == "x"


def test_base64_32_byte_key_used_directly() -> None:
    key = base64.b64encode(os.urandom(32)).decode("ascii")
    cipher = CredentialCipher(key, require_secure=True)

    assert cipher.decrypt(cipher.encrypt("x")) == "x"
