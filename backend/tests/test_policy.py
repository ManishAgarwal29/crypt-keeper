"""Tests for the allow-list policy (pure) and its use by the API.

The rule under test: every ambiguous setting must fail closed. A missing variable on a public host, a
blank value, a typo in CRYPT_KEEPER_PUBLIC: none of them may switch execution on for everyone. Only an
explicit `*` does that.
"""
import json

import pytest
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st

from api import main
from models import ProofOfLife
from proof.policy import (
    CURATED_REASON, NOT_ENABLED_REASON, is_allowed, is_public, normalize, resolve_allowlist, skip_reason,
)
from proof.verdict import judge
from tests.test_api import fakes, revive_body, snapshot_json  # noqa: F401  (fixture and helpers)

client = TestClient(main.app, raise_server_exceptions=False)
NOTHING = frozenset()


# --- is_public: ambiguity means public ----------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "0", "false", "False", "no", "NO", "off", " off "])
def test_only_an_unset_or_explicitly_off_variable_is_a_local_run(raw):
    assert is_public(raw) is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", " yes ", "on", "", "   ", "enabled", "truee", "2", "public"])
def test_any_other_value_counts_as_public(raw):
    assert is_public(raw) is True


# --- resolve_allowlist: the truth table in the module docstring ------------------------------------------------


@pytest.mark.parametrize("raw,public,expected", [
    (None, False, None),                       # local run, nothing configured: no restriction
    (None, True, NOTHING),                     # public host, variable missing: nothing runs
    ("", False, NOTHING),                      # blank is never "unrestricted"
    ("", True, NOTHING),
    (" , ,", False, NOTHING),
    (" , ,", True, NOTHING),
    ("*", False, None),                        # the explicit opt-in
    ("*", True, None),
    (" * ", True, None),
    ("attrdict", False, frozenset({"attrdict"})),
    ("attrdict", True, frozenset({"attrdict"})),
    (" Flask_RestPlus , hyper ", True, frozenset({"flask-restplus", "hyper"})),
    ("attrdict,*", True, frozenset({"attrdict"})),   # a star among names is not an opt-in
    ("*,*", True, NOTHING),
    ("https://github.com/Owner/Repo.git, github.com/o/r/", False, frozenset({"github.com/owner/repo", "github.com/o/r"})),
])
def test_resolve_allowlist(raw, public, expected):
    assert resolve_allowlist(raw, public) == expected


@given(st.text(max_size=60), st.booleans())
def test_only_a_lone_star_or_an_unset_local_variable_removes_the_restriction(raw, public):
    result = resolve_allowlist(raw, public)
    items = [i.strip() for i in raw.split(",") if i.strip()]
    assert (result is None) == (items == ["*"])


@given(st.booleans())
def test_a_missing_variable_removes_the_restriction_only_on_a_local_run(public):
    assert (resolve_allowlist(None, public) is None) == (not public)


@given(st.text(max_size=40))
def test_an_ambiguous_public_flag_never_loosens_the_policy(flag):
    """Whatever CRYPT_KEEPER_PUBLIC says, a missing allow-list is only unrestricted if the flag is off."""
    loose = resolve_allowlist(None, is_public(flag)) is None
    assert loose == (flag.strip().lower() in {"0", "false", "no", "off"})


# --- normalisation and matching ------------------------------------------------------------------------------------


@pytest.mark.parametrize("identifier", ["attrdict", "AttrDict", "ATTRDICT ", "  attrdict"])
def test_pypi_names_match_the_way_pypi_matches_them(identifier):
    assert is_allowed(identifier, resolve_allowlist("attrdict", True)) is True


def test_equivalent_spellings_of_one_package_are_one_key():
    assert normalize("Flask_RestPlus") == normalize("flask-restplus") == normalize("flask.restplus")


@pytest.mark.parametrize("identifier", [
    "attrdict2", "attr-dict", "attr.dict", "attr_dict", "attrdic", "../attrdict",
    "https://github.com/o/attrdict", "attrdict/x", "",
])
def test_other_names_and_urls_are_not_allowed(identifier):
    assert is_allowed(identifier, resolve_allowlist("attrdict", True)) is False


