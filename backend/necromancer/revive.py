"""The Necromancer: pure transforms that revive dead source.

No network, no FastAPI. Pure functions so they can be property-tested (idempotency,
never-drop-a-module, no-pin-below-floor). See .kiro/specs/crypt-keeper-core/design.md.
"""
from __future__ import annotations

import re

from models import AutopsyReport, FileDiff, PackageSnapshot, RevivalResult

# Rewrite mappings expanded in task 7. old substring -> new substring.
API_REWRITES: dict[str, str] = {
    "collections.Mapping": "collections.abc.Mapping",
    "collections.Sequence": "collections.abc.Sequence",
}

_PIN_RE = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)\s*==\s*(?P<ver>.+)$")


def bump_dependencies(
    deps: list[str], target_python: str
) -> tuple[list[str], list[str], list[str]]:
    """Unpin hard-pinned dependencies.

    Returns (new_deps, applied, manual). Pure and order-preserving.
    """
    new_deps: list[str] = []
    applied: list[str] = []
    manual: list[str] = []
    for dep in deps:
        m = _PIN_RE.match(dep.strip())
        if m:
            name = m.group("name")
            new_deps.append(f"{name}>={m.group('ver')}")
            applied.append(f"Unpinned {name}")
        else:
            new_deps.append(dep)
    return new_deps, applied, manual


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
