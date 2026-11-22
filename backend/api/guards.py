"""Cross-site protection for the endpoints that run package code.

Written as plain ASGI middleware, not Starlette's `BaseHTTPMiddleware`. That class runs the app inside
a task group, which wraps any exception raised while a request body is read (for example the body-size
limit's 413) in an `ExceptionGroup`; FastAPI then misreports it as a generic 400.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
FOREIGN_ORIGIN = "The crypt does not open for strangers: origin not allowed."
NOT_JSON = "Send application/json."


class RequestGuardMiddleware:
    """Refuse state-changing requests that a page on another site could send.

    * `Origin` must be this site or an allowed origin. The scheme stays part of the comparison, so an
      `http` page is not the same origin as an `https` API. Behind a proxy that terminates TLS the app
      sees plain http, so the scheme is taken from `X-Forwarded-Proto` when that is a valid value.
    * The body must be declared as `application/json`, which a plain HTML form cannot send.
    """

    def __init__(self, app: ASGIApp, allowed_origins: Callable[[], Sequence[str]]) -> None:
        self.app = app
        self.allowed_origins = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        origin = headers.get("origin")
        host = headers.get("host", "")
        forwarded = headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
        scheme = forwarded if forwarded in ("http", "https") else scope.get("scheme", "http")
        same_origin = bool(host) and origin == f"{scheme}://{host}"

        if origin is not None and not same_origin and origin not in self.allowed_origins():
            await JSONResponse({"detail": FOREIGN_ORIGIN}, status_code=403)(scope, receive, send)
            return
        if not headers.get("content-type", "").lower().startswith("application/json"):
            await JSONResponse({"detail": NOT_JSON}, status_code=415)(scope, receive, send)
            return
        await self.app(scope, receive, send)
