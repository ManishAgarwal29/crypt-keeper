"""The Necromancer: pure transforms that revive dead source.

No network, no FastAPI. Pure functions so they can be property-tested (idempotency,
never-drop-a-module, no-pin-below-floor). See .kiro/specs/crypt-keeper-core/design.md.
"""
from __future__ import annotations

import difflib

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import Specifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

from models import AutopsyReport, FileDiff, PackageSnapshot, RevivalResult
from necromancer.floors import floor_for
from necromancer.rewriter import rewrite_deprecated



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


def revive(snapshot: PackageSnapshot, report: AutopsyReport) -> RevivalResult:
    """Apply all revival transforms to a snapshot.

    Returns the revived dependency constraints, a diff for every changed file, a log of what
    was applied, and the manual items that still need a human. Pure and deterministic: files
    are processed in path order, dependencies keep their order, and nothing is removed from
    the snapshot (see `revived_snapshot`). Reviving an already revived snapshot changes nothing.
    """
    diffs: list[FileDiff] = []
    applied: list[str] = []
    manual: list[str] = []

    new_deps, dep_applied, dep_manual = bump_dependencies(
        snapshot.dependencies, report.target_python
    )
    applied += dep_applied
    manual += dep_manual

    for path in sorted(snapshot.source_map):
        content = snapshot.source_map[path]
        new_content, file_applied, file_manual = rewrite_deprecated(content, report.target_python, path)
        if new_content != content:
            diffs.append(_file_diff(path, content, new_content))
        applied += file_applied
        manual += file_manual

    return RevivalResult(diffs=diffs, dependencies=new_deps, applied=applied, manual_items=manual)


def revived_snapshot(snapshot: PackageSnapshot, result: RevivalResult) -> PackageSnapshot:
    """The snapshot with a revival applied: every original file kept, changed files replaced.

    This is what the proof-of-life runner imports. The input snapshot is not modified.
    """
    source_map = dict(snapshot.source_map)
    for diff in result.diffs:
        source_map[diff.path] = diff.after
    return snapshot.model_copy(update={
        "source_map": source_map,
        "dependencies": list(result.dependencies) or list(snapshot.dependencies),
    })


def _file_diff(path: str, before: str, after: str) -> FileDiff:
    lines = list(difflib.unified_diff(
        before.splitlines(), after.splitlines(),
        fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="",
    ))
    body = lines[2:]  # skip the two file header lines
    return FileDiff(
        path=path, before=before, after=after,
        unified="\n".join(lines),
        lines_added=sum(1 for line in body if line.startswith("+")),
        lines_removed=sum(1 for line in body if line.startswith("-")),
    )
