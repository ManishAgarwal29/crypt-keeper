"""Request-body limits, enforced on the bytes actually received.

A `Content-Length` header is only a claim by the client. A chunked request has none at all, and a
client can send more than it declared. So the limit is checked twice: on the declared length (cheap,
rejects an honest oversized request before reading anything) and on the stream itself (the real
guard, which stops reading as soon as the limit is crossed).

Written as plain ASGI middleware so the app never buffers more than one chunk past the limit.
"""
from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
TOO_LARGE = "The offering is too large for the crypt."
BAD_LENGTH = "Content-Length must be a single non-negative whole number."


def declared_length(headers: list[tuple[bytes, bytes]]) -> int | None:
    """The Content-Length the client declared, or None if it sent none.

    Raises ValueError when the header is malformed or repeated with different values, since a
    request whose size is ambiguous cannot be judged against a limit.
    """
    values = {value.strip() for name, value in headers if name.lower() == b"content-length"}
    if not values:
        return None
    if len(values) != 1:
        raise ValueError("conflicting Content-Length headers")
    (text,) = values
    if not text.isdigit():  # bytes.isdigit() is ASCII-only: rejects "", "-1", "+5", "1e3", "1.5", "0x10"
        raise ValueError("Content-Length is not a whole number")
    return int(text)


class BodyLimitMiddleware:
    """Refuse request bodies larger than `max_bytes()` (read on every request so tests can change it)."""

    def __init__(self, app: ASGIApp, max_bytes: Callable[[], int]) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        limit = self.max_bytes()
        try:
            declared = declared_length(scope["headers"])
        except ValueError:
            await JSONResponse({"detail": BAD_LENGTH}, status_code=400)(scope, receive, send)
            return
        if declared is not None and declared > limit:
            await JSONResponse({"detail": TOO_LARGE}, status_code=413)(scope, receive, send)
            return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # FastAPI's own HTTPException, not Starlette's base class and not a bare Exception:
                    # FastAPI turns any other error raised while it reads the body into a misleading
                    # "400 error parsing the body" and only re-raises its own HTTPException unchanged.
                    raise HTTPException(status_code=413, detail=TOO_LARGE)
            return message

        await self.app(scope, counting_receive, send)
