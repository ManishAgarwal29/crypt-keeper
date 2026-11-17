"""FastAPI app: orchestrates the revival pipeline.

This is the only layer that performs I/O. The Coroner and Necromancer stay pure.

Because `/proof-of-life` and `/revive` execute package code, the app refuses cross-site
requests (Origin and content-type checks) and unknown Host headers (DNS rebinding). Configure
with CRYPT_KEEPER_ALLOWED_ORIGINS and CRYPT_KEEPER_ALLOWED_HOSTS (comma-separated).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from api.exhumer import ExhumeError, exhume
from api.sandbox import find_interpreter, proof_enabled, run_proof_of_life
from coroner.autopsy import run_autopsy
from models import (
    AutopsyReport,
    ErrorResponse,
    PackageRef,
    PackageSnapshot,
    PackageSummary,
    ProofOfLife,
    RevivalResult,
    ReviveResponse,
)
from necromancer.revive import revive, revived_snapshot
from proof.verdict import judge

log = logging.getLogger("crypt_keeper")

MAX_BODY_BYTES = 25_000_000
SLOT_WAIT_SECONDS = 30.0
_proof_slots = threading.BoundedSemaphore(int(os.environ.get("CRYPT_KEEPER_MAX_PROOFS", "4")))


def _csv_env(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


ALLOWED_ORIGINS = _csv_env(
    "CRYPT_KEEPER_ALLOWED_ORIGINS",
    "http://127.0.0.1:8000,http://localhost:8000,http://127.0.0.1:5500,http://localhost:5500",
)
ALLOWED_HOSTS = _csv_env("CRYPT_KEEPER_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver")

app = FastAPI(title="Crypt Keeper", description="Revive the dead. One package at a time.")

# Middleware runs last-added-first: TrustedHost, then the request guard, then CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def request_guard(request: Request, call_next):
    """Refuse requests that a page on another site could send to a code-running endpoint."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        same_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if origin is not None and origin != same_origin and origin not in ALLOWED_ORIGINS:
            return JSONResponse(
                {"detail": "The crypt does not open for strangers: origin not allowed."}, status_code=403
            )
        if not request.headers.get("content-type", "").lower().startswith("application/json"):
            return JSONResponse({"detail": "Send application/json."}, status_code=415)
        if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
            return JSONResponse({"detail": "The offering is too large for the crypt."}, status_code=413)
    return await call_next(request)


app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


@app.exception_handler(ExhumeError)
async def handle_exhume_error(_: Request, exc: ExhumeError) -> JSONResponse:
    return JSONResponse({"detail": exc.message}, status_code=exc.status)


@app.exception_handler(Exception)
async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error", exc_info=exc)
    return JSONResponse({"detail": "The crypt collapsed unexpectedly. See the server log."}, status_code=500)


def _http_client() -> httpx.Client:
    return httpx.Client(timeout=20, follow_redirects=True)


def _exhume(ref: PackageRef, target_python: str) -> PackageSnapshot:
    with _http_client() as client:
        return exhume(ref, target_python, client)


def _run_proof(snapshot: PackageSnapshot, target_python: str) -> ProofOfLife:
    """Run the sandbox, waiting briefly for a free slot so a burst cannot exhaust the machine."""
    if not _proof_slots.acquire(timeout=SLOT_WAIT_SECONDS):
        raise ExhumeError("The crypt is busy: too many proof-of-life runs are in progress. Try again shortly.", 503)
    try:
        return run_proof_of_life(snapshot, target_python)
    finally:
        _proof_slots.release()


_ERRORS = {
    404: {"model": ErrorResponse, "description": "The package or repository does not exist"},
    422: {"model": ErrorResponse, "description": "Invalid name or URL, or an unreadable source archive"},
    429: {"model": ErrorResponse, "description": "GitHub rate limit reached"},
    502: {"model": ErrorResponse, "description": "PyPI or GitHub could not be reached"},
    503: {"model": ErrorResponse, "description": "Too many proof-of-life runs in progress"},
}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "the crypt is open", "proof_of_life": "on" if proof_enabled() else "off"}


class ProofStatus(BaseModel):
    enabled: bool
    target_python: str
    python_used: str | None = None
    target_python_matched: bool = False


@app.get("/proof-of-life/status", response_model=ProofStatus)
def proof_status(target_python: str = "3.12") -> ProofStatus:
    """Whether proof of life can run, and on which Python. Lets the UI warn before a long run."""
    if not proof_enabled():
        return ProofStatus(enabled=False, target_python=target_python)
    _, version, matched = find_interpreter(target_python)
    return ProofStatus(enabled=True, target_python=target_python, python_used=version, target_python_matched=matched)


class ExhumeRequest(BaseModel):
    ref: PackageRef
    target_python: str = "3.12"


@app.post("/exhume", response_model=PackageSnapshot, responses=_ERRORS)
def exhume_package(req: ExhumeRequest) -> PackageSnapshot:
    """Fetch a package's metadata and source from PyPI or GitHub.

    Failure modes: 404 not found, 422 invalid name/URL or unreadable archive, 413 archive too
    large, 429 GitHub rate limit, 502 upstream unreachable.
    """
    return _exhume(req.ref, req.target_python)


@app.post("/autopsy", response_model=AutopsyReport)
def autopsy(snapshot: PackageSnapshot, target_python: str = "3.12") -> AutopsyReport:
    """Diagnose why a snapshot is dead. Pure analysis; nothing is executed."""
    return run_autopsy(snapshot, target_python, date.today())


