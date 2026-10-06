import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from jwt import PyJWTError
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from app.api.deps import (
    ensure_account_is_active,
    find_user_by_email,
    revoke_all_sessions,
    token_is_current,
    user_for_token,
)
from app.core import security
from app.core.config import settings
from app.core.rate_limit import limiter
from app.db.models import RevokedToken
from app.db.session import get_db
from app.schemas.token import LogoutRequest, RefreshRequest, TokenPair

logger = logging.getLogger(__name__)

router = APIRouter()

DUMMY_PASSWORD_HASH = "$2b$12$C6UzMDM.H6dfI/f/IKcEeOq6Yh6M7f5qX6Ch12yvDqOiiMHDL/95."


def _purge_expired_revoked_tokens(db: Session) -> int:
    """
    Delete revocation rows whose tokens have already expired.

    Once a token is past its expiry it is rejected by signature/exp validation
    anyway, so its revocation entry is no longer needed. Purging on each
    revocation event keeps the table bounded without a separate scheduler.
    For very high-volume deployments a periodic background job would be more
    efficient than purging inline.

    Returns the number of rows deleted.
    """
    now = datetime.now(timezone.utc)
    deleted = (
        db.query(RevokedToken)
        .filter(RevokedToken.expires_at < now)
        .delete(synchronize_session=False)
    )
    if deleted:
        db.commit()
    return deleted


