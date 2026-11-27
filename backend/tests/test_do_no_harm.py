"""Do no harm: when the original worked and the revival broke it, try the revived code with the original dependencies."""
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from api import main
from coroner.autopsy import run_autopsy
from models import PackageRef, PackageSnapshot, ProofOfLife, Source
from necromancer.revive import KEPT_DEPENDENCIES_NOTE, keep_original_dependencies, revive
from tests.test_exhumer import SDIST_URL, make_tar, pypi_client, pypi_payload

client = TestClient(main.app, raise_server_exceptions=False)
SOURCE = b"import collections.abc\nx = 1\n"  # alive on 3.12, so only the dependency revision can change anything


def routes(requires):
    return {
        "https://pypi.org/pypi/deadpkg/json": httpx.Response(200, json=pypi_payload(requires_dist=requires)),
        SDIST_URL: httpx.Response(200, content=make_tar({"deadpkg-1.0/deadpkg/__init__.py": SOURCE})),
    }


@pytest.fixture
def capped(monkeypatch):
    """A package that pins `oldlib<2`. The fake sandbox: alive when oldlib is capped, dead when the cap was loosened."""
    monkeypatch.setattr(main, "_http_client", lambda: pypi_client(routes(["oldlib<2"])))
    runs: list[list[str]] = []

    def fake(snapshot, target):
        runs.append(list(snapshot.dependencies))
        alive = any("<2" in d for d in snapshot.dependencies)
        return ProofOfLife(imported=alive, error=None if alive else "ImportError: cannot import name 'gone' from 'oldlib'",
                           python_used="3.12.0", target_python_matched=True, test_source="smoke", tests_passed=int(alive))

    monkeypatch.setattr(main, "run_proof_of_life", fake)
    return runs


def body():
    return {"ref": {"source": "pypi", "identifier": "deadpkg"}}


def test_a_revival_that_breaks_a_working_package_is_retried_with_the_original_dependencies(capped):
    r = client.post("/revive", json=body()).json()
    assert r["verdict"] == "already_alive" and r["proof_after"]["imported"]
    assert r["revival"]["dependencies"] == ["oldlib<2"]
    assert KEPT_DEPENDENCIES_NOTE in r["revival"]["applied"]
    assert not any("Removed upper bound" in a for a in r["revival"]["applied"]), "a change that was undone is not reported as done"
    assert sorted(map(tuple, capped)) == sorted([("oldlib",), ("oldlib<2",), ("oldlib<2",)]), "before, after, and one retry"


def test_if_the_retry_also_fails_the_regression_is_reported_honestly(monkeypatch, capped):
    """Only the first run with the original limits (the check of the original) is alive; the retry is not."""
    import threading

    lock, seen = threading.Lock(), []

    def fake(snapshot, target):
        with lock:
            original_limits = snapshot.dependencies == ["oldlib<2"]
            seen.append(original_limits)
            alive = original_limits and seen.count(True) == 1
        return ProofOfLife(imported=alive, error=None if alive else "ImportError: cannot import name 'gone' from 'oldlib'",
                           python_used="3.12.0", target_python_matched=True)

    monkeypatch.setattr(main, "run_proof_of_life", fake)
    r = client.post("/revive", json=body()).json()
    assert len(seen) == 3, "before, after, and the retry"
    assert r["verdict"] == "regressed" and r["revival"]["dependencies"] == ["oldlib"], "the revival shown is the one that ran"
    assert KEPT_DEPENDENCIES_NOTE not in r["revival"]["applied"]
    assert r["failure_hint"] is not None


@pytest.mark.parametrize("before, after, changed, expected", [
    (True, False, True, True),
    (True, False, False, False),     # nothing to undo
    (False, False, True, False),     # it was dead anyway: the revision is not to blame
    (True, True, True, False),       # it worked
    (None, False, True, False),      # no baseline
])
def test_when_a_retry_is_worth_it(before, after, changed, expected):
    snap = PackageSnapshot(ref=PackageRef(source=Source.pypi, identifier="p"), dependencies=["a<2"])
    from models import RevivalResult

    revival = RevivalResult(dependencies=["a"] if changed else ["a<2"])
    b = None if before is None else ProofOfLife(imported=before)
    assert main._should_retry_with_original_dependencies(snap, revival, b, ProofOfLife(imported=after)) is expected


