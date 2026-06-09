import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import settings
from app.core.rate_limit import limiter
from app.db.models import RevokedToken, User
from app.db.session import get_db
from app.schemas.token import RefreshRequest, TokenPair

logger = logging.getLogger(__name__)

router = APIRouter()

DUMMY_PASSWORD_HASH = "$2b$12$C6UzMDM.H6dfI/f/IKcEeOq6Yh6M7f5qX6Ch12yvDqOiiMHDL/95."


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

    H4: Access tokens are now short-lived (default 60 min).
    A refresh token (default 7 days) is returned alongside to allow
    seamless token renewal without re-authentication.

    Send credentials as form data (not JSON):
    - **username**: The user's registered email address.
    - **password**: The user's current password.

    **Responses:**
    - `200`: Login successful. Returns `access_token`, `refresh_token`,
             `token_type`, and `expires_in`.
    - `400`: Account exists but is deactivated.
    - `401`: Invalid email or password.
    - `422`: Missing required form fields.
    """
    # 1. Authenticate User
    user = db.query(User).filter(User.email == form_data.username).first()
    hashed_password = user.hashed_password if user else DUMMY_PASSWORD_HASH
    password_is_valid = security.verify_password(form_data.password, hashed_password)

    if not user or not password_is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
        
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user")

    # 2. Create token pair
    access_token = security.create_access_token(subject=user.email)
    refresh_token = security.create_refresh_token(subject=user.email)

    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/login/refresh", response_model=TokenPair)
def refresh_access_token(
    body: RefreshRequest,
    db: Session = Depends(get_db),
) -> Any:
    """
    Exchange a valid refresh token for a new access + refresh token pair.

    The old refresh token is revoked to prevent reuse (rotation).
    If the refresh token has already been revoked, all tokens for the
    user should be considered compromised (but for now we just reject).

    **Responses:**
    - `200`: New token pair returned.
    - `401`: Refresh token is invalid, expired, or revoked.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = security.decode_token(body.refresh_token)
        email = payload.get("sub")
        token_type = payload.get("type")
        jti = payload.get("jti")
        exp = payload.get("exp")

        if not email or token_type != "refresh" or not jti:
            raise credentials_exception

    except JWTError:
        raise credentials_exception

    # Check if the refresh token has been revoked
    revoked = db.query(RevokedToken).filter(RevokedToken.jti == jti).first()
    if revoked:
        logger.warning(
            "Revoked refresh token reuse attempt for user=%s jti=%s",
            email, jti[:8],
        )
        raise credentials_exception

    # Verify user still exists and is active
    user = db.query(User).filter(User.email == email).first()
    if not user or not user.is_active:
        raise credentials_exception

    # Revoke the old refresh token (rotation)
    expires_at = datetime.fromtimestamp(exp, tz=timezone.utc) if exp else datetime.now(timezone.utc)
    db.add(RevokedToken(jti=jti, expires_at=expires_at))
    db.commit()

    # Issue new token pair
    new_access = security.create_access_token(subject=user.email)
    new_refresh = security.create_refresh_token(subject=user.email)

    return TokenPair(
        access_token=new_access,
        refresh_token=new_refresh,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Revoke the current access token so it can no longer be used.

    The client should send the same Bearer token it uses for API calls.
    After logout, the token will be rejected by all endpoints.
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

    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    # Idempotent: if already revoked, just return success
    existing = db.query(RevokedToken).filter(RevokedToken.jti == jti).first()
    if not existing:
        expires_at = datetime.fromtimestamp(exp, tz=timezone.utc) if exp else datetime.now(timezone.utc)
        db.add(RevokedToken(jti=jti, expires_at=expires_at))
        db.commit()
