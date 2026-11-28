"""A whole request has a time budget: a slow source or slow lookups cannot hold it open indefinitely.

Found by the benchmark: two requests stayed open for about 44 minutes. The sandbox's 600 s budget only covers running a
package; the exhume and the retry were not bounded as a whole. A fake clock stands in for time, so nothing here waits.
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from api import exhumer, main
from api.exhumer import ExhumeError, exhume_github, exhume_pypi, fetch_dependency_info
from models import ProofOfLife
from tests.test_exhumer import SDIST_URL, github_routes, make_tar, pypi_client, pypi_payload

client = TestClient(main.app, raise_server_exceptions=False)


class Clock:
    """Each reading of the clock moves time forward by `step` seconds."""

    def __init__(self, step: float):
        self.t, self.step = 0.0, step

    def __call__(self) -> float:
        self.t += self.step
        return self.t


def trickle(chunks: int):
    """A source archive sent in many small chunks, like a stalled CDN."""
    data = make_tar({"deadpkg-1.0/deadpkg/__init__.py": b"x = 1\n"})
    size = max(1, len(data) // chunks)
    return httpx.Response(200, stream=httpx.ByteStream(data)) if chunks <= 1 else httpx.Response(
        200, content=iter([data[i:i + size] for i in range(0, len(data), size)]))


def routes(sdist=None, requires=None):
    return {
        "https://pypi.org/pypi/deadpkg/json": httpx.Response(200, json=pypi_payload(requires_dist=requires)),
        SDIST_URL: sdist or httpx.Response(200, content=make_tar({"deadpkg-1.0/deadpkg/__init__.py": b"x = 1\n"})),
    }


# --- the exhume budget ---------------------------------------------------------------------------------------------


def test_a_download_that_trickles_past_the_budget_is_stopped_with_504(monkeypatch):
    monkeypatch.setattr(exhumer, "_now", Clock(step=10))       # every chunk costs 10 "seconds"
    with pytest.raises(ExhumeError) as err:
        exhume_pypi("deadpkg", "3.12", pypi_client(routes(sdist=trickle(chunks=200))))
    assert err.value.status == 504 and "too slow" in err.value.message and str(exhumer.MAX_EXHUME_SECONDS) in err.value.message


def test_a_download_within_the_budget_is_unaffected(monkeypatch):
    monkeypatch.setattr(exhumer, "_now", Clock(step=0.001))
    snap = exhume_pypi("deadpkg", "3.12", pypi_client(routes(sdist=trickle(chunks=50))))
    assert snap.source_map == {"deadpkg/__init__.py": "x = 1\n"}


def test_dependency_lookups_stop_at_the_budget_and_keep_what_was_found(monkeypatch):
    monkeypatch.setattr(exhumer, "_now", Clock(step=50))       # a reading before each lookup: 50, 100, 150 ...
    lookups = {f"https://pypi.org/pypi/dep{i}/json": httpx.Response(200, json=pypi_payload(name=f"dep{i}")) for i in range(5)}
    info = fetch_dependency_info([f"dep{i}" for i in range(5)], "3.12", pypi_client(lookups), deadline=120)
    assert sorted(info) == ["dep0", "dep1"], "looked up until the budget ran out, then stopped"


def test_an_exhume_passes_its_deadline_to_the_dependency_lookups(monkeypatch):
    """Through exhume_pypi, not only the helper: the lookups share the exhume's one budget."""
    deps = [f"dep{i}" for i in range(5)]
    r = routes(requires=deps)
    r.update({f"https://pypi.org/pypi/dep{i}/json": httpx.Response(200, json=pypi_payload(name=f"dep{i}")) for i in range(5)})
    monkeypatch.setattr(exhumer, "_now", Clock(step=25))  # deadline 25 + 120 = 145; a few readings pass during the download
    snap = exhume_pypi("deadpkg", "3.12", pypi_client(r))
    assert snap.dependencies == deps and 0 < len(snap.dependency_info) < 5


def test_without_a_deadline_every_dependency_is_looked_up(monkeypatch):
    monkeypatch.setattr(exhumer, "_now", Clock(step=1000))
    lookups = {f"https://pypi.org/pypi/dep{i}/json": httpx.Response(200, json=pypi_payload(name=f"dep{i}")) for i in range(3)}
    assert len(fetch_dependency_info([f"dep{i}" for i in range(3)], "3.12", pypi_client(lookups))) == 3


def test_a_github_repository_that_runs_out_of_time_is_marked_truncated_not_failed(monkeypatch):
    monkeypatch.setattr(exhumer, "_now", Clock(step=200))      # deadline 200 + 120 = 320; the first file fetch reads 400
    snap = exhume_github("https://github.com/o/r", "3.12", pypi_client(github_routes()))
    assert snap.truncated and snap.source_map == {}


def test_the_api_reports_a_slow_source_as_504(monkeypatch):
    monkeypatch.setattr(exhumer, "_now", Clock(step=10))
    monkeypatch.setattr(main, "_http_client", lambda: pypi_client(routes(sdist=trickle(chunks=200))))
    r = client.post("/revive", json={"ref": {"source": "pypi", "identifier": "deadpkg"}})
    assert r.status_code == 504 and "too slow" in r.json()["detail"]


def test_504_is_documented_in_the_openapi_schema():
    assert "504" in client.get("/openapi.json").json()["paths"]["/revive"]["post"]["responses"]


# --- the request budget for the retry --------------------------------------------------------------------------------


@pytest.fixture
def regression(monkeypatch):
    """The original works with `oldlib<2`; the revision loosens the cap and breaks it; the original limits work again."""
    monkeypatch.setattr(main, "_http_client", lambda: pypi_client(routes(requires=["oldlib<2"])))
    runs: list[list[str]] = []

    def fake(snapshot, target):
        runs.append(list(snapshot.dependencies))
        alive = any("<2" in d for d in snapshot.dependencies)
        return ProofOfLife(imported=alive, error=None if alive else "ImportError: x", python_used="3.12.0", target_python_matched=True)

    monkeypatch.setattr(main, "run_proof_of_life", fake)
    return runs


def stream_events():
    with client.stream("POST", "/revive/stream", json={"ref": {"source": "pypi", "identifier": "deadpkg"}}) as r:
        return [json.loads(line) for line in r.iter_lines() if line.strip()]


def test_the_retry_runs_when_a_whole_sandbox_run_still_fits(regression):
    events = stream_events()
    assert len(regression) == 3 and events[-1]["data"]["verdict"] == "already_alive"


def test_the_retry_is_skipped_and_says_so_when_it_would_not_fit(monkeypatch, regression):
    monkeypatch.setattr(main, "REQUEST_BUDGET_SECONDS", main.SANDBOX_BUDGET - 1)
    events = stream_events()
    assert len(regression) == 2, "no third sandbox run"
    skipped = [e for e in events if e.get("stage") == "proof_after" and e.get("status") == "skipped"]
    assert skipped and "no time left" in skipped[0]["detail"]
    assert events[-1]["data"]["verdict"] == "regressed", "the result that did run is reported as it is"


def test_the_request_budget_leaves_room_for_the_sandbox_and_stays_under_the_host_timeout():
    assert main.SANDBOX_BUDGET + exhumer.MAX_EXHUME_SECONDS < main.REQUEST_BUDGET_SECONDS <= 900
