"""Approving a package name does not approve what it installs: on a public host the dependencies need a review too."""
import json
import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from api import main
from models import PackageRef, PackageSnapshot, ProofOfLife, Source
from proof import policy
from proof.policy import (
    APPROVED_FILE,
    dependency_names,
    load_approved,
    parse_approved,
    unapproved_dependencies,
    unapproved_reason,
)

ROOT = Path(__file__).resolve().parents[2]
APPROVED = {"demo": frozenset({"six", "attrs"})}


def snap(deps: list[str], name: str = "demo") -> PackageSnapshot:
    return PackageSnapshot(ref=PackageRef(source=Source.pypi, identifier=name), dependencies=deps,
                           source_map={"demo/__init__.py": "x = 1\n"})


# --- the pure functions ---------------------------------------------------------------------------------------


def test_names_are_canonical_and_ignore_versions_and_markers():
    assert dependency_names(["Six>=1.0", "typing_extensions; python_version<'3.8'", "A.B==1"]) == ["six", "typing-extensions", "a-b"]


def test_an_unparseable_requirement_keeps_its_text_so_it_never_matches():
    assert dependency_names(["not a requirement!!"]) == ["not a requirement!!"]


def test_reviewed_dependencies_pass():
    assert unapproved_dependencies("demo", ["six>=1", "attrs"], APPROVED) == []


def test_a_dependency_nobody_reviewed_is_reported():
    assert unapproved_dependencies("demo", ["six", "evil-package"], APPROVED) == ["evil-package"]


def test_a_package_without_a_reviewed_list_is_none_not_empty():
    assert unapproved_dependencies("other", [], APPROVED) is None


def test_package_keys_are_compared_after_normalising():
    assert unapproved_dependencies("DEMO", ["six"], APPROVED) == []
    assert unapproved_dependencies("https://github.com/o/r", ["six"], {"github.com/o/r": frozenset({"six"})}) == []


@given(st.lists(st.text(min_size=1, max_size=12), max_size=8))
def test_nothing_is_approved_for_a_package_with_an_empty_reviewed_list(deps):
    bad = unapproved_dependencies("demo", deps, {"demo": frozenset()})
    assert bad is not None and set(bad) == set(dependency_names(deps))


@pytest.mark.parametrize("text", ["", "not json", "[]", "null", '{"demo": 3}', '{"demo": {"dependencies": "six"}}', '{"demo": {"dependencies": [1]}}'])
def test_a_malformed_file_approves_nothing(text):
    assert parse_approved(text) == {}


def test_the_file_format_is_read():
    assert parse_approved('{"Demo": {"dependencies": ["Six", "typing_extensions"], "closure": []}}') == {
        "demo": frozenset({"six", "typing-extensions"})
    }


def test_a_missing_file_approves_nothing(tmp_path):
    assert load_approved(tmp_path / "nope.json") == {}


def test_the_reasons_name_what_was_refused():
    assert "evil" in unapproved_reason("demo", ["evil"])
    assert "no reviewed dependency list" in unapproved_reason("demo", None)


# --- the shipped file ----------------------------------------------------------------------------------------


def dockerfile_allowlist() -> list[str]:
    m = re.search(r'CRYPT_KEEPER_PROOF_ALLOWLIST="([^"]+)"', (ROOT / "Dockerfile").read_text(encoding="utf-8"))
    assert m, "the Dockerfile must set the allow-list"
    return [policy.normalize(item) for item in m.group(1).split(",") if item.strip()]


def test_every_package_the_image_may_run_has_a_reviewed_dependency_list():
    approved = load_approved()
    missing = [p for p in dockerfile_allowlist() if p not in approved]
    assert not missing, f"packages on the Dockerfile allow-list with no entry in approved_dependencies.json: {missing}"


def test_the_shipped_file_is_valid_json_with_reviewed_names_only():
    data = json.loads(APPROVED_FILE.read_text(encoding="utf-8"))
    for key, entry in data.items():
        assert policy.normalize(key) == key, f"{key} must be written in its normalised form"
        assert entry["dependencies"] == sorted(set(entry["dependencies"]))
        assert set(entry["dependencies"]) <= set(entry["closure"]), "the closure must include every direct dependency"


def test_the_reviewed_file_is_part_of_the_image():
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "approved_dependencies" not in ignored and "*.json" not in ignored


