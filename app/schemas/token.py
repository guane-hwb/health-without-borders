from typing import Optional

from pydantic import BaseModel


class Token(BaseModel):
    """Response for the access-token endpoint (backward-compatible)."""
    access_token: str
    token_type: str


class TokenPair(BaseModel):
    """
    H4: Full token pair — short-lived access token + long-lived refresh token.
    """
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # Access token TTL in seconds


class RefreshRequest(BaseModel):
    """Body for the /login/refresh endpoint."""
    refresh_token: str


class TokenPayload(BaseModel):
    sub: Optional[str] = None
    jti: Optional[str] = None
    type: Optional[str] = None  # "access" or "refresh"
