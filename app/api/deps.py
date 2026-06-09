
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decode_token
from app.db.models import RevokedToken, User
from app.db.session import get_db

# Define where the frontend goes to get the token (the login URL)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/login/access-token")


def get_current_user(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme)
) -> User:
    """
    Dependency that validates the JWT Token sent in the Authorization header.
    
    H4 enhancements:
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

        # H4: Only access tokens are valid for API endpoints
        if token_type != "access":
            raise credentials_exception

    except JWTError:
        raise credentials_exception

    # H4: Check token revocation
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
    
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user")

    return user
