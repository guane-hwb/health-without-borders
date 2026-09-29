"""
HTTP security headers on every response.

Audit finding: be-v2-docs-openapi-publicos-sin-cabeceras (the headers part; the
public /docs and OpenAPI stay, the project is open source).

- ``Cache-Control: no-store`` on everything under the API prefix: those
  responses carry patient records, tokens and the NFC keyring, which no
  browser, proxy or CDN in between should keep.
- ``Strict-Transport-Security``: browsers only reach the service over HTTPS
  (Cloud Run always serves HTTPS; browsers ignore the header over plain HTTP,
  so local development is unaffected).
- ``X-Content-Type-Options: nosniff``, ``X-Frame-Options: DENY``,
  ``Referrer-Policy: no-referrer``: standard hardening for anything a browser
  might open (e.g. /docs).

No Content-Security-Policy: the Swagger UI at /docs loads its assets from a CDN
and a strict policy would break it. The mobile app ignores all of these.
"""

from typing import Awaitable, Callable, Dict

from app.core.config import settings

Scope = Dict
Message = Dict
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

ALWAYS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"strict-transport-security", b"max-age=31536000"),
)
NO_STORE = (b"cache-control", b"no-store")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        api_response = scope.get("path", "").startswith(settings.API_V1_STR)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {name.lower() for name, _ in headers}
                extra = [h for h in ALWAYS if h[0] not in present]
                # An endpoint that sets its own caching policy keeps it.
                if api_response and NO_STORE[0] not in present:
                    extra.append(NO_STORE)
                message = {**message, "headers": headers + extra}
            await send(message)

        await self.app(scope, receive, send_with_headers)
