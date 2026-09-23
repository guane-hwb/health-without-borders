
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
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
        email: str = payload.get("sub")
        token_type: str = payload.get("type", "access")
        jti: str = payload.get("jti", "")

        if email is None:
            raise credentials_exception

        # Only access tokens are valid for API endpoints
        if token_type != "access":
            raise credentials_exception

    except JWTError:
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
    
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception
    
    ensure_account_is_active(user, status.HTTP_403_FORBIDDEN)
    return user


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
