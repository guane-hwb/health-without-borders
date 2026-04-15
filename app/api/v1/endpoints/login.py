from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import settings
from app.core.rate_limit import limiter
from app.db.models import User
from app.db.session import get_db
from app.schemas.token import Token

router = APIRouter()

DUMMY_PASSWORD_HASH = "$2b$12$C6UzMDM.H6dfI/f/IKcEeOq6Yh6M7f5qX6Ch12yvDqOiiMHDL/95."

@router.post("/login/access-token", response_model=Token)
@limiter.limit(settings.RATE_LIMIT_LOGIN)
def login_access_token(
    request: Request,
    db: Session = Depends(get_db), 
    form_data: OAuth2PasswordRequestForm = Depends()
) -> Any:
    """
    OAuth2 compatible token login. Returns a signed JWT for use in all protected endpoints.

    Send credentials as form data (not JSON):
    - **username**: The user's registered email address.
    - **password**: The user's current password.

    **Responses:**
    - `200`: Login successful. Returns `access_token` and `token_type: bearer`.
    - `400`: Account exists but is deactivated.
    - `401`: Invalid email or password.
    - `422`: Missing required form fields.

    **Usage:** Copy the `access_token` value and click the 'Authorize' button at the top
    of this page to authenticate all subsequent requests.
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

    # 2. Create JWT Token
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    return {
        "access_token": security.create_access_token(
            subject=user.email, expires_delta=access_token_expires
        ),
        "token_type": "bearer",
    }