def test_github_urls_match_case_insensitively_and_ignore_dot_git():
    allow = resolve_allowlist("https://github.com/Owner/Repo", True)
    assert is_allowed("https://github.com/owner/repo.git", allow)
    assert is_allowed("https://github.com/owner/repo/", allow)
    assert not is_allowed("https://github.com/owner/other", allow)
    assert not is_allowed("https://github.com/evil/repo", allow)


def test_an_empty_list_allows_nothing_and_no_list_allows_everything():
    assert not is_allowed("anything", NOTHING)
    assert is_allowed("anything", None)


def test_the_list_never_matches_a_pypi_name_to_a_url_or_back():
    assert not is_allowed("github.com/o/r", resolve_allowlist("r", True))
    assert not is_allowed("r", resolve_allowlist("https://github.com/o/r", True))


@given(st.text(max_size=80), st.text(max_size=80), st.booleans())
def test_policy_never_raises(listing, identifier, public):
    assert isinstance(is_allowed(identifier, resolve_allowlist(listing, public)), bool)


@given(st.text(alphabet="abcXYZ-_.", min_size=1, max_size=12))
def test_a_listed_name_is_allowed_however_it_is_spelled(name):
    allow = resolve_allowlist(name, True)
    assert is_allowed(name.swapcase(), allow) and is_allowed(f"  {name}  ", allow)


# --- messages and verdict ----------------------------------------------------------------------------------------------


def test_the_skip_reason_says_which_situation_it_is():
    assert skip_reason(frozenset({"attrdict"})) == CURATED_REASON
    assert skip_reason(NOTHING) == NOT_ENABLED_REASON


def test_a_skipped_proof_explains_itself_in_the_verdict():
    skipped = ProofOfLife(imported=False, skipped=True, error=CURATED_REASON)
    assert judge(None, skipped) == ("unverified", CURATED_REASON)


def test_a_skipped_proof_without_a_reason_gets_the_generic_one():
    assert judge(None, ProofOfLife(imported=False, skipped=True))[1] == "Proof of life was not run."


# --- API: a curated list --------------------------------------------------------------------------------------------------


@pytest.fixture
def curated(monkeypatch):
    monkeypatch.setenv("CRYPT_KEEPER_PROOF_ALLOWLIST", "deadpkg")


def stream_events(body: dict) -> list[dict]:
    r = client.post("/revive/stream", json=body)
    return [json.loads(line) for line in r.text.splitlines()]


def test_a_listed_package_still_gets_the_full_pipeline(fakes, curated):  # noqa: F811
    body = client.post("/revive", json=revive_body()).json()
    assert body["verdict"] == "resurrected" and len(fakes) == 2


