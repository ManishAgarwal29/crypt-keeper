"""API tests (task 11). Network and sandbox are replaced with fakes; nothing is fetched or executed."""
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from api import main
from models import PackageRef, PackageSnapshot, ProofOfLife, Source
from tests.test_exhumer import SDIST_URL, make_tar, pypi_client, pypi_payload

client = TestClient(main.app, raise_server_exceptions=False)

DEAD_SOURCE = b"import collections\nx = collections.Mapping\n"


def pypi_routes(source: bytes = DEAD_SOURCE) -> dict:
    return {
        "https://pypi.org/pypi/deadpkg/json": httpx.Response(200, json=pypi_payload()),
        SDIST_URL: httpx.Response(200, content=make_tar({"deadpkg-1.0/deadpkg/__init__.py": source})),
    }


@pytest.fixture
def fakes(monkeypatch):
    """Serve PyPI from memory and replace the sandbox with a fake that reports on the source."""
    monkeypatch.setattr(main, "_http_client", lambda: pypi_client(pypi_routes()))
    calls: list[PackageSnapshot] = []

    def fake_proof(snapshot, target):
        calls.append(snapshot)
        time.sleep(0.4)
        alive = "collections.abc" in "".join(snapshot.source_map.values())
        return ProofOfLife(
            imported=alive, error=None if alive else "AttributeError: module 'collections' has no attribute 'Mapping'",
            python_used="3.12.0", target_python_matched=True, test_source="smoke",
            tests_passed=2 if alive else 0, tests_failed=0 if alive else 1,
        )

    monkeypatch.setattr(main, "run_proof_of_life", fake_proof)
    return calls


def revive_body(**kw) -> dict:
    return {"ref": {"source": "pypi", "identifier": "deadpkg"}, **kw}


# --- /revive: the whole pipeline ------------------------------------------------------------------------------------


def test_revive_returns_the_whole_pipeline_and_a_resurrection_verdict(fakes):
    r = client.post("/revive", json=revive_body())
    assert r.status_code == 200
    body = r.json()
    assert body["package"] == {
        "identifier": "deadpkg", "source": "pypi", "last_release_date": "2019-03-04",
        "declared_pythons": ["3.6", "3.7"], "dependencies": [], "file_count": 1, "truncated": False,
    }
    assert body["autopsy"]["liveness"] == "dead"
    assert any(f["kind"] == "deprecated-api" for f in body["autopsy"]["findings"])
    [diff] = body["revival"]["diffs"]
    assert diff["path"] == "deadpkg/__init__.py" and "collections.abc.Mapping" in diff["after"]
    assert diff["unified"].startswith("--- a/deadpkg/__init__.py")
    assert body["proof_before"]["imported"] is False and body["proof_after"]["imported"] is True
    assert body["verdict"] == "resurrected" and "AttributeError" in body["verdict_reason"]
    assert set(body["timings"]) == {"exhume", "autopsy", "revive", "proof_of_life"}


def test_revive_checks_the_original_and_the_revived_package_in_parallel(fakes):
    body = client.post("/revive", json=revive_body()).json()
    assert len(fakes) == 2
    assert {"collections.abc" in "".join(s.source_map.values()) for s in fakes} == {True, False}
    assert body["timings"]["proof_of_life"] < 0.75  # two 0.4s checks overlapped


def test_revive_without_the_original_check_has_no_baseline(fakes):
    body = client.post("/revive", json=revive_body(verify_original=False)).json()
    assert body["proof_before"] is None and body["proof_after"]["imported"] is True
    assert body["verdict"] == "unverified" and len(fakes) == 1


def test_revive_can_skip_proof_of_life_entirely(fakes):
    body = client.post("/revive", json=revive_body(run_proof_of_life=False)).json()
    assert body["proof_before"] is None and body["proof_after"] is None
    assert body["verdict"] == "unverified" and "proof_of_life" not in body["timings"] and fakes == []
    assert body["revival"]["diffs"]


def test_revive_response_does_not_ship_the_whole_source(fakes):
    body = client.post("/revive", json=revive_body()).json()
    assert "source_map" not in body["package"]


# --- error mapping -------------------------------------------------------------------------------------------------------


def test_missing_package_is_a_404_that_names_what_was_searched(monkeypatch):
    monkeypatch.setattr(main, "_http_client", lambda: pypi_client({}))
    r = client.post("/revive", json=revive_body())
    assert r.status_code == 404 and "deadpkg" in r.json()["detail"] and "PyPI" in r.json()["detail"]


def test_invalid_name_is_a_422():
    r = client.post("/revive", json={"ref": {"source": "pypi", "identifier": "../bad"}})
    assert r.status_code == 422 and "valid PyPI package name" in r.json()["detail"]


