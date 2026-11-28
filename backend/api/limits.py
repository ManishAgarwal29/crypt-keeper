"""Request-body limits, enforced on the bytes actually received.

A `Content-Length` header is only a claim by the client. A chunked request has none at all, and a
client can send more than it declared. So the limit is checked twice: on the declared length (cheap,
rejects an honest oversized request before reading anything) and on the stream itself (the real
guard, which stops reading as soon as the limit is crossed).

Written as plain ASGI middleware so the app never buffers more than one chunk past the limit.
"""
from __future__ import annotations

import re
import time
from collections import OrderedDict, deque
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


# --- per-client rate limit -------------------------------------------------------------------------------------------

RATE_LIMIT_ENV = "CRYPT_KEEPER_RATE_LIMIT"
PROXY_HOPS_ENV = "CRYPT_KEEPER_TRUSTED_PROXY_HOPS"
# 30 costly requests per client per 10 minutes on a public host. The smoke test alone makes about 12, and judges behind
# one office address share a limit, so it is set well above what one person clicking through the demo needs (about 8).
DEFAULT_PUBLIC_RATE = (30, 600.0)
MAX_TRACKED_CLIENTS = 10_000
# Requests that fetch from PyPI or GitHub, or run code. Reads of the UI, /health and the status are never limited.
LIMITED_PATHS = frozenset({"/revive", "/revive/stream", "/revive/export", "/proof-of-life", "/exhume", "/autopsy", "/resurrect"})
RATE_LIMITED = "The crypt needs rest: too many requests from your address. Try again in {seconds} s."
_RATE = re.compile(r"\s*(\d+)\s*/\s*(\d+)\s*")


def parse_rate(raw: str | None, public: bool) -> tuple[int, float] | None:
    """(requests, window seconds), or None for no limit. Read from CRYPT_KEEPER_RATE_LIMIT, e.g. `20/600`.

    Fails closed on a public host: a missing or malformed value means the default limit, never "unlimited". Only an
    explicit `off` turns it off there. A local run has no limit unless one is set.
    """
    text = (raw or "").strip().lower()
    if text in ("off", "0", "none"):
        return None
    match = _RATE.fullmatch(text)
    if match and int(match.group(1)) > 0 and int(match.group(2)) > 0:
        return int(match.group(1)), float(match.group(2))
    return DEFAULT_PUBLIC_RATE if public else None


def proxy_hops(raw: str | None) -> int:
    """How many proxies in front of the app append to X-Forwarded-For. Default 1 (Azure, Cloud Run); never negative."""
    try:
        return max(0, int((raw or "1").strip()))
    except ValueError:
        return 1


def client_key(headers: list[tuple[bytes, bytes]], peer: str | None, behind_proxy: bool, hops: int = 1) -> str:
    """Who is asking. Behind a proxy, the address the proxy added: X-Forwarded-For counted `hops` from the right.

    The left of X-Forwarded-For is whatever the client sent, so it can be anything; only what the host's own proxy
    appended can be trusted. Without a proxy, the TCP peer. A header with fewer entries than `hops` falls back to the
    peer, so a client cannot pick its own key by sending a short header.
    """
    if behind_proxy and hops > 0:
        values = [v.decode("latin-1") for n, v in headers if n.lower() == b"x-forwarded-for"]
        entries = [e.strip() for e in ",".join(values).split(",") if e.strip()]
        if len(entries) >= hops:
            return entries[-hops]
    return peer or "unknown"


class SlidingWindow:
    """Requests per key in the last `window` seconds. Bounded: the least recently seen keys are forgotten first."""

    def __init__(self, limit: int, window: float, max_keys: int = MAX_TRACKED_CLIENTS) -> None:
        self.limit, self.window, self.max_keys = limit, window, max_keys
        self.hits: OrderedDict[str, deque[float]] = OrderedDict()

    def hit(self, key: str, now: float) -> float | None:
        """Record a request. Returns None if it is allowed, else the seconds until the next one would be."""
        times = self.hits.pop(key, None) or deque()
        while times and times[0] <= now - self.window:
            times.popleft()
        if len(times) >= self.limit:
            self.hits[key] = times
            return max(1.0, times[0] + self.window - now)
        times.append(now)
        self.hits[key] = times
        while len(self.hits) > self.max_keys:
            self.hits.popitem(last=False)
        return None


class RateLimitMiddleware:
    """Plain ASGI. Limits the costly POSTs per client; reads the settings on every request so tests can change them."""

    def __init__(self, app: ASGIApp, settings: Callable[[], tuple[tuple[int, float] | None, bool, int]],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.app, self.settings, self.clock = app, settings, clock
        self.window: SlidingWindow | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS or scope["path"] not in LIMITED_PATHS:
            await self.app(scope, receive, send)
            return
        rate, behind_proxy, hops = self.settings()
        if rate is None:
            await self.app(scope, receive, send)
            return
        if self.window is None or (self.window.limit, self.window.window) != rate:
            self.window = SlidingWindow(*rate)
        peer = (scope.get("client") or (None,))[0]
        wait = self.window.hit(client_key(scope["headers"], peer, behind_proxy, hops), self.clock())
        if wait is None:
            await self.app(scope, receive, send)
            return
        seconds = int(wait + 0.999)
        response = JSONResponse({"detail": RATE_LIMITED.format(seconds=seconds)}, status_code=429,
                                headers={"Retry-After": str(seconds)})
        await response(scope, receive, send)
