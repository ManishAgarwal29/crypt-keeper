"""The Undertaker: re-triage a watchlist of abandoned packages and report what changed since last time.

    python undertaker/undertake.py                      # reads undertaker/watchlist.txt, updates state.json, writes REPORT.md
    python undertaker/undertake.py --target 3.13 --state /tmp/s.json

It is deterministic and static: it fetches each package from PyPI, runs the Coroner and the Necromancer, and
compares the outcome with the previous run. It executes no package code. A person (or the Kiro Crew job that runs
this nightly, see undertaker/README.md) decides what to verify with proof of life.

Exit code is 0 when the run completed, even if packages could not be fetched (those are reported). It is 2 for
a bad watchlist or state file.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))

from api.exhumer import ExhumeError, exhume_pypi  # noqa: E402
from coroner.autopsy import run_autopsy  # noqa: E402
from necromancer.revive import revive, revived_snapshot  # noqa: E402
from necromancer.triage import (  # noqa: E402
    TriageEntry,
    compare,
    needs_attention,
    render_report,
    triage,
    unreadable,
)

STATE_VERSION = 1


def read_watchlist(path: Path) -> list[str]:
    names: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line and line.lower() not in (n.lower() for n in names):
            names.append(line)
    return names


def read_state(path: Path) -> dict[str, TriageEntry]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != STATE_VERSION or not isinstance(data.get("packages"), dict):
        raise ValueError(f"{path} is not a version {STATE_VERSION} state file")
    return {name: TriageEntry.model_validate(entry) for name, entry in data["packages"].items()}


def write_state(path: Path, entries: dict[str, TriageEntry]) -> None:
    payload = {"version": STATE_VERSION, "packages": {n: entries[n].model_dump(mode="json") for n in sorted(entries)}}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def triage_package(name: str, target: str, client: httpx.Client, today: date) -> TriageEntry:
    """Exhume, diagnose, revive and triage one package. Raises ExhumeError if it cannot be fetched."""
    snapshot = exhume_pypi(name, target, client)
    report = run_autopsy(snapshot, target, today)
    # Python 2 conversion is mechanical and unproven until the package runs. The Undertaker only reads, so it must not
    # call a Python 2 package "revivable" on the strength of a conversion it never ran.
    revival = revive(snapshot, report, convert_python2=False)
    revived_report = run_autopsy(revived_snapshot(snapshot, revival), target, today)
    return triage(report, revival, revived_report, snapshot.last_release_date, today.isoformat())


def undertake(names: list[str], previous: dict[str, TriageEntry], target: str, client: httpx.Client, today: date):
    """Triage every name. Returns (current entries, observations). A package that cannot be fetched keeps its last status."""
    current: dict[str, TriageEntry] = {}
    for name in names:
        try:
            current[name] = triage_package(name, target, client, today)
        except ExhumeError as exc:
            current[name] = unreadable(previous.get(name), exc.message, today.isoformat())
        except Exception as exc:  # one bad package must not stop the night's run
            current[name] = unreadable(previous.get(name), f"{type(exc).__name__}: {exc}", today.isoformat())
    return current, compare(previous, current)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--watchlist", type=Path, default=HERE / "watchlist.txt")
    parser.add_argument("--state", type=Path, default=HERE / "state.json")
    parser.add_argument("--report", type=Path, default=HERE / "REPORT.md")
    parser.add_argument("--target", default="3.12")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    try:
        names = read_watchlist(args.watchlist)
        previous = read_state(args.state)
    except (OSError, ValueError) as exc:
        print(f"Cannot start: {exc}", file=sys.stderr)
        return 2

    today = date.today()
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        current, observations = undertake(names, previous, args.target, client, today)

    # Packages that left the watchlist are dropped from the state, so a re-added one starts as "new".
    write_state(args.state, current)
    args.report.write_text(render_report(current, observations, today.isoformat(), args.target), encoding="utf-8")

    flagged = needs_attention(observations)
    print(f"Watched {len(current)} packages; {len(observations)} changes; {len(flagged)} need attention.")
    for o in flagged:
        print(f"  ATTENTION {o.package}: {o.detail}")
    print(f"Report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
