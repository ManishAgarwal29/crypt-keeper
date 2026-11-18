"""Tests for the curated-list policy (pure) and its use by the API."""
import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from api import main
from fastapi.testclient import TestClient
from models import ProofOfLife
from proof.policy import CURATED_REASON, is_allowed, normalize, parse_allowlist
from proof.verdict import judge
from tests.test_api import fakes, revive_body  # noqa: F401  (fixture and helper)

client = TestClient(main.app, raise_server_exceptions=False)


# --- pure policy ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    (None, None), ("", None), (" , ,", None),
    ("attrdict", frozenset({"attrdict"})),
    (" Flask_RestPlus , hyper ", frozenset({"flask-restplus", "hyper"})),
    ("https://github.com/Owner/Repo.git, github.com/o/r/", frozenset({"github.com/owner/repo", "github.com/o/r"})),
])
def test_parse_allowlist(text, expected):
    assert parse_allowlist(text) == expected


@pytest.mark.parametrize("identifier", ["attrdict", "AttrDict", "ATTRDICT ", "  attrdict"])
def test_pypi_names_match_the_way_pypi_matches_them(identifier):
    assert is_allowed(identifier, parse_allowlist("attrdict")) is True


def test_equivalent_spellings_of_one_package_are_one_key():
    assert normalize("Flask_RestPlus") == normalize("flask-restplus") == normalize("flask.restplus")


@pytest.mark.parametrize("identifier", [
    "attrdict2", "attr-dict", "attr.dict", "attr_dict", "attrdic", "../attrdict",
    "https://github.com/o/attrdict", "attrdict/x", "",
])
def test_other_names_and_urls_are_not_allowed(identifier):
    assert is_allowed(identifier, parse_allowlist("attrdict")) is False


def test_github_urls_match_case_insensitively_and_ignore_dot_git():
    allow = parse_allowlist("https://github.com/Owner/Repo")
    assert is_allowed("https://github.com/owner/repo.git", allow)
    assert is_allowed("https://github.com/owner/repo/", allow)
    assert not is_allowed("https://github.com/owner/other", allow)
    assert not is_allowed("https://github.com/evil/repo", allow)


def test_no_list_means_no_restriction():
    assert is_allowed("anything-at-all", None) is True


def test_the_list_never_matches_a_pypi_name_to_a_url_or_back():
    assert not is_allowed("github.com/o/r", parse_allowlist("r"))
    assert not is_allowed("r", parse_allowlist("https://github.com/o/r"))


@given(st.text(max_size=80), st.text(max_size=80))
def test_policy_never_raises(listing, identifier):
    assert isinstance(is_allowed(identifier, parse_allowlist(listing)), bool)


@given(st.text(alphabet="abcXYZ-_.", min_size=1, max_size=12))
def test_a_listed_name_is_allowed_however_it_is_spelled(name):
    allow = parse_allowlist(name)
    assert is_allowed(name.swapcase(), allow) and is_allowed(f"  {name}  ", allow)


# --- verdict -------------------------------------------------------------------------------------------------


def test_a_skipped_proof_explains_itself_in_the_verdict():
    skipped = ProofOfLife(imported=False, skipped=True, error=CURATED_REASON)
    assert judge(None, skipped) == ("unverified", CURATED_REASON)


def test_a_skipped_proof_without_a_reason_gets_the_generic_one():
    assert judge(None, ProofOfLife(imported=False, skipped=True))[1] == "Proof of life was not run."


# --- API with a curated list ------------------------------------------------------------------------------------


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
    # the exhumer is faked for PyPI only, so this fails to exhume; it must fail without running anything
    client.post("/revive", json=gh)
    assert fakes == []


def test_posting_your_own_source_is_refused_when_a_list_is_configured(fakes, curated):  # noqa: F811
    from tests.test_api import snapshot_json
    r = client.post("/proof-of-life", json={"snapshot": snapshot_json()})
    assert r.status_code == 403 and "disabled on this server" in r.json()["detail"] and fakes == []


def test_posting_your_own_source_still_works_locally(fakes, monkeypatch):  # noqa: F811
    from tests.test_api import snapshot_json
    monkeypatch.delenv("CRYPT_KEEPER_PROOF_ALLOWLIST", raising=False)
    assert client.post("/proof-of-life", json={"snapshot": snapshot_json()}).status_code == 200


def test_status_reports_the_list(monkeypatch):
    monkeypatch.setenv("CRYPT_KEEPER_PROOF_ALLOWLIST", "Hyper, attrdict")
    assert client.get("/proof-of-life/status").json()["limited_to"] == ["attrdict", "hyper"]
    monkeypatch.delenv("CRYPT_KEEPER_PROOF_ALLOWLIST")
    assert client.get("/proof-of-life/status").json()["limited_to"] is None


def test_unrestricted_by_default(fakes, monkeypatch):  # noqa: F811
    monkeypatch.delenv("CRYPT_KEEPER_PROOF_ALLOWLIST", raising=False)
    assert client.post("/revive", json=revive_body()).json()["verdict"] == "resurrected"