def _expiry(exp: Optional[int]) -> datetime:
    return datetime.fromtimestamp(exp, tz=timezone.utc) if exp else datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """SQLite hands timestamps back without their zone; they are UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _revoke_once(
    db: Session, jti: str, expires_at: datetime, replaced_by: Optional[str] = None
) -> bool:
    """
    Add ``jti`` to the revocation list; False if it was already there.

    A single ``INSERT … ON CONFLICT DO NOTHING``, so of two requests revoking
    the same token exactly one wins. Reading first and inserting after let two
    simultaneous logouts both try the insert, and the second failed with a 500.
    """
    dialect = postgresql if db.get_bind().dialect.name == "postgresql" else sqlite
    statement = (
        dialect.insert(RevokedToken)
        .values(
            jti=jti,
            expires_at=expires_at,
            revoked_at=datetime.now(timezone.utc),
            replaced_by_jti=replaced_by,
        )
        .on_conflict_do_nothing(index_elements=["jti"])
    )
    return db.execute(statement).rowcount == 1


def _retry_within_grace(db: Session, jti: str, issued_jti: str) -> bool:
    """
    Whether an already rotated refresh token is a retry after a lost response.

    On a patchy network the server can rotate the token and the response never
    arrive; the device then retries with the token it still holds. That is a
    retry, not theft, when it comes soon after the rotation and the token
    issued then was never used. The token issued then is revoked in favour of
    the one issued now, so only one of them stays valid.
    """
    rotated = db.query(RevokedToken).filter(RevokedToken.jti == jti).one_or_none()
    if rotated is None or rotated.replaced_by_jti is None:
        return False  # revoked by logout, or before replacements were recorded
    grace = timedelta(seconds=settings.REFRESH_RETRY_GRACE_SECONDS)
    if datetime.now(timezone.utc) - _as_utc(rotated.revoked_at) > grace:
        return False
    previous_expiry = datetime.now(timezone.utc) + timedelta(
        minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES
    )
    if not _revoke_once(db, rotated.replaced_by_jti, previous_expiry, replaced_by=issued_jti):
        return False  # the token issued in its place was already used
    rotated.replaced_by_jti = issued_jti
    return True


@router.post("/login/access-token", response_model=TokenPair)
@limiter.limit(settings.RATE_LIMIT_LOGIN)
def login_access_token(
    request: Request,
    db: Session = Depends(get_db), 
    form_data: OAuth2PasswordRequestForm = Depends()
) -> Any:
    """
    OAuth2 compatible token login. Returns a signed JWT access token
    and a refresh token.

    Access tokens are short-lived (default 60 min).
    A refresh token (default 7 days) is returned alongside to allow
    seamless token renewal without re-authentication.

    Send credentials as form data (not JSON):
    - **username**: The user's registered email address.
    - **password**: The user's current password.

    **Responses:**
    - `200`: Login successful. Returns `access_token`, `refresh_token`,
             `token_type`, and `expires_in`.
    - `401`: Invalid email or password.
    - `401` with `code` `user_inactive` / `organization_inactive`: the account
      or its organization is deactivated (only after a correct password).
    - `422`: Missing required form fields.
    """
    # 1. Authenticate User
    user = find_user_by_email(db, form_data.username)
    hashed_password = user.hashed_password if user else DUMMY_PASSWORD_HASH
    password_is_valid = security.verify_password(form_data.password, hashed_password)

    if not user or not password_is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
        
    ensure_account_is_active(user, status.HTTP_401_UNAUTHORIZED)

    # 2. Create token pair
    access_token, refresh_token = security.token_pair_for(user)

    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        **security.nfc_key_claims(role=user.role, db=db),
    )


@router.post("/login/refresh", response_model=TokenPair)
def refresh_access_token(
    body: RefreshRequest,
    db: Session = Depends(get_db),
) -> Any:
    """
    Exchange a valid refresh token for a new access + refresh token pair.

    The old refresh token is revoked to prevent reuse (rotation).
    If a refresh token that was already rotated or revoked is presented again,
    someone else holds a copy: every session of that user is revoked (the
    legitimate device signs in again, the copy stops working).

    Except a retry after a lost response: a rotated token presented again
    within `REFRESH_RETRY_GRACE_SECONDS` (2 minutes), while the token issued
    in its place is still unused, gets a new pair. The token issued the first
    time is revoked in its favour, so only one refresh token stays valid.

    **Responses:**
    - `200`: New token pair returned.
    - `401`: Refresh token is invalid, expired, or revoked.
    - `401` with `code` `user_inactive` / `organization_inactive`: the account
      or its organization was deactivated.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = security.decode_token(body.refresh_token)
        token_type = payload.get("type")
        jti = payload.get("jti")
        exp = payload.get("exp")

        if not payload.get("sub") or token_type != "refresh" or not jti:
            raise credentials_exception

    except PyJWTError:
        raise credentials_exception

    # Verify the token still represents the account, and that the account is
    # active (and so is its organization)
    user = user_for_token(db, payload)
    if not user:
        raise credentials_exception
    ensure_account_is_active(user, status.HTTP_401_UNAUTHORIZED)
    if not token_is_current(user, payload):
        raise credentials_exception

    # Rotation: revoke the presented token, recording the one issued for it.
    new_access, new_refresh = security.token_pair_for(user)
    issued_jti = security.decode_token(new_refresh)["jti"]
    if not _revoke_once(db, jti, _expiry(exp), replaced_by=issued_jti):
        if _retry_within_grace(db, jti, issued_jti):
            logger.info("Refresh retried within the grace period jti=%s", jti[:8])
        else:
            # Someone else holds a copy: every session of the user ends.
            db.rollback()
            revoke_all_sessions(user)
            db.commit()
            logger.warning(
                "Revoked refresh token reuse attempt jti=%s; sessions revoked for user_id=%s",
                jti[:8],
                user.id,
            )
            raise credentials_exception
    db.commit()

    # Opportunistically drop revocation rows that are already expired.
    _purge_expired_revoked_tokens(db)

    return TokenPair(
        access_token=new_access,
        refresh_token=new_refresh,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        # Deliver the keyring on refresh too. The client does persist it, but
        # only for as long as this refresh token's window stays open, so a
        # refresh is what renews its right to hold the keys — and it picks up a
        # rotated current version without an extra /users/me round-trip.
        **security.nfc_key_claims(role=user.role, db=db),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    body: Optional[LogoutRequest] = None,
    db: Session = Depends(get_db),
):
    """
    Revoke the current session's tokens so they can no longer be used.

    The access token is taken from the `Authorization: Bearer` header and is
    always revoked. If the client also sends its refresh token in the body,
    that token is revoked as well — otherwise the refresh token would remain
    valid and could mint new access tokens after logout. Clients should send
    the refresh token to fully terminate the session.

    After logout, the revoked tokens are rejected by all endpoints.
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer token",
        )

    token = auth_header.split(" ", 1)[1]

    try:
        payload = security.decode_token(token)
        jti = payload.get("jti")
        exp = payload.get("exp")

        if not jti:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token missing JTI claim",
            )

    except PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    # Idempotent: a token revoked already (a double tap, a retry) is fine.
    _revoke_once(db, jti, _expiry(exp))
    db.commit()

    # Also revoke the refresh token when the client provides it, so the
    # session is fully terminated. Best-effort: a malformed or expired refresh
    # token must not fail the logout (the access token is already revoked).
    if body and body.refresh_token:
        try:
            refresh_payload = security.decode_token(body.refresh_token)
            refresh_jti = refresh_payload.get("jti")
            if refresh_jti and refresh_payload.get("type") == "refresh":
                _revoke_once(db, refresh_jti, _expiry(refresh_payload.get("exp")))
                db.commit()
        except PyJWTError:
            # Invalid/expired refresh token — nothing to revoke, ignore.
            pass

    # Opportunistically drop revocation rows that are already expired.
    _purge_expired_revoked_tokens(db)
