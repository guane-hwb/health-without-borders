import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

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
