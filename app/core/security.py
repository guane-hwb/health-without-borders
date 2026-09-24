import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union

import jwt
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
    token_version: Optional[int] = None,
) -> str:
    """
    Internal helper: creates a signed JWT with a unique JTI claim
    for revocation support.

    Claims:
      - sub: user id (tokens issued before September 2026 carry the email)
      - exp: expiration timestamp
      - jti: unique token ID (UUID4) for revocation lookups
      - type: "access" or "refresh"
      - tv: the user's token_version; bumping it revokes every older token
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
    if token_version is not None:
        to_encode["tv"] = token_version
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(
    subject: Union[str, Any],
    expires_delta: Optional[timedelta] = None,
    token_version: Optional[int] = None,
) -> str:
    """
    Generates a short-lived access token (default: 60 min).
    """
    delta = expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return _create_token(subject, delta, token_type="access", token_version=token_version)


def create_refresh_token(
    subject: Union[str, Any],
    expires_delta: Optional[timedelta] = None,
    token_version: Optional[int] = None,
) -> str:
    """
    Generates a longer-lived refresh token (default: 7 days).
    Used to obtain a new access token without re-authenticating.
    """
    delta = expires_delta or timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES)
    return _create_token(subject, delta, token_type="refresh", token_version=token_version)


def token_pair_for(user: Any) -> tuple[str, str]:
    """Access and refresh tokens bound to the user's id and token_version."""
    version = user.token_version or 0
    return (
        create_access_token(subject=user.id, token_version=version),
        create_refresh_token(subject=user.id, token_version=version),
    )


def decode_token(token: str) -> dict:
    """
    Decode and validate a JWT token, returning the full claims dict.
    Raises jwt.PyJWTError on invalid/expired tokens.

    ``exp`` is mandatory: a signed token without it would never expire.
    """
    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[settings.ALGORITHM],
        options={"require": ["exp"]},
    )


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


def nfc_key_claims(role: Optional[str] = None, db: Optional[Any] = None) -> dict[str, Any]:
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

    ``db`` lets the ring come from the database, which is what makes revoking a
    version take effect without a redeploy. Without a session, or without a KEK
    configured, the ring is read from the environment exactly as before.
    """
    # ``role`` may arrive as a UserRole enum member or a plain string.
    role_value = getattr(role, "value", role)

    if role_value in NFC_KEYLESS_ROLES:
        return {
            "nfc_encryption_key": None,
            "nfc_key_version": None,
            "nfc_keyring": None,
        }

    ring, current_version = _resolve_keyring(db)
    if not ring:
        return {
            "nfc_encryption_key": None,
            "nfc_key_version": None,
            "nfc_keyring": None,
        }

    current_key = ring.get(current_version) if current_version is not None else None
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


def _resolve_keyring(db: Optional[Any]) -> tuple[dict[int, str], Optional[int]]:
    """
    The live ring and the version new writes use.

    Prefers the database when a KEK is configured and a session is available,
    because that is the copy an operator can change at runtime. Without a KEK
    the environment is the only source, exactly as before.

    With a KEK, the environment is never used: after a revocation it still
    holds the revoked version 0 (NFC_MASTER_KEY), and handing that out as the
    whole ring would make devices write with a leaked key and lose the live
    ones. If the database read fails, the last ring read from it is served;
    if there is none, no keys are — the app keeps the ring it already has.
    """
    kek_mode = bool(settings.NFC_KEK.strip())
    if db is not None or kek_mode:
        from app.services.nfc_key_service import last_known_keyring, load_keyring

        try:
            stored = load_keyring(db) if db is not None else None
            if stored is not None:
                return stored["keys"], stored["current"]
        except Exception:
            # Never let a keyring lookup break authentication.
            logger.error("Could not read the NFC keyring from the database.")
            db.rollback()
        if kek_mode:
            fallback = last_known_keyring()
            if fallback is not None:
                logger.warning("Serving the last NFC keyring read from the database.")
                return fallback["keys"], fallback["current"]
            return {}, None

    return settings.nfc_keyring(), settings.NFC_CURRENT_KEY_VERSION
