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


class DependencyInfo(BaseModel):
    """What PyPI knows about one dependency. Filled in by the exhumer (task 9)."""

    last_release_date: str | None = None
    yanked_versions: list[str] = Field(default_factory=list)
    supports_target_python: bool | None = Field(
        default=None, description="None means unknown"
    )


class PackageSnapshot(BaseModel):
    """Everything exhumed about a package at a point in time."""

    ref: PackageRef
    last_release_date: str | None = None
    declared_pythons: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    dependency_info: dict[str, DependencyInfo] = Field(
        default_factory=dict, description="dependency name -> PyPI facts"
    )
    truncated: bool = Field(
        default=False,
        description="True when size or count limits left some .py files out, so analysis and proof of life are incomplete",
    )
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
    unified: str = Field(default="", description="unified diff of before -> after")
    lines_added: int = 0
    lines_removed: int = 0


class RevivalResult(BaseModel):
    diffs: list[FileDiff] = Field(
        default_factory=list, description="changed files only, ordered by path"
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="revived dependency constraints, one per dependency in the snapshot, same order",
    )
    applied: list[str] = Field(default_factory=list)
    manual_items: list[str] = Field(default_factory=list)


class ProofOfLife(BaseModel):
    imported: bool
    error: str | None = None
    tests_passed: int = 0
    tests_failed: int = 0
    tests_errors: int = 0
    test_source: Literal["shipped", "smoke", "none"] = "none"
    import_results: dict[str, str | None] = Field(
        default_factory=dict, description="module -> import error, or None if it imported"
    )
    python_used: str | None = Field(default=None, description="interpreter version the check ran on")
    target_python_matched: bool = Field(
        default=False,
        description="False means the check ran on a different Python than the target, so it does not prove the target works",
    )
    output: str = Field(default="", description="tail of the sandbox logs")
    duration_seconds: float = 0.0
