"""Per-client rate limit for the costly requests. Plain ASGI, fail-closed on a public host, keyed on what the proxy saw."""
import uuid

import pytest
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st

from api import main
from api.limits import DEFAULT_PUBLIC_RATE, LIMITED_PATHS, SlidingWindow, client_key, parse_rate, proxy_hops

client = TestClient(main.app, raise_server_exceptions=False)
SNAPSHOT = {"ref": {"source": "pypi", "identifier": "demo"}, "source_map": {"demo/__init__.py": "x = 1\n"}}


# --- settings ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw, public, expected", [
    (None, True, DEFAULT_PUBLIC_RATE),          # missing on a public host: the default, never unlimited
    ("", True, DEFAULT_PUBLIC_RATE),
    ("garbage", True, DEFAULT_PUBLIC_RATE),     # malformed: the default
    ("5/0", True, DEFAULT_PUBLIC_RATE),
    ("0/60", True, DEFAULT_PUBLIC_RATE),         # a zero count is malformed, not "off"
    ("5/60", True, (5, 60.0)),
    (" 5 / 60 ", True, (5, 60.0)),
    ("off", True, None),                         # only an explicit off turns it off
    ("OFF", True, None),
    (None, False, None),                         # a local run is unlimited unless a limit is set
    ("garbage", False, None),
    ("5/60", False, (5, 60.0)),
])
def test_parse_rate(raw, public, expected):
    assert parse_rate(raw, public) == expected


@given(st.text(max_size=20))
def test_a_public_host_is_never_left_unlimited_by_accident(raw):
    if raw.strip().lower() not in ("off", "0", "none"):
        assert parse_rate(raw, True) is not None


@pytest.mark.parametrize("raw, hops", [(None, 1), ("2", 2), ("0", 0), ("-3", 0), ("x", 1), (" 1 ", 1)])
def test_proxy_hops(raw, hops):
    assert proxy_hops(raw) == hops


# --- who is asking ---------------------------------------------------------------------------------------------------


def xff(value: str) -> list[tuple[bytes, bytes]]:
    return [(b"x-forwarded-for", value.encode())]


def test_behind_a_proxy_the_address_the_proxy_appended_is_used():
    assert client_key(xff("6.6.6.6, 1.2.3.4"), "10.0.0.1", behind_proxy=True) == "1.2.3.4"


def test_a_client_cannot_choose_its_key_by_sending_its_own_header():
    """Whatever the client puts on the left, the proxy's entry on the right stays the key."""
    keys = {client_key(xff(f"{i}.0.0.1, 1.2.3.4"), "10.0.0.1", behind_proxy=True) for i in range(50)}
    assert keys == {"1.2.3.4"}


def test_with_two_proxies_the_second_entry_from_the_right_is_used():
    assert client_key(xff("6.6.6.6, 1.2.3.4, 10.9.9.9"), "10.0.0.1", behind_proxy=True, hops=2) == "1.2.3.4"


def test_a_header_shorter_than_the_proxy_chain_falls_back_to_the_peer():
    assert client_key(xff("6.6.6.6"), "10.0.0.1", behind_proxy=True, hops=2) == "10.0.0.1"


def test_several_header_lines_are_read_as_one_list():
    headers = [(b"x-forwarded-for", b"6.6.6.6"), (b"X-Forwarded-For", b"1.2.3.4")]
    assert client_key(headers, "10.0.0.1", behind_proxy=True) == "1.2.3.4"


def test_without_a_proxy_the_header_is_ignored():
    assert client_key(xff("1.2.3.4"), "10.0.0.1", behind_proxy=False) == "10.0.0.1"
    assert client_key([], None, behind_proxy=False) == "unknown"


# --- the window ------------------------------------------------------------------------------------------------------------


def test_the_window_allows_the_limit_then_says_how_long_to_wait():
    w = SlidingWindow(limit=2, window=60)
    assert w.hit("a", 0) is None and w.hit("a", 10) is None
    assert w.hit("a", 20) == 40, "the oldest request leaves the window at 60"
    assert w.hit("b", 20) is None, "another client is unaffected"


def test_old_requests_leave_the_window():
    w = SlidingWindow(limit=1, window=60)
    assert w.hit("a", 0) is None and w.hit("a", 59) is not None and w.hit("a", 60) is None


def test_a_refused_request_does_not_push_the_wait_further_out():
    w = SlidingWindow(limit=1, window=60)
    w.hit("a", 0)
    for t in range(1, 50):
        w.hit("a", t)
    assert w.hit("a", 60) is None, "hammering while refused does not extend the ban"


