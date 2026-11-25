"""Prepare the reviewed-dependency entries for a public host.

Approving a package name for proof of life does not approve what it installs. This script lists, for each
package, the direct dependencies the server will accept (`dependencies`) and the full set pip would pull
in on Python 3.12 (`closure`), so a person can read them before they are written down.

    python scripts/review_dependencies.py attrdict hyper unittest2 flask-restplus            # print
    python scripts/review_dependencies.py attrdict hyper --write                              # update the file

Nothing is approved by running this: `--write` records what you just looked at. Re-run it, and read the
diff, whenever a package or its dependencies change. Needs network access to PyPI.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import httpx
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from api.exhumer import ExhumeError, exhume_pypi, fetch_pypi_metadata  # noqa: E402
from coroner.autopsy import run_autopsy  # noqa: E402
from necromancer.revive import revive  # noqa: E402
from proof.discovery import validate_dependencies  # noqa: E402
from proof.policy import APPROVED_FILE, dependency_names, normalize  # noqa: E402

MARKER_ENV = {"python_version": "3.12", "python_full_version": "3.12.0", "sys_platform": "linux", "platform_system": "Linux",
              "os_name": "posix", "implementation_name": "cpython", "platform_python_implementation": "CPython", "extra": ""}
MAX_CLOSURE = 60


def closure(names: list[str], client: httpx.Client) -> list[str]:
    """Every project pip would install for `names` on Python 3.12 (latest releases; an approximation)."""
    seen: set[str] = set()
    queue = list(names)
    while queue and len(seen) < MAX_CLOSURE:
        name = canonicalize_name(queue.pop())
        if name in seen:
            continue
        seen.add(name)
        try:
            meta = fetch_pypi_metadata(name, client)
        except ExhumeError as exc:
            print(f"  ! could not look up {name}: {exc.message}", file=sys.stderr)
            continue
        for raw in meta.dependencies:
            try:
                req = Requirement(raw)
            except InvalidRequirement:
                continue
            if req.marker is None or req.marker.evaluate(MARKER_ENV):
                queue.append(req.name)
    return sorted(seen)


def review(package: str, client: httpx.Client) -> dict:
    snapshot = exhume_pypi(package, "3.12", client)
    # The server runs the original AND the revived package, and a revival can add a dependency (setuptools, for code
    # that imports pkg_resources or distutils). Review both lists, or the revived run is refused on a public host.
    revival = revive(snapshot, run_autopsy(snapshot, "3.12", date.today()))
    direct, rejected = validate_dependencies(snapshot.dependencies + [d for d in revival.dependencies if d not in snapshot.dependencies])
    names = sorted(set(dependency_names(direct)))
    return {
        "dependencies": names,
        "closure": closure(names, client),
        "reviewed_on": date.today().isoformat(),
        "skipped_non_plain_requirements": rejected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("packages", nargs="+")
    parser.add_argument("--write", action="store_true", help=f"merge the entries into {APPROVED_FILE.relative_to(ROOT)}")
    args = parser.parse_args()

    entries: dict[str, dict] = {}
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for package in args.packages:
            entries[normalize(package)] = review(package, client)
            e = entries[normalize(package)]
            print(f"{package}: direct {e['dependencies']}\n  closure ({len(e['closure'])}): {e['closure']}")

    if args.write:
        current = json.loads(APPROVED_FILE.read_text(encoding="utf-8")) if APPROVED_FILE.exists() else {}
        current.update(entries)
        APPROVED_FILE.write_text(json.dumps(dict(sorted(current.items())), indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {APPROVED_FILE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