# --- enforcement in the API ------------------------------------------------------------------------------------


@pytest.fixture
def public(monkeypatch):
    monkeypatch.setenv("CRYPT_KEEPER_PUBLIC", "1")
    monkeypatch.setenv("CRYPT_KEEPER_PROOF_ALLOWLIST", "demo")
    monkeypatch.setattr(main, "load_approved", lambda: APPROVED)
    ran: list[str] = []
    monkeypatch.setattr(main, "run_proof_of_life", lambda s, t: ran.append(s.ref.identifier) or ProofOfLife(imported=True))
    return ran


def test_reviewed_dependencies_reach_the_sandbox(public):
    assert main._run_proof(snap(["six>=1.0"]), "3.12").imported and public == ["demo"]


def test_an_unreviewed_dependency_never_reaches_the_sandbox(public):
    result = main._run_proof(snap(["six", "evil-package"]), "3.12")
    assert result.skipped and not result.imported and "evil-package" in result.error and public == []


def test_a_package_with_no_reviewed_list_never_reaches_the_sandbox(public, monkeypatch):
    monkeypatch.setattr(main, "load_approved", lambda: {})
    result = main._run_proof(snap([]), "3.12")
    assert result.skipped and "no reviewed dependency list" in result.error and public == []


def test_a_url_style_dependency_is_not_a_way_around_the_list(public):
    """validate_dependencies drops these, so they are never installed; they must not make a run look reviewed or break it."""
    result = main._run_proof(snap(["six", "evil @ https://example.com/x.whl"]), "3.12")
    assert result.imported and public == ["demo"]


def test_a_local_run_without_a_list_is_not_restricted(monkeypatch):
    monkeypatch.delenv("CRYPT_KEEPER_PUBLIC", raising=False)
    monkeypatch.delenv("CRYPT_KEEPER_PROOF_ALLOWLIST", raising=False)
    monkeypatch.setattr(main, "load_approved", lambda: {})
    monkeypatch.setattr(main, "run_proof_of_life", lambda s, t: ProofOfLife(imported=True))
    assert main._run_proof(snap(["anything"]), "3.12").imported


def test_a_list_without_a_public_host_does_not_need_the_review(monkeypatch):
    monkeypatch.delenv("CRYPT_KEEPER_PUBLIC", raising=False)
    monkeypatch.setenv("CRYPT_KEEPER_PROOF_ALLOWLIST", "demo")
    monkeypatch.setattr(main, "load_approved", lambda: {})
    monkeypatch.setattr(main, "run_proof_of_life", lambda s, t: ProofOfLife(imported=True))
    assert main._run_proof(snap(["anything"]), "3.12").imported


@pytest.mark.parametrize("flag", ["1", "true", "yes", "enabled", "", "garbage"])
def test_any_ambiguous_public_setting_enforces_the_review(monkeypatch, flag):
    monkeypatch.setenv("CRYPT_KEEPER_PUBLIC", flag)
    monkeypatch.setenv("CRYPT_KEEPER_PROOF_ALLOWLIST", "demo")
    monkeypatch.setattr(main, "load_approved", lambda: APPROVED)
    monkeypatch.setattr(main, "run_proof_of_life", lambda s, t: ProofOfLife(imported=True))
    assert main._run_proof(snap(["evil"]), "3.12").skipped


# --- exhume concurrency ----------------------------------------------------------------------------------------


def test_exhumes_are_capped_and_refuse_with_503_when_busy(monkeypatch):
    import threading

    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(main, "_exhume_slots", slots)
    monkeypatch.setattr(main, "SLOT_WAIT_SECONDS", 0.1)
    slots.acquire()
    with pytest.raises(main.ExhumeError) as err:
        main._exhume(PackageRef(source=Source.pypi, identifier="x"), "3.12")
    assert err.value.status == 503


def test_an_exhume_slot_is_released_after_a_failure(monkeypatch):
    import threading

    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(main, "_exhume_slots", slots)

    def boom(*_a, **_k):
        raise main.ExhumeError("nope", 404)

    monkeypatch.setattr(main, "exhume", boom)
    for _ in range(3):
        with pytest.raises(main.ExhumeError) as err:
            main._exhume(PackageRef(source=Source.pypi, identifier="x"), "3.12")
        assert err.value.status == 404
