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
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from api.exhumer import ExhumeError, exhume
from api.guards import RequestGuardMiddleware
from api.limits import BodyLimitMiddleware
from api.sandbox import find_interpreter_cached as find_interpreter, proof_enabled, run_proof_of_life
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
from necromancer.export import archive_name, build_export
from necromancer.revive import revive, revived_snapshot
from proof.discovery import validate_dependencies
from proof.policy import (
    is_allowed,
    is_public,
    load_approved,
    resolve_allowlist,
    skip_reason,
    unapproved_dependencies,
    unapproved_reason,
)
from proof.verdict import judge

log = logging.getLogger("crypt_keeper")

MAX_BODY_BYTES = 25_000_000
SLOT_WAIT_SECONDS = 30.0
_proof_slots = threading.BoundedSemaphore(int(os.environ.get("CRYPT_KEEPER_MAX_PROOFS", "4")))
_exhume_slots = threading.BoundedSemaphore(int(os.environ.get("CRYPT_KEEPER_MAX_EXHUMES", "8")))


def _csv_env(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


ALLOWED_ORIGINS = _csv_env(
    "CRYPT_KEEPER_ALLOWED_ORIGINS",
    "http://127.0.0.1:8000,http://localhost:8000,http://127.0.0.1:5500,http://localhost:5500",
)
ALLOWED_HOSTS = _csv_env("CRYPT_KEEPER_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver")

app = FastAPI(title="Crypt Keeper", description="Revive the dead. One package at a time.")

# Middleware runs last-added-first: body limit, TrustedHost, the request guard, then CORS.
# The order matters: nothing is read from a request until it has passed the limit and the host check.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


# Origin and content-type checks for state-changing requests (api/guards.py). Plain ASGI on purpose:
# a BaseHTTPMiddleware here would wrap the body limit's 413 in an ExceptionGroup.
app.add_middleware(RequestGuardMiddleware, allowed_origins=lambda: ALLOWED_ORIGINS)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
# Outermost, so an oversized body is refused before anything else looks at it. It counts the bytes
# actually received (a chunked request has no Content-Length), see api/limits.py.
app.add_middleware(BodyLimitMiddleware, max_bytes=lambda: MAX_BODY_BYTES)


@app.exception_handler(ExhumeError)
async def handle_exhume_error(_: Request, exc: ExhumeError) -> JSONResponse:
    return JSONResponse({"detail": exc.message}, status_code=exc.status)


@app.exception_handler(Exception)
async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error", exc_info=exc)
    return JSONResponse({"detail": "The crypt collapsed unexpectedly. See the server log."}, status_code=500)


def proof_allowlist() -> frozenset[str] | None:
    """The packages that may be executed; None means no restriction (see proof/policy.py).

    Read on every call so a change takes effect without a restart. Every ambiguous setting
    resolves to "nothing may run", never to "anything may run".
    """
    return resolve_allowlist(
        os.environ.get("CRYPT_KEEPER_PROOF_ALLOWLIST"), is_public(os.environ.get("CRYPT_KEEPER_PUBLIC"))
    )


def _http_client() -> httpx.Client:
    return httpx.Client(timeout=20, follow_redirects=True)


def _exhume(ref: PackageRef, target_python: str) -> PackageSnapshot:
    """Fetch a package, at most CRYPT_KEEPER_MAX_EXHUMES at a time: each one holds an archive in memory."""
    if not _exhume_slots.acquire(timeout=SLOT_WAIT_SECONDS):
        raise ExhumeError("The crypt is busy: too many exhumations are in progress. Try again shortly.", 503)
    try:
        with _http_client() as client:
            return exhume(ref, target_python, client)
    finally:
        _exhume_slots.release()


def _run_proof(snapshot: PackageSnapshot, target_python: str) -> ProofOfLife:
    """Run the sandbox, waiting briefly for a free slot so a burst cannot exhaust the machine."""
    blocked = dependency_block(snapshot)
    if blocked:
        return ProofOfLife(imported=False, skipped=True, error=blocked)
    if not _proof_slots.acquire(timeout=SLOT_WAIT_SECONDS):
        raise ExhumeError("The crypt is busy: too many proof-of-life runs are in progress. Try again shortly.", 503)
    try:
        return run_proof_of_life(snapshot, target_python)
    finally:
        _proof_slots.release()


def dependency_block(snapshot: PackageSnapshot) -> str | None:
    """Why this snapshot's dependencies may not be installed on this server, or None if they may.

    Applies on a public host with a curated list: approving a package name does not approve what it
    installs. Anything the reviewed file does not list for the package is refused.
    """
    if proof_allowlist() is None or not is_public(os.environ.get("CRYPT_KEEPER_PUBLIC")):
        return None
    deps, _rejected = validate_dependencies(snapshot.dependencies)
    bad = unapproved_dependencies(snapshot.ref.identifier, deps, load_approved())
    if bad == []:
        return None
    return unapproved_reason(snapshot.ref.identifier, bad)


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
    limited_to: list[str] | None = None  # the curated list, when this server has one


@app.get("/proof-of-life/status", response_model=ProofStatus)
def proof_status(target_python: str = "3.12") -> ProofStatus:
    """Whether proof of life can run, on which Python, and for which packages."""
    allowlist = proof_allowlist()
    limited_to = sorted(allowlist) if allowlist is not None else None
    if not proof_enabled():
        return ProofStatus(enabled=False, target_python=target_python, limited_to=limited_to)
    _, version, matched = find_interpreter(target_python)
    return ProofStatus(
        enabled=True, target_python=target_python, python_used=version,
        target_python_matched=matched, limited_to=limited_to,
    )


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
    It runs source the caller supplies, so a server with a curated list refuses it (403): the list
    only protects anything if callers cannot bring their own code.
    """
    if proof_allowlist() is not None:
        raise ExhumeError(
            "Running a posted snapshot is disabled on this server. Use /revive with a listed package.", 403
        )
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
    allowlist = proof_allowlist()
    if req.run_proof_of_life and not is_allowed(req.ref.identifier, allowlist):
        # Not an approved package: report everything that needs no execution, and say why there is no verdict.
        after = ProofOfLife(imported=False, skipped=True, error=skip_reason(allowlist))
        detail = "not on the curated list" if allowlist else "not enabled on this server"
        for stage in ("proof_before", "proof_after"):
            yield {"event": "stage", "stage": stage, "status": "skipped", "detail": detail}
    elif req.run_proof_of_life:
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

    verdict, reason = judge(before, after, truncated=snapshot.truncated)
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


class ExportRequest(BaseModel):
    ref: PackageRef
    target_python: str = "3.12"


@app.post(
    "/revive/export",
    response_class=Response,
    responses={**_ERRORS, 200: {"content": {"application/zip": {}}, "description": "patch, revived sources and report"}},
)
def export_revival(req: ExportRequest) -> Response:
    """Exhume, diagnose and revive a package, and return the result as a zip.

    The zip holds `patch.diff`, the revived `.py` files, the revised requirements and a plain report. Nothing
    is executed, so this works on any package even where proof of life is limited to a list.
    Failure modes: 404 not found, 413 archive too large, 422 invalid name or unreadable archive,
    429 GitHub rate limit, 502 upstream unreachable, 503 busy.
    """
    snapshot = _exhume(req.ref, req.target_python)
    report = run_autopsy(snapshot, req.target_python, date.today())
    revival = revive(snapshot, report)
    return Response(
        content=build_export(snapshot, report, revival),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{archive_name(req.ref.identifier)}"',
            "Cache-Control": "no-store",
        },
    )


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
