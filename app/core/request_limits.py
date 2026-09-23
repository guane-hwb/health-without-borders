"""
Request body size limits, enforced before any endpoint parses the body.

``POST /login/access-token`` is public and its form is parsed while FastAPI
resolves dependencies — before the rate limiter runs. python-multipart parses a
crafted ``a;a;a;…`` urlencoded body in quadratic time, so a few MiB blocked the
event loop for seconds (audit be-v2-dependencias-vulnerables-dos-login-sin-auth).
A login body is a few hundred bytes; capping it makes that attack cost
milliseconds. Every other route gets a generous cap that still bounds memory.
"""

import json
from typing import Awaitable, Callable, Dict

from app.core.config import settings

#: Default cap for any request body.
MAX_BODY_BYTES = 10 * 1024 * 1024
#: Cap for the authentication endpoints (form or small JSON bodies).
MAX_AUTH_BODY_BYTES = 16 * 1024

Scope = Dict
Message = Dict
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class _BodyTooLarge(Exception):
    pass


def body_limit_for(path: str) -> int:
    """Byte cap for a request path."""
    if path.startswith(f"{settings.API_V1_STR}/login"):
        return MAX_AUTH_BODY_BYTES
    return MAX_BODY_BYTES


class BodySizeLimitMiddleware:
    """Answer 413 when a body exceeds its cap, whether declared or streamed."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = body_limit_for(scope.get("path", ""))

        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await _reject(send, limit)
            return

        received = 0
        too_large = False
        rejected = False

        async def limited_receive() -> Message:
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    too_large = True
                    raise _BodyTooLarge
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal rejected
            if not too_large:
                await send(message)
                return
            # A chunked body without Content-Length only reveals its size while
            # it is read. FastAPI turns the read failure into a 400 "error
            # parsing the body"; replace whatever the app answers with the 413.
            if not rejected:
                rejected = True
                await _reject(send, limit)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            if not rejected:
                await _reject(send, limit)


async def _reject(send: Send, limit: int) -> None:
    body = json.dumps(
        {"detail": f"Request body exceeds the {limit} byte limit for this endpoint."}
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
