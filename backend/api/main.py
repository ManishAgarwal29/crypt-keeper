"""FastAPI app: orchestrates the revival pipeline.

This is the only layer that performs I/O. The Coroner and Necromancer stay pure.
Endpoints are stubbed to the pipeline shape defined in the design doc; task 11 fleshes
out real exhumation and the proof-of-life runner.
"""
from __future__ import annotations

from datetime import date

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from coroner.autopsy import run_autopsy
from models import (
    AutopsyReport,
    PackageSnapshot,
    ProofOfLife,
    RevivalResult,
)
from necromancer.revive import revive

app = FastAPI(title="Crypt Keeper", description="Revive the dead. One package at a time.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "the crypt is open"}


@app.post("/autopsy", response_model=AutopsyReport)
def autopsy(snapshot: PackageSnapshot, target_python: str = "3.12") -> AutopsyReport:
    return run_autopsy(snapshot, target_python, date.today())


@app.post("/resurrect", response_model=RevivalResult)
def resurrect(snapshot: PackageSnapshot, target_python: str = "3.12") -> RevivalResult:
    report = run_autopsy(snapshot, target_python, date.today())
    return revive(snapshot, report)


class ReviveResponse(AutopsyReport):
    pass


@app.post("/revive")
def revive_full(snapshot: PackageSnapshot, target_python: str = "3.12") -> dict:
    """Run the full pipeline and return a combined payload for the UI (task 11)."""
    report = run_autopsy(snapshot, target_python, date.today())
    revival = revive(snapshot, report)
    proof = ProofOfLife(imported=False, error="proof-of-life runner not yet implemented")
    return {
        "autopsy": report.model_dump(),
        "revival": revival.model_dump(),
        "proof_of_life": proof.model_dump(),
    }