def test_a_skipped_run_is_never_a_reason_to_retry():
    snap = PackageSnapshot(ref=PackageRef(source=Source.pypi, identifier="p"), dependencies=["a<2"])
    from models import RevivalResult

    assert not main._should_retry_with_original_dependencies(
        snap, RevivalResult(dependencies=["a"]), ProofOfLife(imported=True), ProofOfLife(imported=False, skipped=True))


def test_the_stream_shows_the_retry_as_a_second_proof_after_run(capped):
    import json

    with client.stream("POST", "/revive/stream", json=body()) as r:
        events = [json.loads(line) for line in r.iter_lines() if line.strip()]
    after_done = [e for e in events if e.get("stage") == "proof_after" and e.get("status") == "done"]
    assert len(after_done) == 2 and "original dependency limits" in after_done[1]["detail"]


# --- the pure helper -------------------------------------------------------------------------------------------------


def snap(deps, files=None):
    return PackageSnapshot(ref=PackageRef(source=Source.pypi, identifier="p"), dependencies=deps,
                           source_map=files or {"p/__init__.py": "import collections\nx = collections.Mapping\n"})


def test_code_rewrites_are_kept_and_dependency_notes_dropped():
    s = snap(["six==1.0", "attrs<20"])
    revival = revive(s, run_autopsy(s, "3.12", date(2026, 11, 20)))
    kept = keep_original_dependencies(s, revival, "3.12")
    assert kept.dependencies == ["six==1.0", "attrs<20"] and kept.diffs == revival.diffs
    assert any("collections.abc" in a for a in kept.applied)
    assert not any(a.startswith(("Unpinned", "Removed upper bound")) for a in kept.applied)
    assert kept.applied[-1] == KEPT_DEPENDENCIES_NOTE


def test_a_declared_setuptools_is_undone_too():
    s = snap([], {"p/__init__.py": "import pkg_resources\n"})
    revival = revive(s, run_autopsy(s, "3.12", date(2026, 11, 20)))
    assert revival.dependencies == ["setuptools<81"]
    kept = keep_original_dependencies(s, revival, "3.12")
    assert kept.dependencies == [] and not any("setuptools" in a for a in kept.applied)


def test_every_dependency_change_is_undone_and_no_note_of_it_is_left():
    """Unpin, the removed-name cap and the declared import all come from one function, so the undo drops all three."""
    s = snap(["Jinja2==2.10"], {"p/__init__.py": "import six\nfrom jinja2 import evalcontextfilter\n"})
    revival = revive(s, run_autopsy(s, "3.12", date(2026, 11, 20)))
    assert revival.dependencies == ["Jinja2<3.1,>=2.10", "six"] and len(revival.applied) == 3, "the test needs all three changes"
    kept = keep_original_dependencies(s, revival, "3.12")
    assert kept.dependencies == ["Jinja2==2.10"] and kept.applied == [KEPT_DEPENDENCIES_NOTE]


def test_a_python2_conversion_survives_the_undo():
    s = snap([], {"p/__init__.py": "import six\nprint 'hello'\n"})
    revival = revive(s, run_autopsy(s, "3.12", date(2026, 11, 20)))
    assert revival.dependencies == ["six"] and revival.diffs, "the test needs a conversion and a dependency change"
    kept = keep_original_dependencies(s, revival, "3.12")
    assert kept.diffs == revival.diffs and kept.dependencies == []
    assert any("2to3" in a or "Python 2" in a for a in kept.applied), "the conversion note is kept"


def test_distutils_is_a_manual_item_again_once_setuptools_is_no_longer_declared():
    s = snap([], {"p/__init__.py": "from distutils.core import setup\n"})
    revival = revive(s, run_autopsy(s, "3.12", date(2026, 11, 20)))
    assert revival.manual_items == []
    kept = keep_original_dependencies(s, revival, "3.12")
    assert len(kept.manual_items) == 1 and "distutils" in kept.manual_items[0]


def test_the_input_revival_is_not_modified():
    s = snap(["six==1.0"])
    revival = revive(s, run_autopsy(s, "3.12", date(2026, 11, 20)))
    before = revival.model_dump()
    keep_original_dependencies(s, revival, "3.12")
    assert revival.model_dump() == before
