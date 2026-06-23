from typing import Optional

from pydantic import BaseModel


class Token(BaseModel):
    """Response for the access-token endpoint (backward-compatible)."""
    access_token: str
    token_type: str


class TokenPair(BaseModel):
    """
    Full token pair — short-lived access token + long-lived refresh token.
    """
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # Access token TTL in seconds
    # Organization-wide AES-256 master key (hex) used to encrypt/decrypt NFC
    # payloads offline. Delivered at login so the device can write and read
    # wristband data without a second round-trip.
    nfc_encryption_key: Optional[str] = None


class RefreshRequest(BaseModel):
    """Body for the /login/refresh endpoint."""
    refresh_token: str


class LogoutRequest(BaseModel):
    """
    Optional body for the /logout endpoint.

    When the client sends its refresh token here, the server revokes it
    alongside the access token so the session is fully terminated and the
    refresh token can no longer mint new access tokens.
    """
    refresh_token: Optional[str] = None


class TokenPayload(BaseModel):
    sub: Optional[str] = None
    jti: Optional[str] = None
    type: Optional[str] = None  # "access" or "refresh"
