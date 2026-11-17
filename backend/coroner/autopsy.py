"""The Coroner: pure analysis that diagnoses a package's cause of death.

No network, no FastAPI. Everything here is a pure function so it can be unit- and
property-tested. See .kiro/specs/crypt-keeper-core/design.md.
"""
from __future__ import annotations

import re
from datetime import date

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from coroner.deprecations import scan_source
from models import AutopsyReport, DependencyInfo, Finding, Liveness, PackageSnapshot

DORMANT_AFTER_DAYS = 365
DEAD_AFTER_DAYS = 365 * 3


PYTHON_LAG_MINORS = 3  # newest declared Python this many minors behind the target counts as lagging
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
_PY_VERSION_RE = re.compile(r"^(\d+)\.(\d+)")


def classify_liveness(
    last_release: date | None,
    now: date,
    declared_pythons: list[str],
    target_python: str | None = None,
) -> Liveness:
    """Classify a package as alive, dormant, or dead.

    By release age: dead if no release date is known or the last release is older than
    DEAD_AFTER_DAYS; dormant from DORMANT_AFTER_DAYS. By Python support: when a
    `target_python` is given and the newest declared Python lags it by PYTHON_LAG_MINORS
    or more (or the package declares only Python 2), an otherwise alive package is
    dormant. Support never makes a package deader than its age does.
    """
    if last_release is None:
        return Liveness.dead
    age_days = (now - last_release).days
    if age_days >= DEAD_AFTER_DAYS:
        return Liveness.dead
    if age_days >= DORMANT_AFTER_DAYS:
        return Liveness.dormant
    if target_python is not None and _python_support_gap(declared_pythons, target_python):
        return Liveness.dormant
    return Liveness.alive


def _newest_python(declared: list[str]) -> tuple[int, int] | None:
    versions = [(int(m.group(1)), int(m.group(2))) for d in declared if (m := _PY_VERSION_RE.match(d))]
    return max(versions) if versions else None


def _python_support_gap(declared: list[str], target_python: str) -> str | None:
    """Describe how the declared Python support falls short of the target, or None if it doesn't.

    Unknown or unparseable data yields None: we only report a gap we can see.
    """
    newest = _newest_python(declared)
    m = _PY_VERSION_RE.match(target_python.strip())
    if newest is None or m is None:
        return None
    target = (int(m.group(1)), int(m.group(2)))
    if newest >= target:
        return None
    if newest[0] < target[0]:
        return f"It declares Python up to {newest[0]}.{newest[1]} only, but the target is {target[0]}.{target[1]}."
    lag = target[1] - newest[1]
    if lag >= PYTHON_LAG_MINORS:
        return (
            f"The newest Python it declares is {newest[0]}.{newest[1]}, "
            f"{lag} versions behind the target {target[0]}.{target[1]}."
        )
    return None


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
    """Scan every file for APIs removed at or before the target Python version."""
    findings: list[Finding] = []
    for path in sorted(source_map):
        findings += scan_source(path, source_map[path], target_python)
    return findings


def run_autopsy(
    snapshot: PackageSnapshot, target_python: str, now: date
) -> AutopsyReport:
    """Compose the full autopsy report from the sub-analyses.

    The report lists every cause of death as a finding, including the package's own
    abandonment and Python support gap, ordered most severe first. Ordering is
    deterministic: severity, then kind, then location.
    """
    last_release = _parse_date(snapshot.last_release_date)
    liveness = classify_liveness(last_release, now, snapshot.declared_pythons, target_python)

    findings = _liveness_findings(liveness, last_release, now)
    if snapshot.truncated:
        findings.append(Finding(
            kind="truncated-source", severity="medium",
            message="The source exceeded the exhumer's size limits, so some files were not analysed.",
        ))
    gap = _python_support_gap(snapshot.declared_pythons, target_python)
    if gap:
        findings.append(Finding(
            kind="python-support-gap", severity="medium", message=gap,
        ))
    findings += analyze_dependencies(snapshot.dependencies, now, snapshot.dependency_info)
    findings += detect_deprecated_apis(snapshot.source_map, target_python)

    findings.sort(key=lambda f: (_SEVERITY_RANK[f.severity], f.kind, f.location or ""))
    return AutopsyReport(liveness=liveness, target_python=target_python, findings=findings)


def _liveness_findings(liveness: Liveness, last_release: date | None, now: date) -> list[Finding]:
    if liveness == Liveness.alive:
        return []
    if last_release is None:
        return [Finding(
            kind="abandoned-package", severity="high",
            message="No release date is known, so the package is presumed dead.",
        )]
    years = (now - last_release).days / 365
    severity = "high" if liveness == Liveness.dead else "medium"
    # A dormant package can be dormant by Python support alone; only blame age if it is old.
    if (now - last_release).days < DORMANT_AFTER_DAYS:
        return []
    return [Finding(
        kind="abandoned-package", severity=severity,
        message=f"Last released {last_release.isoformat()} ({years:.1f} years ago); the package is {liveness.value}.",
    )]


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
