"""
Role: Authenticated at-rest encryption for connector credentials (e.g. IMAP app passwords).
      AES-256-GCM via the `cryptography` library: confidentiality + tamper detection, with a
      per-encryption random nonce.
Used by: connectors.services.connector_service (encrypt on save / decrypt on connection test);
         constructed in connectors.dependencies from settings.connector_secret_key.
Depends on: cryptography (external), app.connectors.exceptions, app.core.config (the key value
            only — read via the caller, not imported here, to keep core->connectors direction).
Key invariants:
  - FAIL-CLOSED, LAZILY: when require_secure is True (every non-dev env), construction refuses
    the dev-default key or any key material under 32 chars — so a misconfigured prod fails the
    moment a connector is used, while a connector-less deployment still boots (the boot guard in
    core.config deliberately does NOT check this key).
  - The plaintext secret is NEVER logged and never appears in an exception message.
  - Ciphertext layout is nonce(12 bytes) || AES-GCM(ciphertext+tag); decrypt authenticates, so a
    tampered or wrong-key blob raises ConnectorSecretError rather than returning garbage.
  - key_version is stamped onto every stored row (ConnectorConnection.secret_key_version) so an
    app-wide key rotation can later tell which rows used which key.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.connectors.exceptions import ConnectorConfigurationError, ConnectorSecretError

# Must equal the dev default of Settings.connector_secret_key in app.core.config. Rejected in
# any env that requires secure secrets (see require_secure below).
_INSECURE_CONNECTOR_KEY = "dev-only-insecure-connector-secret-change-me"
_MIN_KEY_MATERIAL_CHARS = 32
_NONCE_BYTES = 12
# Bump when the key-derivation / cipher scheme changes; stored alongside each ciphertext.
SECRET_KEY_VERSION = 1


class CredentialCipher:
    """Encrypts/decrypts connector secrets with AES-256-GCM under a process-wide key."""

    def __init__(self, key_material: str, *, require_secure: bool) -> None:
        """Derive the AES key from `key_material`, failing closed on a weak key in non-dev.

        Args:
            key_material: a base64-encoded 32-byte key (preferred) or a passphrase; a passphrase
                is hashed to 32 bytes. Supply a strong value in prod (`openssl rand -base64 32`).
            require_secure: when True (every non-dev env), reject the dev default and any
                material under 32 chars with ConnectorConfigurationError (-> 503).
        """
        cleaned = key_material.strip()
        if require_secure and (
            cleaned == _INSECURE_CONNECTOR_KEY or len(cleaned) < _MIN_KEY_MATERIAL_CHARS
        ):
            raise ConnectorConfigurationError(
                "CONNECTOR_SECRET_KEY is the dev default or too weak for this environment. "
                "Provide a strong key (e.g. `openssl rand -base64 32`) via environment / "
                "secret manager."
            )
        self._key = self._derive_key(cleaned)
        self.key_version = SECRET_KEY_VERSION

    @staticmethod
    def _derive_key(material: str) -> bytes:
        """Return a 32-byte AES key: a base64-encoded 32-byte value verbatim, else SHA-256."""
        try:
            decoded = base64.b64decode(material, validate=True)
            if len(decoded) == 32:
                return decoded
        except (binascii.Error, ValueError):
            pass
        return hashlib.sha256(material.encode("utf-8")).digest()

    def encrypt(self, plaintext: str) -> bytes:
        """Encrypt `plaintext`, returning nonce || ciphertext+tag (safe to store as BYTEA)."""
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(self._key).encrypt(nonce, plaintext.encode("utf-8"), None)
        return nonce + ciphertext

    def decrypt(self, blob: bytes) -> str:
        """Authenticate + decrypt a nonce||ciphertext blob back to the plaintext secret.

        Raises:
            ConnectorSecretError: the blob is malformed, tampered, or encrypted under a
                different key (authentication failed).
        """
        if len(blob) <= _NONCE_BYTES:
            raise ConnectorSecretError("Stored credential is malformed.")
        nonce, ciphertext = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        try:
            return AESGCM(self._key).decrypt(nonce, ciphertext, None).decode("utf-8")
        except (InvalidTag, ValueError) as exc:
            raise ConnectorSecretError("Stored credential could not be decrypted.") from exc
