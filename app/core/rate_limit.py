"""
Rate limiting configuration.

Uses Redis as the backing store when REDIS_URL is configured, preventing
limit bypass in multi-instance deployments. Falls back to in-memory storage
for local development.

Also extracts the real client IP from the X-Forwarded-For header so that
rate limits apply per-client rather than per-proxy.
"""

import logging
from typing import Optional

from starlette.requests import Request

from app.core.config import settings

logger = logging.getLogger(__name__)


def _get_real_client_ip(request: Request) -> str:
    """
    Extract the real client IP from X-Forwarded-For (set by Cloud Run, nginx,
    or any reverse proxy). Falls back to the direct remote address when no
    proxy header is present.

    A client can forge the leftmost X-Forwarded-For entries, so the leftmost
    value is never trusted for rate-limit keying. Instead, the IP recorded by
    the outermost trusted proxy is used: with ``TRUSTED_PROXY_HOPS`` trusted
    proxies in front of the app, the client IP is the n-th entry counted from
    the right. Anything to its left is client-supplied and ignored.

    ``TRUSTED_PROXY_HOPS`` must match the deployment. The default (1) assumes a
    single trusted front proxy and is the safe choice: if it under-counts, it
    keys on a proxy IP (grouping clients = more restrictive), never on a
    spoofable value.

    X-Forwarded-For format: "client, proxy1, proxy2" (each proxy appends the
    address that connected to it).
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            hops = settings.TRUSTED_PROXY_HOPS if settings.TRUSTED_PROXY_HOPS > 0 else 1
            idx = len(parts) - hops
            return parts[idx] if idx >= 0 else parts[0]
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
