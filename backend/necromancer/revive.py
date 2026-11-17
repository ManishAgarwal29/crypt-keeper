"""The Necromancer: pure transforms that revive dead source.

No network, no FastAPI. Pure functions so they can be property-tested (idempotency,
never-drop-a-module, no-pin-below-floor). See .kiro/specs/crypt-keeper-core/design.md.
"""
from __future__ import annotations

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import Specifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

from models import AutopsyReport, FileDiff, PackageSnapshot, RevivalResult
from necromancer.floors import floor_for

# Rewrite mappings expanded in task 7. old substring -> new substring.
API_REWRITES: dict[str, str] = {
    "collections.Mapping": "collections.abc.Mapping",
    "collections.Sequence": "collections.abc.Sequence",
}

def bump_dependencies(
    deps: list[str], target_python: str
) -> tuple[list[str], list[str], list[str]]:
    """Make dependency constraints usable on the target Python.

    For each requirement: exact pins (`==1.2`) become lower bounds (`>=1.2`), upper bounds
    (`<` and `<=`) are removed, and for known compiled packages a lower bound below the
    first release with wheels for the target Python is raised to that floor. Extras and
    environment markers are kept. A requirement that needs no change is returned exactly
    as given.

    Returns (new_deps, applied, manual). Pure, order-preserving, one output per input.
    Anything that can't be fixed automatically goes in `manual` instead of being skipped.
    """
    new_deps: list[str] = []
    applied: list[str] = []
    manual: list[str] = []
    for raw in deps:
        new, did, todo = _bump_one(raw, target_python)
        new_deps.append(new)
        applied += did
        manual += todo
    return new_deps, applied, manual


def _bump_one(raw: str, target_python: str) -> tuple[str, list[str], list[str]]:
    try:
        req = Requirement(raw)
    except InvalidRequirement:
        return raw, [], [f"Could not parse dependency '{raw}'; review it by hand."]
    if req.url:
        return raw, [], [f"'{req.name}' is a direct URL reference; pick a released version by hand."]

    applied: list[str] = []
    manual: list[str] = []
    kept: list[Specifier] = []

    for spec in req.specifier:
        op, ver = spec.operator, spec.version
        if op == "===":
            manual.append(f"'{req.name}' uses arbitrary equality ({spec}); review it by hand.")
            kept.append(spec)
        elif op == "==" and not ver.endswith(".*"):
            lower = Specifier(f">={Version(ver).public}")
            kept.append(lower)
            applied.append(f"Unpinned {req.name}: {spec} -> {lower}")
        elif op in ("<", "<="):
            applied.append(
                f"Removed upper bound {spec} from {req.name} (check the code against newer releases)"
            )
        else:
            kept.append(spec)

    floor = floor_for(canonicalize_name(req.name), target_python)
    if floor is not None:
        kept = _apply_floor(req.name, kept, floor, target_python, applied, manual)

    if not applied:
        return raw, [], manual
    req.specifier = SpecifierSet(",".join(str(s) for s in kept))
    return str(req), applied, manual


def _apply_floor(
    name: str,
    kept: list[Specifier],
    floor: str,
    target_python: str,
    applied: list[str],
    manual: list[str],
) -> list[Specifier]:
    floor_v = Version(floor)

    lows = [s for s in kept if s.operator in (">=", ">")]
    current = max((Version(s.version) for s in lows), default=None)
    if current is not None and current < floor_v:
        kept = [s for s in kept if s not in lows] + [Specifier(f">={floor}")]
        applied.append(
            f"Raised {name} floor from {current} to {floor} (first release with Python {target_python} wheels)"
        )

    for s in kept:
        if _is_prefix_cap(s) and not s.contains(floor, prereleases=True) and _cap_prefix(s) < floor_v:
            manual.append(
                f"'{name}' is capped by {s}, which cannot reach {floor}, the first release "
                f"with Python {target_python} wheels; relax it by hand."
            )
    return kept


def _is_prefix_cap(spec: Specifier) -> bool:
    return spec.operator == "~=" or (spec.operator == "==" and spec.version.endswith(".*"))


def _cap_prefix(spec: Specifier) -> Version:
    """The version prefix a `~=` or `==X.*` specifier is capped to (1.16 for ==1.16.*, 1 for ~=1.16)."""
    if spec.operator == "==":
        return Version(spec.version[:-2])
    release = Version(spec.version).release
    return Version(".".join(str(n) for n in release[:-1]))


def rewrite_deprecated(source: str) -> tuple[str, list[str]]:
    """Rewrite known deprecated APIs. Idempotent by construction."""
    applied: list[str] = []
    out = source
    for old, new in API_REWRITES.items():
        if old in out:
            out = out.replace(old, new)
            applied.append(f"{old} -> {new}")
    return out, applied


def revive(snapshot: PackageSnapshot, report: AutopsyReport) -> RevivalResult:
    """Apply all revival transforms, producing diffs and an applied/manual log."""
    diffs: list[FileDiff] = []
    applied: list[str] = []
    manual: list[str] = []

    new_deps, dep_applied, dep_manual = bump_dependencies(
        snapshot.dependencies, report.target_python
    )
    applied += dep_applied
    manual += dep_manual

    for path, content in snapshot.source_map.items():
        new_content, file_applied = rewrite_deprecated(content)
        if new_content != content:
            diffs.append(FileDiff(path=path, before=content, after=new_content))
            applied += [f"{path}: {a}" for a in file_applied]

    return RevivalResult(diffs=diffs, applied=applied, manual_items=manual)
