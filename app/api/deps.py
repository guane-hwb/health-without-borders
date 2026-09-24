
from datetime import timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt import PyJWTError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ORGANIZATION_INACTIVE, USER_INACTIVE, ApiError
from app.core.security import decode_token
from app.db.models import Organization, RevokedToken, User
from app.db.session import get_db

# Define where the frontend goes to get the token (the login URL)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/login/access-token")


def get_current_user(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme)
) -> User:
    """
    Dependency that validates the JWT Token sent in the Authorization header.
    
    Token validation rules:
      - Verifies token type is "access" (rejects refresh tokens).
      - Checks the JTI against the revocation list.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = decode_token(token)
        token_type: str = payload.get("type", "access")
        jti: str = payload.get("jti", "")

        if payload.get("sub") is None:
            raise credentials_exception

        # Only access tokens are valid for API endpoints
        if token_type != "access":
            raise credentials_exception

    except PyJWTError:
        raise credentials_exception

    # Check token revocation
    if jti:
        revoked = db.query(RevokedToken).filter(
            RevokedToken.jti == jti
        ).first()
        if revoked:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )
    
    user = user_for_token(db, payload)
    if user is None:
        raise credentials_exception
    
    # Deactivation first, so the app gets the user_inactive /
    # organization_inactive code rather than a bare 401 for a revoked token.
    ensure_account_is_active(user, status.HTTP_403_FORBIDDEN)
    if not token_is_current(user, payload):
        raise credentials_exception
    return user


def find_user_by_email(db: Session, email: str) -> Optional[User]:
    """Email lookup that ignores letter case (``Maria@org.org`` == ``maria@org.org``)."""
    return (
        db.query(User)
        .filter(func.lower(User.email) == (email or "").strip().lower())
        .first()
    )


def user_for_token(db: Session, payload: dict) -> Optional[User]:
    """
    The account a decoded token was issued to, or None.

    ``sub`` is the user id. Tokens issued before September 2026 carry the
    email instead; those are only honoured if issued after the account was
    created, so a deleted account re-created with the same email does not
    inherit its predecessor's tokens. Whether the token is still valid for
    that account is ``token_is_current``.
    """
    subject = payload.get("sub")
    if not subject:
        return None
    if "@" in subject:
        user = find_user_by_email(db, subject)
        issued_at = payload.get("iat")
        if user is not None and user.created_at is not None and issued_at is not None:
            created = user.created_at
            if created.tzinfo is None:  # SQLite returns naive values
                created = created.replace(tzinfo=timezone.utc)
            if issued_at < int(created.timestamp()):
                return None
    else:
        user = db.query(User).filter(User.id == subject).first()
    return user


def token_is_current(user: User, payload: dict) -> bool:
    """
    ``tv`` must equal the user's current ``token_version`` (tokens without it
    count as version 0). Deactivation, revoke-sessions and refresh-token reuse
    bump the version, which revokes every earlier token at once.
    """
    return payload.get("tv", 0) == (user.token_version or 0)


def revoke_all_sessions(user: User) -> None:
    """Invalidate every token issued to ``user`` so far (caller commits)."""
    user.token_version = (user.token_version or 0) + 1


def ensure_account_is_active(user: User, status_code: int) -> None:
    """
    Reject a user who is deactivated or whose organization is deactivated.

    Deactivating an organization is how the platform retires it, and patients
    are global: without this check its staff kept reading every record and
    receiving the NFC keyring. It runs on every authenticated request, on login
    and on refresh, so a deactivation takes effect immediately.

    ``status_code`` is 403 for authenticated requests (the app keeps its pending
    records and retries later) and 401 for login/refresh (the app signs out).
    """
    if not user.is_active:
        raise ApiError(status_code, "Inactive user", code=USER_INACTIVE)
    organization = user.organization
    if isinstance(organization, Organization) and not organization.is_active:
        raise ApiError(status_code, "Organization is inactive.", code=ORGANIZATION_INACTIVE)
