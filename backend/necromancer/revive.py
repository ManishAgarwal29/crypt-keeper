"""The Necromancer: pure transforms that revive dead source.

No network, no FastAPI. Pure functions so they can be property-tested (idempotency,
never-drop-a-module, no-pin-below-floor). See .kiro/specs/crypt-keeper-core/design.md.
"""
from __future__ import annotations

import difflib
import re

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import Specifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

from models import AutopsyReport, FileDiff, PackageSnapshot, RevivalResult
from coroner.deprecations import parse_target, scan_source
from necromancer.declare_imports import declare_missing
from necromancer.floors import floor_for
from necromancer.py2convert import convert_python2 as convert_python2_source
from necromancer.removed_names import cap_removed_names, needed_caps
from necromancer.rewriter import rewrite_deprecated
from necromancer.setuptools_dep import declare_setuptools

_DISTUTILS_REMOVED = re.compile(r"`distutils(\.\w+)*` was removed in Python")



def bump_dependencies(
    deps: list[str], target_python: str, keep_caps: frozenset[tuple[str, str]] = frozenset()
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
        new, did, todo = _bump_one(raw, target_python, keep_caps)
        new_deps.append(new)
        applied += did
        manual += todo
    return new_deps, applied, manual


def _bump_one(
    raw: str, target_python: str, keep_caps: frozenset[tuple[str, str]] = frozenset()
) -> tuple[str, list[str], list[str]]:
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
        elif (op, ver) == ("<", "81") and canonicalize_name(req.name) == "setuptools":
            kept.append(spec)  # the cap `declare_setuptools` adds so that pkg_resources exists; it must survive a second revival
        elif op == "<" and (canonicalize_name(req.name), ver) in keep_caps:
            kept.append(spec)  # a ceiling `cap_removed_names` added, and the code still needs it
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


def revive(snapshot: PackageSnapshot, report: AutopsyReport, convert_python2: bool = True) -> RevivalResult:
    """Apply all revival transforms to a snapshot.

    Returns the revived dependency constraints, a diff for every changed file, a log of what
    was applied, and the manual items that still need a human. Pure and deterministic: files
    are processed in path order, dependencies keep their order, and nothing is removed from
    the snapshot (see `revived_snapshot`). Reviving an already revived snapshot changes nothing.

    `convert_python2` turns on the one transform that is not a drop-in replacement: converting Python 2 source with
    lib2to3 (see `necromancer.py2convert`). It only ever touches a package that does not parse as Python 3.
    """
    diffs: list[FileDiff] = []
    applied: list[str] = []
    manual: list[str] = []

    # Python 2 code is converted first, so everything below reads the code as Python 3. The diffs still compare
    # each file with the original the author shipped.
    working, py2_applied, py2_manual = _working_source(snapshot, convert_python2)
    applied += py2_applied
    manual += py2_manual

    new_deps, dep_applied, dep_manual, setuptools_declared = _revise_dependencies(snapshot.dependencies, working, report.target_python)
    applied += dep_applied
    manual += dep_manual

    for path in sorted(snapshot.source_map):
        original = snapshot.source_map[path]
        new_content, file_applied, file_manual = rewrite_deprecated(working[path], report.target_python, path)
        if new_content != original:
            diffs.append(_file_diff(path, original, new_content))
        applied += file_applied
        # `distutils` is only a problem while nothing provides it; setuptools does, once it is a dependency.
        manual += [m for m in file_manual if not (setuptools_declared and _DISTUTILS_REMOVED.search(m))]

    return RevivalResult(diffs=diffs, dependencies=new_deps, applied=applied, manual_items=manual)


def _working_source(snapshot: PackageSnapshot, convert_python2: bool) -> tuple[dict[str, str], list[str], list[str]]:
    """The source the other transforms read: the original, with Python 2 files converted when that is on."""
    working = dict(snapshot.source_map)
    if not convert_python2:
        return working, [], []
    converted, applied, manual = convert_python2_source(working)
    working.update(converted)
    return working, applied, manual


def _revise_dependencies(deps: list[str], working: dict[str, str], target_python: str) -> tuple[list[str], list[str], list[str], bool]:
    """Every change revival makes to the dependency list, in order. Returns (deps, applied, manual, setuptools declared).

    The one place `revive` and `keep_original_dependencies` get these from, so undoing a revision drops exactly the
    notes it added.
    """
    new_deps, applied, manual = bump_dependencies(deps, target_python, keep_caps=needed_caps(working))

    setuptools_declared = False
    if parse_target(target_python) >= (3, 12):  # a fresh 3.12 environment has no setuptools; older ones do
        new_deps, setuptools_applied, setuptools_declared = declare_setuptools(new_deps, working)
        applied += setuptools_applied

    # A dependency that removed a name the code uses is capped below that release; an import the package never
    # declared is declared. Both read the code as it is, and both only ever add what a verified table names.
    new_deps, cap_applied, cap_manual = cap_removed_names(new_deps, working)
    new_deps, declare_applied, declare_manual = declare_missing(new_deps, working)
    return new_deps, applied + cap_applied + declare_applied, manual + cap_manual + declare_manual, setuptools_declared


KEPT_DEPENDENCIES_NOTE = (
    "Kept the original dependency constraints: the package worked with them, and the loosened ones broke it."
)


def keep_original_dependencies(
    snapshot: PackageSnapshot, result: RevivalResult, target_python: str, convert_python2: bool = True
) -> RevivalResult:
    """The same revival with the package's own dependency list instead of the revised one.

    Used when the original imported and the revival did not: loosening a version cap can break a package that
    worked. The code rewrites are kept. The applied and manual notes that came from changing dependencies are
    dropped (they no longer describe what was done) and one note says why. Pure.
    """
    working, _, _ = _working_source(snapshot, convert_python2)
    _, dep_applied, dep_manual, setuptools_declared = _revise_dependencies(snapshot.dependencies, working, target_python)
    dropped_applied, dropped_manual = set(dep_applied), set(dep_manual)
    # With the original list, setuptools is no longer declared, so a `distutils` use is a manual item again.
    restored = [m for m in result.manual_items if m not in dropped_manual]
    if setuptools_declared:
        restored += [
            f"{f.location}: {f.message}" for path in sorted(snapshot.source_map)
            for f in scan_source(path, working[path], target_python) if _DISTUTILS_REMOVED.search(f.message)
        ]
    return result.model_copy(update={
        "dependencies": list(snapshot.dependencies),
        "applied": [a for a in result.applied if a not in dropped_applied] + [KEPT_DEPENDENCIES_NOTE],
        "manual_items": restored,
    })


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