def test_the_number_of_clients_remembered_is_bounded():
    w = SlidingWindow(limit=1, window=60, max_keys=100)
    for i in range(1000):
        w.hit(f"c{i}", 0)
    assert len(w.hits) == 100


def test_the_wait_is_never_reported_as_zero():
    w = SlidingWindow(limit=1, window=60)
    w.hit("a", 0)
    assert w.hit("a", 59.99) >= 1


@given(st.lists(st.floats(min_value=0, max_value=1000), min_size=1, max_size=60).map(sorted))
def test_never_more_than_the_limit_inside_any_window(times):
    w = SlidingWindow(limit=3, window=100)
    allowed = [t for t in times if w.hit("a", t) is None]
    for t in allowed:
        assert sum(1 for u in allowed if t - 100 < u <= t) <= 3


# --- the middleware in the app --------------------------------------------------------------------------------------------


@pytest.fixture
def public(monkeypatch):
    monkeypatch.setenv("CRYPT_KEEPER_PUBLIC", "1")
    monkeypatch.setenv("CRYPT_KEEPER_RATE_LIMIT", "2/60")


def ip() -> str:
    """A fresh client address per test: the app, and its limiter, are shared by the whole suite."""
    return "203.0.113." + str(uuid.uuid4().int % 250 + 1) + "-" + uuid.uuid4().hex[:6]


def post(path="/autopsy", address=None, spoof="6.6.6.6"):
    headers = {"X-Forwarded-For": f"{spoof}, {address}"} if address else {}
    return client.post(path, json=SNAPSHOT, headers=headers)


def test_a_client_over_the_limit_gets_429_with_retry_after(public):
    who = ip()
    assert [post(address=who).status_code for _ in range(2)] == [200, 200]
    r = post(address=who)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1 and "too many requests" in r.json()["detail"]


def test_changing_the_left_of_the_header_does_not_get_around_the_limit(public):
    who = ip()
    codes = [post(address=who, spoof=f"{i}.1.1.1").status_code for i in range(4)]
    assert codes == [200, 200, 429, 429]


def test_another_client_is_not_affected(public):
    first = ip()
    for _ in range(3):
        post(address=first)
    assert post(address=ip()).status_code == 200


def test_reads_are_never_limited(public):
    who = ip()
    for _ in range(3):
        post(address=who)
    headers = {"X-Forwarded-For": f"6.6.6.6, {who}"}
    assert client.get("/health", headers=headers).status_code == 200
    assert client.get("/proof-of-life/status", headers=headers).status_code == 200


def test_a_cors_preflight_is_never_counted_or_refused(public):
    """A browser sends OPTIONS before a cross-origin POST. A preflight is not the costly request."""
    who = ip()
    headers = {"X-Forwarded-For": f"6.6.6.6, {who}", "Origin": "http://127.0.0.1:5500",
               "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
    assert all(client.options("/revive", headers=headers).status_code != 429 for _ in range(5))
    assert post(address=who).status_code == 200, "five preflights did not use up the limit"


def test_every_costly_endpoint_is_limited():
    assert {r.path for r in main.app.routes if "POST" in getattr(r, "methods", set())} == LIMITED_PATHS


def test_a_public_host_with_no_setting_gets_the_default_limit(monkeypatch):
    monkeypatch.setenv("CRYPT_KEEPER_PUBLIC", "1")
    monkeypatch.delenv("CRYPT_KEEPER_RATE_LIMIT", raising=False)
    assert main.rate_limit_settings()[0] == DEFAULT_PUBLIC_RATE


def test_an_explicit_off_turns_it_off_on_a_public_host(monkeypatch):
    monkeypatch.setenv("CRYPT_KEEPER_PUBLIC", "1")
    monkeypatch.setenv("CRYPT_KEEPER_RATE_LIMIT", "off")
    who = ip()
    assert all(post(address=who).status_code == 200 for _ in range(5))


def test_a_local_run_is_unlimited_by_default(monkeypatch):
    monkeypatch.delenv("CRYPT_KEEPER_PUBLIC", raising=False)
    monkeypatch.delenv("CRYPT_KEEPER_RATE_LIMIT", raising=False)
    assert main.rate_limit_settings()[0] is None


def test_the_limit_comes_before_the_body_is_read(public):
    """A refused client is answered without the app reading what it sent, even an oversized body."""
    who = ip()
    for _ in range(2):
        post(address=who)
    r = client.post("/autopsy", content=b"x" * (main.MAX_BODY_BYTES + 10), headers={
        "Content-Type": "application/json", "X-Forwarded-For": f"6.6.6.6, {who}"})
    assert r.status_code == 429
