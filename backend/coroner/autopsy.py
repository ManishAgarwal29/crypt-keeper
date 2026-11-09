"""The Coroner: pure analysis that diagnoses a package's cause of death.

No network, no FastAPI. Everything here is a pure function so it can be unit- and
property-tested. See .kiro/specs/crypt-keeper-core/design.md.
"""
from __future__ import annotations

from datetime import date

from models import AutopsyReport, Finding, Liveness, PackageSnapshot

# Minimal starter table of stdlib APIs removed/deprecated in modern Python.
# Task 4 will expand this. Format: substring -> (severity, message).
DEPRECATED_API_TABLE: dict[str, tuple[str, str]] = {
    "imp.load_source": ("high", "`imp` was removed in 3.12; use importlib."),
    "asyncio.coroutine": ("high", "`@asyncio.coroutine` removed in 3.11; use async def."),
    "collections.Mapping": (
        "medium",
        "ABCs moved to collections.abc in 3.10.",
    ),
}

DORMANT_AFTER_DAYS = 365
DEAD_AFTER_DAYS = 365 * 3


def classify_liveness(
    last_release: date | None, now: date, declared_pythons: list[str]
) -> Liveness:
    """Classify a package as alive, dormant, or dead.

    Dead if no release date is known, or the last release is older than
    DEAD_AFTER_DAYS. Dormant between DORMANT_AFTER_DAYS and DEAD_AFTER_DAYS.
    """
    if last_release is None:
        return Liveness.dead
    age_days = (now - last_release).days
    if age_days >= DEAD_AFTER_DAYS:
        return Liveness.dead
    if age_days >= DORMANT_AFTER_DAYS:
        return Liveness.dormant
    return Liveness.alive


def analyze_dependencies(deps: list[str]) -> list[Finding]:
    """Flag dependencies that look risky. Placeholder logic for task 3."""
    findings: list[Finding] = []
    for dep in deps:
        if "==" in dep:
            findings.append(
                Finding(
                    kind="pinned-dependency",
                    severity="medium",
                    message=f"Dependency '{dep}' is hard-pinned and may block revival.",
                    location=dep,
                )
            )
    return findings


def detect_deprecated_apis(source_map: dict[str, str], target_python: str) -> list[Finding]:
    """Scan source for known deprecated/removed stdlib APIs."""
    findings: list[Finding] = []
    for path, content in source_map.items():
        for needle, (severity, message) in DEPRECATED_API_TABLE.items():
            if needle in content:
                findings.append(
                    Finding(
                        kind="deprecated-api",
                        severity=severity,  # type: ignore[arg-type]
                        message=message,
                        location=path,
                    )
                )
    return findings


def run_autopsy(
    snapshot: PackageSnapshot, target_python: str, now: date
) -> AutopsyReport:
    """Compose the full autopsy report from the sub-analyses."""
    last_release = _parse_date(snapshot.last_release_date)
    liveness = classify_liveness(last_release, now, snapshot.declared_pythons)
    findings = analyze_dependencies(snapshot.dependencies)
    findings += detect_deprecated_apis(snapshot.source_map, target_python)
    return AutopsyReport(
        liveness=liveness, target_python=target_python, findings=findings
    )


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
