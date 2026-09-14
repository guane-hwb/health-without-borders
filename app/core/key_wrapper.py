"""
Wrapping and unwrapping of NFC key material.

NFC keys live in the database so they can be created and revoked without a
redeploy — a variable in the environment cannot be written at runtime, which is
the whole reason rotation and revocation used to require one. Storing them in
the clear would be a poor trade: database backups are copied to far more places
than the live database. So the rows hold *wrapped* keys, and a single static
secret in the environment unwraps them.

That secret is the KEK (Key Encryption Key). Its only job is to encrypt other
keys; it never touches patient data.

The wrapper is an interface with one implementation today. A deployment on GCP
may later prefer Cloud KMS, and an organisation self-hosting elsewhere needs the
environment variable — the choice should not be baked into the callers.
"""

import base64
import hashlib
import os
import secrets
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

#: Length of an AES-256 key in bytes.
KEY_LENGTH = 32

#: AES-GCM nonce length in bytes.
_NONCE_LENGTH = 12


class KeyWrapperError(RuntimeError):
    """Raised when a key cannot be wrapped or unwrapped."""


class KeyWrapper(Protocol):
    """Wraps and unwraps NFC key material."""

    @property
    def kek_id(self) -> str:
        """
        Fingerprint of the wrapping secret.

        Stored alongside every wrapped row. Without it, replacing the KEK later
        means guessing which rows were wrapped with which secret; with it, the
        migration is a row-by-row re-wrap.
        """
        ...

    def wrap(self, key: bytes, version: int) -> str: ...

    def unwrap(self, wrapped: str, version: int) -> bytes: ...


class EnvKekWrapper:
    """
    Wraps keys with AES-256-GCM using a KEK held in the environment.

    The key version is authenticated as associated data, so a wrapped row
    cannot be moved to a different version: unwrapping it under the wrong
    version fails rather than silently yielding a key that would then encrypt
    chips nobody can read.
    """

    def __init__(self, kek_hex: str) -> None:
        try:
            kek = bytes.fromhex(kek_hex.strip())
        except ValueError as exc:
            raise KeyWrapperError(
                "NFC_KEK must be hexadecimal."
            ) from exc
        if len(kek) != KEY_LENGTH:
            raise KeyWrapperError(
                f"NFC_KEK must be exactly {KEY_LENGTH} bytes "
                f"({KEY_LENGTH * 2} hex characters)."
            )
        self._aesgcm = AESGCM(kek)
        # A fingerprint, not the secret: safe to store and to log.
        self._kek_id = hashlib.sha256(kek).hexdigest()[:16]

    @property
    def kek_id(self) -> str:
        return self._kek_id

    def wrap(self, key: bytes, version: int) -> str:
        nonce = os.urandom(_NONCE_LENGTH)
        sealed = self._aesgcm.encrypt(nonce, key, _aad(version))
        return base64.b64encode(nonce + sealed).decode("ascii")

    def unwrap(self, wrapped: str, version: int) -> bytes:
        try:
            raw = base64.b64decode(wrapped, validate=True)
        except (ValueError, TypeError) as exc:
            raise KeyWrapperError(
                f"Wrapped key for version {version} is not valid base64."
            ) from exc

        if len(raw) <= _NONCE_LENGTH:
            raise KeyWrapperError(
                f"Wrapped key for version {version} is too short."
            )

        try:
            return self._aesgcm.decrypt(
                raw[:_NONCE_LENGTH], raw[_NONCE_LENGTH:], _aad(version)
            )
        except Exception as exc:
            # Either the KEK is wrong or the row was tampered with. Both mean
            # this key cannot be served, and neither should leak detail.
            raise KeyWrapperError(
                f"Could not unwrap key version {version}: wrong KEK or "
                "corrupted row."
            ) from exc


def generate_key() -> bytes:
    """A fresh AES-256 key."""
    return secrets.token_bytes(KEY_LENGTH)


def _aad(version: int) -> bytes:
    return f"nfc-key-v{version}".encode("ascii")