def test_unreachable_upstream_is_a_502(monkeypatch):
    routes = {"https://pypi.org/pypi/deadpkg/json": httpx.ConnectError("down")}
    monkeypatch.setattr(main, "_http_client", lambda: pypi_client(routes))
    assert client.post("/revive", json=revive_body()).status_code == 502


def test_unexpected_errors_are_a_generic_500_that_leaks_nothing(fakes, monkeypatch):
    def explode(*_):
        raise RuntimeError("secret internal path C:/Users/someone")
    monkeypatch.setattr(main, "revive", explode)
    r = client.post("/revive", json=revive_body())
    assert r.status_code == 500 and "secret" not in r.text and "collapsed" in r.json()["detail"]


def test_error_responses_are_documented_in_the_openapi_schema():
    schema = client.get("/openapi.json").json()
    responses = schema["paths"]["/revive"]["post"]["responses"]
    assert {"200", "404", "422", "502", "503"} <= set(responses)
    assert "ReviveResponse" in schema["components"]["schemas"]


# --- the other endpoints ---------------------------------------------------------------------------------------------------


def snapshot_json(source: str = "import collections\nx = collections.Mapping\n") -> dict:
    return PackageSnapshot(
        ref=PackageRef(source=Source.pypi, identifier="p"), last_release_date="2015-01-01", source_map={"a.py": source},
    ).model_dump(mode="json")


def test_autopsy_endpoint_reports_findings():
    body = client.post("/autopsy", json=snapshot_json()).json()
    assert body["liveness"] == "dead" and any(f["kind"] == "deprecated-api" for f in body["findings"])


def test_proof_of_life_endpoint_runs_the_sandbox_on_the_posted_snapshot(fakes):
    r = client.post("/proof-of-life", json={"snapshot": snapshot_json("import collections.abc\n"), "target_python": "3.12"})
    assert r.status_code == 200 and r.json()["imported"] is True and len(fakes) == 1


def test_proof_of_life_is_refused_with_503_when_every_slot_is_busy(fakes, monkeypatch):
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(main, "_proof_slots", slots)
    monkeypatch.setattr(main, "SLOT_WAIT_SECONDS", 0.05)
    r = client.post("/proof-of-life", json={"snapshot": snapshot_json()})
    assert r.status_code == 503 and "busy" in r.json()["detail"] and fakes == []


def test_slots_are_released_after_each_run(fakes, monkeypatch):
    monkeypatch.setattr(main, "_proof_slots", threading.BoundedSemaphore(1))
    for _ in range(3):
        assert client.post("/proof-of-life", json={"snapshot": snapshot_json()}).status_code == 200


def test_health_reports_whether_proof_of_life_is_on(monkeypatch):
    assert client.get("/health").json() == {"status": "the crypt is open", "proof_of_life": "on"}
    monkeypatch.setenv("CRYPT_KEEPER_PROOF_OF_LIFE", "off")
    assert client.get("/health").json()["proof_of_life"] == "off"


def test_proof_status_warns_when_the_target_python_is_missing(monkeypatch):
    monkeypatch.setattr(main, "find_interpreter", lambda target: ("x", "3.11.6", False))
    body = client.get("/proof-of-life/status", params={"target_python": "3.12"}).json()
    assert body == {"enabled": True, "target_python": "3.12", "python_used": "3.11.6", "target_python_matched": False}


def test_proof_status_when_disabled(monkeypatch):
    monkeypatch.setenv("CRYPT_KEEPER_PROOF_OF_LIFE", "off")
    assert client.get("/proof-of-life/status").json()["enabled"] is False


# --- cross-site protection for the code-running endpoints --------------------------------------------------------------------


def post_autopsy(**headers):
    return client.post("/autopsy", json=snapshot_json(), headers=headers)


def test_posts_from_a_foreign_origin_are_refused():
    assert post_autopsy(Origin="http://evil.example").status_code == 403


def test_posts_from_the_configured_dev_origin_are_accepted():
    assert post_autopsy(Origin="http://localhost:5500").status_code == 200


def test_same_origin_posts_are_accepted_on_any_port():
    r = client.post("/autopsy", json=snapshot_json(), headers={"Origin": "http://testserver", "Host": "testserver"})
    assert r.status_code == 200


def test_posts_without_an_origin_header_are_accepted():
    assert post_autopsy().status_code == 200


def test_a_json_body_sent_as_plain_text_is_refused():
    r = client.post("/autopsy", content='{"x": 1}', headers={"Content-Type": "text/plain"})
    assert r.status_code == 415


def test_unknown_host_headers_are_refused_against_dns_rebinding():
    assert post_autopsy(Host="evil.example").status_code == 400
    assert client.get("/health", headers={"Host": "evil.example"}).status_code == 400