@app.post("/resurrect", response_model=RevivalResult)
def resurrect(snapshot: PackageSnapshot, target_python: str = "3.12") -> RevivalResult:
    """Revive a snapshot: bump dependencies and rewrite removed APIs. Nothing is executed."""
    return revive(snapshot, run_autopsy(snapshot, target_python, date.today()))


class ProofRequest(BaseModel):
    snapshot: PackageSnapshot
    target_python: str = "3.12"


@app.post("/proof-of-life", response_model=ProofOfLife, responses=_ERRORS)
def proof_of_life(req: ProofRequest) -> ProofOfLife:
    """Import the snapshot's package in a throwaway virtualenv and run its tests.

    This executes the package's code. See api/sandbox.py for exactly what is and is not isolated.
    """
    return _run_proof(req.snapshot, req.target_python)




class ReviveRequest(BaseModel):
    ref: PackageRef
    target_python: str = "3.12"
    run_proof_of_life: bool = True
    verify_original: bool = True  # also check the original, to show dead -> alive


def _pipeline(req: ReviveRequest) -> Iterator[dict]:
    """Run the whole pipeline, yielding a progress event as each stage starts and finishes.

    Stage events look like {"event": "stage", "stage": "exhume", "status": "start" | "done",
    "seconds": 1.2, "detail": "31 files"}. The final event is {"event": "result", "response":
    ReviveResponse}. Errors propagate as exceptions.
    """
    timings: dict[str, float] = {}
    target = req.target_python

    def start(stage: str) -> dict:
        return {"event": "stage", "stage": stage, "status": "start"}

    def done(stage: str, since: float, detail: str, record: bool = True) -> dict:
        seconds = round(time.perf_counter() - since, 2)
        if record:
            timings[stage] = seconds
        return {"event": "stage", "stage": stage, "status": "done", "seconds": seconds, "detail": detail}

    yield start("exhume")
    t = time.perf_counter()
    snapshot = _exhume(req.ref, target)
    yield done("exhume", t, f"{len(snapshot.source_map)} files" + (", truncated" if snapshot.truncated else ""))

    yield start("autopsy")
    t = time.perf_counter()
    report = run_autopsy(snapshot, target, date.today())
    yield done("autopsy", t, f"{report.liveness.value}, {len(report.findings)} findings")

    yield start("revive")
    t = time.perf_counter()
    revival = revive(snapshot, report)
    yield done("revive", t, f"{len(revival.diffs)} files changed")

    before = after = None
    if req.run_proof_of_life:
        revived = revived_snapshot(snapshot, revival)
        t = time.perf_counter()
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = {pool.submit(_run_proof, revived, target): "proof_after"}
            if req.verify_original:
                jobs[pool.submit(_run_proof, snapshot, target)] = "proof_before"
            for stage in jobs.values():
                yield start(stage)
            for future in as_completed(jobs):
                proof = future.result()
                stage = jobs[future]
                if stage == "proof_after":
                    after = proof
                else:
                    before = proof
                yield done(stage, t, "skipped" if proof.skipped else "alive" if proof.imported else "dead", record=False)
        timings["proof_of_life"] = round(time.perf_counter() - t, 2)

    verdict, reason = judge(before, after)
    yield {
        "event": "result",
        "response": ReviveResponse(
            package=PackageSummary.from_snapshot(snapshot),
            autopsy=report,
            revival=revival,
            proof_before=before,
            proof_after=after,
            verdict=verdict,
            verdict_reason=reason,
            timings=timings,
        ),
    }


@app.post("/revive", response_model=ReviveResponse, responses=_ERRORS)
def revive_package(req: ReviveRequest) -> ReviveResponse:
    """The whole pipeline in one request: exhume, autopsy, revive, then prove (or disprove) life.

    The original and the revived package are checked in parallel, so the extra comparison does
    not double the wait. Failure modes are those of `/exhume`, plus 503 when busy.
    """
    for event in _pipeline(req):
        if event["event"] == "result":
            return event["response"]
    raise RuntimeError("The pipeline ended without a result.")


@app.post("/revive/stream")
def revive_stream(req: ReviveRequest) -> StreamingResponse:
    """Same as `/revive`, but streams newline-delimited JSON so a client can show real progress.

    Each line is a stage event, then one `{"event": "result", "data": <ReviveResponse>}` line, or
    `{"event": "error", "status": <http status>, "detail": <message>}`. Because the HTTP status is
    sent before the work starts, failures arrive as an `error` line, not as a status code.
    """
    def lines() -> Iterator[str]:
        try:
            for event in _pipeline(req):
                if event["event"] == "result":
                    event = {"event": "result", "data": event["response"].model_dump(mode="json")}
                yield json.dumps(event) + "\n"
        except ExhumeError as exc:
            yield json.dumps({"event": "error", "status": exc.status, "detail": exc.message}) + "\n"
        except Exception:
            log.exception("Unhandled error in /revive/stream")
            yield json.dumps({
                "event": "error", "status": 500,
                "detail": "The crypt collapsed unexpectedly. See the server log.",
            }) + "\n"

    return StreamingResponse(
        lines(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    )


# The UI is served by the API itself, so it is same-origin and needs no CORS. Registered last
# so every API route above takes precedence.
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
