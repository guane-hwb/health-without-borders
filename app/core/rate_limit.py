"""
Rate limiting configuration.

H6 fix: Uses Redis as the backing store when REDIS_URL is configured,
preventing limit bypass in multi-instance deployments. Falls back to
in-memory storage for local development.

Also extracts the real client IP from the X-Forwarded-For header so
that rate limits apply per-client rather than per-proxy.
"""

import logging
from typing import Optional

from starlette.requests import Request

from app.core.config import settings

logger = logging.getLogger(__name__)


def _get_real_client_ip(request: Request) -> str:
    """
    Extract the real client IP from X-Forwarded-For (set by Cloud Run,
    nginx, or any reverse proxy). Falls back to the direct remote
    address when no proxy header is present.

    X-Forwarded-For format: "client, proxy1, proxy2"
    We want the leftmost (client) IP.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Take the first (client) IP, strip whitespace
        return forwarded.split(",")[0].strip()
    # Direct connection (local dev)
    return request.client.host if request.client else "127.0.0.1"


def _build_limiter():
    """
    Build the SlowAPI limiter with the best available backend.
    """
    from slowapi import Limiter

    storage_uri: Optional[str] = settings.REDIS_URL

    if storage_uri:
        logger.info("Rate limiter using Redis backend")
        return Limiter(
            key_func=_get_real_client_ip,
            storage_uri=storage_uri,
        )

    logger.warning(
        "REDIS_URL not set — rate limiter using in-memory storage "
        "(not suitable for multi-instance production)"
    )
    return Limiter(key_func=_get_real_client_ip)


limiter = _build_limiter()
