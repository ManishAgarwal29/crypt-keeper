"""Shared Pydantic models for Crypt Keeper.

These describe the data that flows through the revival pipeline:
Exhume -> Autopsy -> Resurrect -> Proof of Life.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Source(str, Enum):
    pypi = "pypi"
    github = "github"


class Liveness(str, Enum):
    alive = "alive"
    dormant = "dormant"
    dead = "dead"


Severity = Literal["low", "medium", "high"]


class PackageRef(BaseModel):
    """Where to exhume a package from."""

    source: Source
    identifier: str = Field(..., description="PyPI name or GitHub repo URL")


class PackageSnapshot(BaseModel):
    """Everything exhumed about a package at a point in time."""

    ref: PackageRef
    last_release_date: str | None = None
    declared_pythons: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    source_map: dict[str, str] = Field(
        default_factory=dict, description="relative path -> file contents"
    )


class Finding(BaseModel):
    """A single diagnosed problem."""

    kind: str
    severity: Severity
    message: str
    location: str | None = None


class AutopsyReport(BaseModel):
    liveness: Liveness
    target_python: str
    findings: list[Finding] = Field(default_factory=list)


class FileDiff(BaseModel):
    path: str
    before: str
    after: str


class RevivalResult(BaseModel):
    diffs: list[FileDiff] = Field(default_factory=list)
    applied: list[str] = Field(default_factory=list)
    manual_items: list[str] = Field(default_factory=list)


class ProofOfLife(BaseModel):
    imported: bool
    error: str | None = None
    tests_passed: int = 0
    tests_failed: int = 0