def test_oversized_bodies_are_refused(monkeypatch):
    monkeypatch.setattr(main, "MAX_BODY_BYTES", 10)
    assert post_autopsy().status_code == 413


def test_preflight_from_a_foreign_origin_gets_no_cors_permission():
    r = client.options("/autopsy", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
    assert r.status_code == 400 and "access-control-allow-origin" not in r.headers


def test_preflight_from_an_allowed_origin_succeeds():
    r = client.options(
        "/autopsy",
        headers={"Origin": "http://localhost:5500", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"},
    )
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "http://localhost:5500"


def test_reads_do_not_grant_cors_to_foreign_origins():
    r = client.get("/health", headers={"Origin": "http://evil.example"})
    assert r.status_code == 200 and "access-control-allow-origin" not in r.headers


# --- /revive/stream: real progress for the UI ------------------------------------------------------------------------------------

import json  # noqa: E402


def stream(body: dict) -> list[dict]:
    r = client.post("/revive/stream", json=body)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in r.text.splitlines()]


def test_stream_reports_each_stage_then_the_full_result(fakes):
    events = stream(revive_body())
    stages = [(e["stage"], e["status"]) for e in events if e["event"] == "stage"]
    assert stages[:6] == [
        ("exhume", "start"), ("exhume", "done"), ("autopsy", "start"), ("autopsy", "done"),
        ("revive", "start"), ("revive", "done"),
    ]
    assert {s for s in stages[6:]} == {
        ("proof_before", "start"), ("proof_after", "start"), ("proof_before", "done"), ("proof_after", "done"),
    }
    done = {e["stage"]: e for e in events if e["event"] == "stage" and e["status"] == "done"}
    assert done["exhume"]["detail"] == "1 files" and "dead" in done["autopsy"]["detail"]
    assert done["revive"]["detail"] == "1 files changed"
    assert done["proof_before"]["detail"] == "dead" and done["proof_after"]["detail"] == "alive"
    assert all(isinstance(e["seconds"], float) for e in done.values())
    assert events[-1]["event"] == "result" and events[-1]["data"]["verdict"] == "resurrected"
    assert [e["event"] for e in events].count("result") == 1


def test_stream_starts_both_proofs_before_either_finishes(fakes):
    events = [(e["stage"], e["status"]) for e in stream(revive_body()) if e["event"] == "stage"]
    first_done = min(i for i, (s, st) in enumerate(events) if s.startswith("proof") and st == "done")
    started = [i for i, (s, st) in enumerate(events) if s.startswith("proof") and st == "start"]
    assert len(started) == 2 and max(started) < first_done


def test_stream_without_proof_of_life_has_no_proof_stages(fakes):
    events = stream(revive_body(run_proof_of_life=False))
    assert not any(e.get("stage", "").startswith("proof") for e in events)
    assert events[-1]["data"]["verdict"] == "unverified"


def test_stream_reports_a_missing_package_as_an_error_event(monkeypatch):
    monkeypatch.setattr(main, "_http_client", lambda: pypi_client({}))
    events = stream(revive_body())
    assert events[0] == {"event": "stage", "stage": "exhume", "status": "start"}
    assert events[-1]["event"] == "error" and events[-1]["status"] == 404
    assert "deadpkg" in events[-1]["detail"] and not any(e["event"] == "result" for e in events)


def test_stream_unexpected_errors_are_generic(fakes, monkeypatch):
    def explode(*_):
        raise RuntimeError("secret internal path")
    monkeypatch.setattr(main, "revive", explode)
    events = stream(revive_body())
    assert events[-1]["event"] == "error" and events[-1]["status"] == 500 and "secret" not in json.dumps(events)


def test_stream_busy_sandbox_is_an_error_event(fakes, monkeypatch):
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(main, "_proof_slots", slots)
    monkeypatch.setattr(main, "SLOT_WAIT_SECONDS", 0.05)
    assert stream(revive_body())[-1] == {
        "event": "error", "status": 503,
        "detail": "The crypt is busy: too many proof-of-life runs are in progress. Try again shortly.",
    }


def test_stream_is_protected_like_the_other_post_endpoints(fakes):
    assert client.post("/revive/stream", json=revive_body(), headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post("/revive/stream", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415


# --- the UI is served by the API --------------------------------------------------------------------------------------------------


def test_the_frontend_is_served_from_the_same_origin():
    page = client.get("/")
    assert page.status_code == 200 and "Crypt Keeper" in page.text and page.headers["content-type"].startswith("text/html")
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200


def test_api_routes_win_over_the_static_mount_and_unknown_paths_404():
    assert client.get("/health").json()["status"] == "the crypt is open"
    assert client.get("/no-such-file.txt").status_code == 404
