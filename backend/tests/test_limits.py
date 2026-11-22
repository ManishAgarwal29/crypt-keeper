"""Tests for the request-body limit (api/limits.py).

The bug these guard against: the limit used to read only the Content-Length header, so a chunked
request (which has none) was never counted, and a malformed header crashed the guard.
"""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st

from api import main
from api.limits import BAD_LENGTH, TOO_LARGE, declared_length

client = TestClient(main.app, raise_server_exceptions=False)
HEADERS = {"Content-Type": "application/json"}
LIMIT = 1000


@pytest.fixture(autouse=True)
def small_limit(monkeypatch):
    monkeypatch.setattr(main, "MAX_BODY_BYTES", LIMIT)


def json_body(size: int) -> bytes:
    """A syntactically valid JSON body of exactly `size` bytes."""
    prefix, suffix = b'{"ref": {"source": "pypi", "identifier": "', b'"}}'
    return prefix + b"a" * (size - len(prefix) - len(suffix)) + suffix


# --- declared_length --------------------------------------------------------------------------------


@pytest.mark.parametrize("headers,expected", [
    ([], None),
    ([(b"content-type", b"x")], None),
    ([(b"content-length", b"0")], 0),
    ([(b"content-length", b"10")], 10),
    ([(b"content-length", b" 7 ")], 7),
    ([(b"content-length", b"5"), (b"content-length", b"5")], 5),
    ([(b"Content-Length", b"12")], 12),
])
def test_declared_length_reads_valid_headers(headers, expected):
    assert declared_length(headers) == expected


@pytest.mark.parametrize("value", [b"", b"abc", b"-1", b"+5", b"1.5", b"1e3", b"0x10", b"12 34", b"\xd9\xa1"])
def test_declared_length_rejects_malformed_values(value):
    with pytest.raises(ValueError):
        declared_length([(b"content-length", value)])


def test_declared_length_rejects_conflicting_duplicates():
    with pytest.raises(ValueError):
        declared_length([(b"content-length", b"5"), (b"content-length", b"6")])


@given(st.binary(max_size=20))
def test_declared_length_returns_a_non_negative_int_or_raises(value):
    try:
        result = declared_length([(b"content-length", value)])
    except ValueError:
        return
    assert isinstance(result, int) and result >= 0


# --- declared length ------------------------------------------------------------------------------------


def test_a_body_exactly_at_the_limit_is_accepted():
    r = client.post("/exhume", content=json_body(LIMIT), headers=HEADERS)
    assert r.status_code != 413


def test_a_body_one_byte_over_the_limit_is_refused():
    r = client.post("/exhume", content=json_body(LIMIT + 1), headers=HEADERS)
    assert r.status_code == 413 and r.json() == {"detail": TOO_LARGE}


def test_a_declared_length_over_the_limit_is_refused_even_if_the_body_is_short():
    r = client.post("/exhume", content=b"{}", headers={**HEADERS, "Content-Length": str(LIMIT + 1)})
    assert r.status_code in (413, 400)  # the client library may rewrite the header; never a success


@pytest.mark.parametrize("value", ["abc", "-5", "1e3", ""])
def test_a_malformed_content_length_is_a_400_not_a_crash(value):
    r = client.post("/exhume", content=b"{}", headers={**HEADERS, "Content-Length": value})
    assert r.status_code in (400, 413) and r.status_code != 500
    if r.status_code == 400:
        assert r.json() == {"detail": BAD_LENGTH}


# --- chunked bodies: the original bypass -----------------------------------------------------------------------


def chunks(*sizes: int):
    body = json_body(sum(sizes))
    start = 0
    for size in sizes:
        yield body[start:start + size]
        start += size


def test_a_chunked_body_over_the_limit_is_refused():
    request = client.build_request("POST", "/exhume", content=chunks(600, 600), headers=HEADERS)
    assert request.headers.get("transfer-encoding") == "chunked" and "content-length" not in request.headers
    r = client.send(request)
    assert r.status_code == 413 and r.json() == {"detail": TOO_LARGE}


def test_a_chunked_body_under_the_limit_is_accepted():
    r = client.post("/exhume", content=chunks(300, 300), headers=HEADERS)
    assert r.status_code != 413


def test_a_chunked_body_exactly_at_the_limit_is_accepted():
    r = client.post("/exhume", content=chunks(500, 500), headers=HEADERS)
    assert r.status_code != 413


def test_every_unsafe_method_is_limited_not_only_post():
    for method in ("PUT", "PATCH", "DELETE"):
        r = client.request(method, "/exhume", content=json_body(LIMIT + 1), headers=HEADERS)
        assert r.status_code in (413, 405), method  # 405 would mean the route refused first; 413 the limit


def test_safe_methods_are_not_limited():
    assert client.get("/health").status_code == 200


# --- streaming: the server stops reading early -------------------------------------------------------------------------


def test_reading_stops_soon_after_the_limit_instead_of_buffering_everything():
    consumed = 0
    total_chunks, chunk = 500, 100  # 50,000 bytes offered against a 1,000-byte limit

    async def body():
        nonlocal consumed
        for _ in range(total_chunks):
            consumed += 1
            yield b"x" * chunk

    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            return await c.post("/exhume", content=body(), headers=HEADERS)

    response = asyncio.run(run())
    assert response.status_code == 413
    assert consumed <= LIMIT // chunk + 2, f"read {consumed} of {total_chunks} chunks; it should stop at the limit"


# --- the other guards still apply -------------------------------------------------------------------------------------------


def test_the_limit_applies_before_the_other_checks():
    r = client.post("/exhume", content=json_body(LIMIT + 1), headers={"Content-Type": "text/plain"})
    assert r.status_code == 413


def test_an_oversized_foreign_origin_request_is_still_never_processed():
    r = client.post("/exhume", content=json_body(LIMIT + 1), headers={**HEADERS, "Origin": "http://evil.example"})
    assert r.status_code in (403, 413)


def test_a_normal_request_still_works_end_to_end():
    snapshot = {"ref": {"source": "pypi", "identifier": "p"}, "last_release_date": "2015-01-01", "source_map": {"a.py": "x = 1\n"}}
    monkey_limit = 100_000
    main.MAX_BODY_BYTES = monkey_limit
    r = client.post("/autopsy", content=json.dumps(snapshot).encode(), headers=HEADERS)
    assert r.status_code == 200 and r.json()["liveness"] == "dead"
