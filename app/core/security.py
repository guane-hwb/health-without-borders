import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

logger = logging.getLogger(__name__)

# Roles with no clinical access, and therefore no reason to hold NFC keys.
NFC_KEYLESS_ROLES = frozenset({"superadmin"})

# Password hashing context using bcrypt algorithm
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _create_token(
    subject: Union[str, Any],
    expires_delta: timedelta,
    token_type: str,
) -> str:
    """
    Internal helper: creates a signed JWT with a unique JTI claim
    for revocation support.

    Claims:
      - sub: user identifier (email)
      - exp: expiration timestamp
      - jti: unique token ID (UUID4) for revocation lookups
      - type: "access" or "refresh"
    """
    now = datetime.now(timezone.utc)
    expire = now + expires_delta

    to_encode = {
        "sub": str(subject),
        "exp": expire,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "type": token_type,
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(
    subject: Union[str, Any], expires_delta: Optional[timedelta] = None
) -> str:
    """
    Generates a short-lived access token (default: 60 min).
    """
    delta = expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return _create_token(subject, delta, token_type="access")


def create_refresh_token(
    subject: Union[str, Any], expires_delta: Optional[timedelta] = None
) -> str:
    """
    Generates a longer-lived refresh token (default: 7 days).
    Used to obtain a new access token without re-authenticating.
    """
    delta = expires_delta or timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES)
    return _create_token(subject, delta, token_type="refresh")


def decode_token(token: str) -> dict:
    """
    Decode and validate a JWT token, returning the full claims dict.
    Raises jose.JWTError on invalid/expired tokens.
    """
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Compares a raw string (e.g., "123456") against the stored hash.
    """
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """
    Generates a secure hash from a raw password.
    """
    return pwd_context.hash(password)


def nfc_key_claims(role: Optional[str] = None) -> dict[str, Any]:
    """
    Assemble the NFC key material handed to clients at login, refresh, and
    ``/users/me``.

    ``role`` gates delivery. A ``superadmin`` has no clinical access and never
    reads or writes a wristband, so it is served no key at all: these are the
    accounts most worth phishing, and there is no reason for a stolen one to
    come with the material that decrypts every tag in the field. Omitting
    ``role`` delivers the keyring, which keeps existing callers working.

    Returns a dict with three keys, matching the response schemas:
      - ``nfc_encryption_key``: the current version's hex key. Kept for
        backward compatibility with clients that predate key versioning.
      - ``nfc_key_version``: the integer version clients stamp into the payload
        header when writing a tag.
      - ``nfc_keyring``: ``{version_str: hex}`` for every live key, so a device
        can decrypt any tag still in circulation while offline and encrypt with
        the current one.

    When no key is configured every value is ``None``, preserving the previous
    behavior of omitting the key from the response. When the configured current
    version is missing from the keyring the writable fields are ``None`` (the
    client can still read via the keyring) and a warning is logged.
    """
    # ``role`` may arrive as a UserRole enum member or a plain string.
    role_value = getattr(role, "value", role)

    ring = settings.nfc_keyring()
    if role_value in NFC_KEYLESS_ROLES or not ring:
        return {
            "nfc_encryption_key": None,
            "nfc_key_version": None,
            "nfc_keyring": None,
        }

    current_version = settings.NFC_CURRENT_KEY_VERSION
    current_key = ring.get(current_version)
    if current_key is None:
        logger.warning(
            "NFC current key version %s is not present in the keyring "
            "(available versions: %s); clients can read but not write NFC tags.",
            current_version,
            sorted(ring.keys()),
        )

    return {
        "nfc_encryption_key": current_key,
        "nfc_key_version": current_version if current_key is not None else None,
        "nfc_keyring": {str(v): k for v, k in sorted(ring.items())},
    }
