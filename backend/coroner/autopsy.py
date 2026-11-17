"""The Coroner: pure analysis that diagnoses a package's cause of death.

No network, no FastAPI. Everything here is a pure function so it can be unit- and
property-tested. See .kiro/specs/crypt-keeper-core/design.md.
"""
from __future__ import annotations

from datetime import date

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from models import AutopsyReport, DependencyInfo, Finding, Liveness, PackageSnapshot

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


def analyze_dependencies(
    deps: list[str],
    now: date,
    info: dict[str, DependencyInfo] | None = None,
) -> list[Finding]:
    """Flag dependencies that are pinned, upper-bounded, unmaintained, yanked, or
    incompatible with the target Python.

    Pure: PyPI facts arrive through `info`, keyed by dependency name. Missing info
    never raises; it only means the metadata-based checks are skipped for that dependency.
    """
    known = {canonicalize_name(name): meta for name, meta in (info or {}).items()}
    findings: list[Finding] = []

    for raw in deps:
        try:
            req = Requirement(raw)
        except InvalidRequirement:
            findings.append(
                Finding(
                    kind="unparseable-dependency",
                    severity="low",
                    message=f"Could not parse dependency '{raw}'; it needs a manual look.",
                    location=raw,
                )
            )
            continue

        name = canonicalize_name(req.name)
        pinned = _exact_pin(req)

        if pinned is not None:
            findings.append(
                Finding(
                    kind="pinned-dependency",
                    severity="medium",
                    message=f"'{req.name}' is hard-pinned to {pinned} and may block revival.",
                    location=raw,
                )
            )

        if any(s.operator in ("<", "<=") for s in req.specifier):
            findings.append(
                Finding(
                    kind="upper-bounded-dependency",
                    severity="low",
                    message=f"'{req.name}' has an upper version bound that may exclude modern releases.",
                    location=raw,
                )
            )

        meta = known.get(name)
        if meta is None:
            continue

        if meta.supports_target_python is False:
            findings.append(
                Finding(
                    kind="incompatible-dependency",
                    severity="high",
                    message=f"'{req.name}' does not support the target Python version.",
                    location=raw,
                )
            )

        if pinned is not None and any(_same_version(pinned, y) for y in meta.yanked_versions):
            findings.append(
                Finding(
                    kind="yanked-dependency",
                    severity="high",
                    message=f"'{req.name}' {pinned} was yanked from PyPI.",
                    location=raw,
                )
            )

        last_release = _parse_date(meta.last_release_date)
        if last_release is not None and classify_liveness(last_release, now, []) == Liveness.dead:
            findings.append(
                Finding(
                    kind="unmaintained-dependency",
                    severity="medium",
                    message=f"'{req.name}' has had no release since {last_release.isoformat()}.",
                    location=raw,
                )
            )

    return findings


def _exact_pin(req: Requirement) -> str | None:
    """Return the pinned version for an exact `==` pin, else None. Wildcards (==1.*) don't count."""
    for spec in req.specifier:
        if spec.operator in ("==", "===") and not spec.version.endswith(".*"):
            return spec.version
    return None


def _same_version(a: str, b: str) -> bool:
    """Compare versions semantically (1.0 == 1.0.0), falling back to string equality."""
    try:
        return Version(a) == Version(b)
    except InvalidVersion:
        return a == b


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
    findings = analyze_dependencies(snapshot.dependencies, now, snapshot.dependency_info)
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