def test_an_unlisted_package_is_never_executed(fakes, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CRYPT_KEEPER_PROOF_ALLOWLIST", "someone-else")
    r = client.post("/revive", json=revive_body())
    body = r.json()
    assert r.status_code == 200 and fakes == [], "the sandbox must not be called"
    assert body["verdict"] == "unverified" and body["verdict_reason"] == CURATED_REASON
    assert body["proof_before"] is None and body["proof_after"]["skipped"] is True


def test_an_unlisted_package_still_gets_autopsy_revival_and_diffs(fakes, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CRYPT_KEEPER_PROOF_ALLOWLIST", "someone-else")
    body = client.post("/revive", json=revive_body()).json()
    assert body["autopsy"]["liveness"] == "dead" and body["revival"]["diffs"]
    assert "proof_of_life" not in body["timings"]


def test_the_stream_marks_skipped_proof_stages(fakes, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CRYPT_KEEPER_PROOF_ALLOWLIST", "someone-else")
    events = stream_events(revive_body())
    skipped = [e for e in events if e.get("status") == "skipped"]
    assert {e["stage"] for e in skipped} == {"proof_before", "proof_after"}
    assert all(e["detail"] == "not on the curated list" for e in skipped)
    assert events[-1]["event"] == "result" and fakes == []


def test_a_github_repository_is_not_executed_unless_it_is_listed(fakes, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CRYPT_KEEPER_PROOF_ALLOWLIST", "deadpkg")
    gh = {"ref": {"source": "github", "identifier": "https://github.com/o/r"}}
    client.post("/revive", json=gh)  # the exhumer is faked for PyPI only; it must fail without running anything
    assert fakes == []


def test_posting_your_own_source_is_refused_when_a_list_is_configured(fakes, curated):  # noqa: F811
    r = client.post("/proof-of-life", json={"snapshot": snapshot_json()})
    assert r.status_code == 403 and "disabled on this server" in r.json()["detail"] and fakes == []


def test_posting_your_own_source_still_works_locally(fakes):  # noqa: F811
    assert client.post("/proof-of-life", json={"snapshot": snapshot_json()}).status_code == 200


def test_status_reports_the_list(monkeypatch):
    monkeypatch.setenv("CRYPT_KEEPER_PROOF_ALLOWLIST", "Hyper, attrdict")
    assert client.get("/proof-of-life/status").json()["limited_to"] == ["attrdict", "hyper"]
    monkeypatch.delenv("CRYPT_KEEPER_PROOF_ALLOWLIST")
    assert client.get("/proof-of-life/status").json()["limited_to"] is None


def test_a_local_run_with_nothing_configured_is_unrestricted(fakes):  # noqa: F811
    assert client.post("/revive", json=revive_body()).json()["verdict"] == "resurrected"


# --- API: every ambiguous deployment fails closed ------------------------------------------------------------------------------


def assert_nothing_runs(fakes_called: list) -> None:
    """The deploy-mistake contract: /revive skips the sandbox, the raw endpoint is refused, status says so."""
    body = client.post("/revive", json=revive_body()).json()
    assert fakes_called == [], "the sandbox was called"
    assert body["verdict"] == "unverified" and body["verdict_reason"] == NOT_ENABLED_REASON
    assert body["proof_before"] is None and body["proof_after"]["skipped"] is True
    assert body["revival"]["diffs"], "autopsy and revival must still work"
    raw = client.post("/proof-of-life", json={"snapshot": snapshot_json()})
    assert raw.status_code == 403
    assert client.get("/proof-of-life/status").json()["limited_to"] == []
    assert fakes_called == []


@pytest.mark.parametrize("flag", ["1", "true", "enabled", "", "on"])
def test_a_public_host_with_no_allow_list_runs_nothing(fakes, monkeypatch, flag):  # noqa: F811
    monkeypatch.setenv("CRYPT_KEEPER_PUBLIC", flag)
    assert_nothing_runs(fakes)


@pytest.mark.parametrize("value", ["", " ", ",", " , , "])
@pytest.mark.parametrize("public", [None, "1"])
def test_a_blank_allow_list_runs_nothing_in_any_mode(fakes, monkeypatch, value, public):  # noqa: F811
    monkeypatch.setenv("CRYPT_KEEPER_PROOF_ALLOWLIST", value)
    if public:
        monkeypatch.setenv("CRYPT_KEEPER_PUBLIC", public)
    assert_nothing_runs(fakes)


def test_an_explicit_star_is_the_only_way_to_run_everything_on_a_public_host(fakes, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CRYPT_KEEPER_PUBLIC", "1")
    monkeypatch.setenv("CRYPT_KEEPER_PROOF_ALLOWLIST", "*")
    assert client.post("/revive", json=revive_body()).json()["verdict"] == "resurrected"
    assert client.post("/proof-of-life", json={"snapshot": snapshot_json()}).status_code == 200
    assert client.get("/proof-of-life/status").json()["limited_to"] is None


def test_the_public_flag_turned_off_explicitly_restores_the_local_default(fakes, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CRYPT_KEEPER_PUBLIC", "off")
    assert client.post("/revive", json=revive_body()).json()["verdict"] == "resurrected"


def test_the_stream_skips_both_proof_stages_when_nothing_is_enabled(fakes, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CRYPT_KEEPER_PUBLIC", "1")
    events = stream_events(revive_body())
    skipped = [e for e in events if e.get("status") == "skipped"]
    assert {e["stage"] for e in skipped} == {"proof_before", "proof_after"}
    assert {e["detail"] for e in skipped} == {"not enabled on this server"}, "there is no list to be 'not on'"
    assert events[-1]["data"]["verdict_reason"] == NOT_ENABLED_REASON and fakes == []
