"""Success-rate benchmark: run many real, old packages through a Crypt Keeper server and count what happens.

    # 1. Start a server that is allowed to run any package (do this in a container, not on your machine):
    #    docker run --rm -p 8080:8080 -e PORT=8080 -e CRYPT_KEEPER_PUBLIC=off -e CRYPT_KEEPER_PROOF_ALLOWLIST='*' crypt-keeper
    # 2. Run the benchmark. It is resumable: packages already in the output file are skipped.
    python scripts/benchmark.py run http://127.0.0.1:8080 --out benchmark/results.jsonl
    # 3. Write the report.
    python scripts/benchmark.py report benchmark/results.jsonl > docs/BENCHMARK.md

Running a package executes its code (see api/sandbox.py: this is not a security boundary), so point `run` at a
throwaway container. Candidates come from benchmark/packages.txt; the selection rule is applied here, in code,
and every candidate is recorded, including the ones it excludes.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
CUTOFF = "2021-01-01"  # the latest release must be older than this
MAX_HELD_OUT = 42
VERDICTS = ["resurrected", "improved", "already_alive", "still_dead", "regressed", "unverified"]


# --- selection ---------------------------------------------------------------------------------------------------


def read_candidates(path: Path) -> list[tuple[str, str]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            group, name = line.split()
            rows.append((group, name))
    return rows


def last_release(payload: dict) -> str | None:
    """The newest upload date (YYYY-MM-DD) across every release file, or None."""
    stamps = [f.get("upload_time_iso_8601") or f.get("upload_time") for files in (payload.get("releases") or {}).values() for f in files or []]
    stamps = [s for s in stamps if s]
    return max(stamps)[:10] if stamps else None


def eligibility(payload: dict | None, cutoff: str = CUTOFF) -> str | None:
    """None when the package qualifies, else the reason it does not."""
    if payload is None:
        return "not found on PyPI"
    date = last_release(payload)
    if date is None:
        return "no release files"
    if date >= cutoff:
        return f"released {date}, not before {cutoff}"
    if not any(f.get("packagetype") == "sdist" for f in payload.get("urls") or []):
        return "latest release has no sdist"
    return None


def decide(group: str, payload: dict | None, held_out_taken: int) -> tuple[str | None, int]:
    """(reason to exclude or None, held-out packages taken so far). Applies the rule and the cap, in list order."""
    reason = eligibility(payload)
    if reason is None and group == "held-out":
        if held_out_taken >= MAX_HELD_OUT:
            return f"more than {MAX_HELD_OUT} held-out packages qualified earlier in the list", held_out_taken
        return None, held_out_taken + 1
    return reason, held_out_taken


def pypi_payload(name: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


# --- running -------------------------------------------------------------------------------------------------------


def revive_stream(base: str, package: str, target: str, timeout: float) -> dict:
    """Run one package through /revive/stream and return the final result, or {"error": ...}."""
    body = json.dumps({"ref": {"source": "pypi", "identifier": package}, "target_python": target}).encode()
    origin = f"{urlsplit(base).scheme}://{urlsplit(base).netloc}"
    request = urllib.request.Request(base + "/revive/stream", data=body, headers={"Content-Type": "application/json", "Origin": origin})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw in response:
                if not raw.strip():
                    continue
                event = json.loads(raw)
                if event["event"] == "result":
                    return event["data"]
                if event["event"] == "error":
                    return {"error": f"{event.get('status')}: {event.get('detail')}"}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"error": "the stream ended without a result"}


def row_from_result(group: str, name: str, last: str | None, data: dict, seconds: float) -> dict:
    if "error" in data:
        return {"name": name, "group": group, "status": "error", "error": data["error"], "last_release": last, "seconds": round(seconds, 1)}
    b, a = data.get("proof_before") or {}, data.get("proof_after") or {}
    revival = data["revival"]
    return {
        "name": name, "group": group, "status": "ran", "last_release": last, "seconds": round(seconds, 1),
        "verdict": data["verdict"], "reason": data["verdict_reason"],
        "liveness": data["autopsy"]["liveness"], "findings": len(data["autopsy"]["findings"]),
        "files_changed": len(revival["diffs"]), "manual_items": len(revival["manual_items"]),
        "truncated": data["package"]["truncated"],
        "before_imported": b.get("imported"), "after_imported": a.get("imported"),
        "before_skipped": bool(b.get("skipped")), "after_skipped": bool(a.get("skipped")),
        "before_tests": [b.get("tests_passed", 0), b.get("tests_failed", 0), b.get("tests_errors", 0)],
        "after_tests": [a.get("tests_passed", 0), a.get("tests_failed", 0), a.get("tests_errors", 0)],
        "test_source": a.get("test_source"), "after_error": a.get("error"),
    }


def cmd_run(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {json.loads(line)["name"].lower() for line in out.read_text(encoding="utf-8").splitlines() if line.strip()} if out.exists() else set()

    todo: list[tuple[str, str, str | None]] = []  # (group, name, last_release)
    held_out = sum(1 for line in (out.read_text(encoding="utf-8").splitlines() if out.exists() else [])
                   if (r := json.loads(line))["group"] in ("held-out", args.relabel) and r["status"] != "excluded"
                   and r["group"] != "seen")
    with out.open("a", encoding="utf-8") as sink:
        for group, name in read_candidates(Path(args.packages)):
            if name.lower() in done:
                continue
            payload = pypi_payload(name)
            reason, held_out = decide(group, payload, held_out)
            if reason:
                sink.write(json.dumps({"name": name, "group": args.relabel or group, "status": "excluded", "reason": reason}) + "\n")
                print(f"EXCLUDED {name}: {reason}", flush=True)
                continue
            todo.append((group, name, last_release(payload)))
        sink.flush()
        print(f"{len(todo)} packages to run, concurrency {args.concurrency}", flush=True)

        def work(item: tuple[str, str, str | None]) -> dict:
            group, name, last = item
            started = time.monotonic()
            data = revive_stream(args.base_url.rstrip("/"), name, args.target, args.timeout)
            return row_from_result(args.relabel or group, name, last, data, time.monotonic() - started)

        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            for row in pool.map(work, todo):
                sink.write(json.dumps(row) + "\n")
                sink.flush()
                print(f"{row['name']:<20} {row.get('verdict') or row.get('error')}  ({row['seconds']}s)", flush=True)
    return 0


# --- reporting -------------------------------------------------------------------------------------------------------


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion. (0, 0) when n is 0."""
    if n == 0:
        return 0.0, 0.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def failure_cause(row: dict) -> str:
    """A coarse reason a package is still not importable after revival, from the sandbox's own error text."""
    text = (row.get("after_error") or row.get("reason") or "").lower()
    if "could not install" in text:
        return "a dependency could not be installed (wheel-only)"
    if "syntaxerror" in text:
        return "Python 2 syntax (not converted)"
    if "no module named" in text:
        return "a module is missing or was removed from Python"
    if "cannot import name" in text:
        return "a name was removed from a dependency or the standard library"
    if "attributeerror" in text:
        return "an attribute was removed or changed"
    if "timed out" in text:
        return "the import timed out"
    return "other"


def pct(successes: int, n: int) -> str:
    if n == 0:
        return "n/a"
    lo, hi = wilson(successes, n)
    return f"{successes}/{n} = {100 * successes / n:.0f}% (95% CI {100 * lo:.0f}% to {100 * hi:.0f}%)"


def summarize(rows: list[dict], group: str | None = None) -> dict:
    every = [r for r in rows if r["status"] == "ran" and (group is None or r["group"] == group)]
    # A package with no Python source of its own (a placeholder that installs another one) has nothing to import: it is
    # neither dead nor revived, so it is counted apart and kept out of every rate. Older result files have no such rows.
    nothing = [r for r in every if r.get("before_skipped") or r.get("after_skipped")]
    ran = [r for r in every if r not in nothing]
    dead = [r for r in ran if r["before_imported"] is False]
    return {
        "nothing_to_run": len(nothing),
        "ran": len(ran),
        "dead_before": len(dead),
        "resurrected": sum(1 for r in dead if r["after_imported"]),
        "alive_before": sum(1 for r in ran if r["before_imported"]),
        "alive_after": sum(1 for r in ran if r["after_imported"]),
        "verdicts": Counter(r["verdict"] for r in every),
        "still_dead_causes": Counter(failure_cause(r) for r in ran if r["after_imported"] is False),
        "errors": [r for r in rows if r["status"] == "error" and (group is None or r["group"] == group)],
        "median_seconds": sorted(r["seconds"] for r in ran)[len(ran) // 2] if ran else 0,
    }


def render_report(rows: list[dict], source: str = "benchmark/results.jsonl") -> str:
    held, seen = summarize(rows, "held-out"), summarize(rows, "seen")
    excluded = [r for r in rows if r["status"] == "excluded"]
    lines = [
        "# Benchmark: how often does Crypt Keeper revive a dead package?",
        "",
        f"Generated by `scripts/benchmark.py report` from `{source}`. Python 3.12, a fresh container, wheel-only installs.",
        "",
        "## The result",
        "",
        f"**Held-out packages** (never used while building Crypt Keeper): {held['ran']} ran"
        + (f", and {held['nothing_to_run']} more had no Python source of their own (placeholders), so they are in no rate." if held["nothing_to_run"] else "."),
        "",
        f"- Dead on Python 3.12 before revival: {pct(held['dead_before'], held['ran'])} of those that ran.",
        f"- **Of the dead ones, brought back to importable: {pct(held['resurrected'], held['dead_before'])}.**",
        f"- Importable after revival, dead or not: {pct(held['alive_after'], held['ran'])}.",
        f"- Median time per package: {held['median_seconds']} s.",
        "",
        "\"Brought back\" means: the original could not be imported on Python 3.12, and the revived one can, with its",
        "declared dependencies installed from wheels. It does **not** mean every test passes. The per-package table",
        "gives the test counts.",
        "",
        "## Verdicts (held-out)",
        "",
        "| Verdict | Packages |", "|---|---|",
    ] + [f"| {v} | {held['verdicts'].get(v, 0)} |" for v in VERDICTS] + [
        f"| error (the run did not finish) | {len(held['errors'])} |",
        "",
        "## Why the rest are still dead (held-out)",
        "",
    ]
    causes = held["still_dead_causes"]
    lines += [f"- {c}: {n}" for c, n in causes.most_common()] or ["- None."]
    if seen["ran"]:
        lines += [
            "",
            f"## Packages used while developing Crypt Keeper ({seen['ran']} ran)",
            "",
            "The code was tuned against these, so they are not part of the headline rate.",
            f"Dead before: {seen['dead_before']}; brought back: {seen['resurrected']}; importable after: {seen['alive_after']}.",
        ]
    lines += [
        "",
        "## Every package",
        "",
        "| Package | Group | Last release | Before | After | Verdict | Tests before (pass/fail/err) | Tests after | Files changed | Seconds |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted((r for r in rows if r["status"] == "ran"), key=lambda r: (r["group"], r["name"].lower())):
        def yn(imported, skipped):
            return "nothing to run" if skipped else "alive" if imported else "dead"

        lines.append(
            f"| {r['name']} | {r['group']} | {r['last_release']} | {yn(r['before_imported'], r.get('before_skipped'))} | "
            f"{yn(r['after_imported'], r.get('after_skipped'))} | {r['verdict']} | "
            f"{'/'.join(map(str, r['before_tests']))} | {'/'.join(map(str, r['after_tests']))} | {r['files_changed']} | {r['seconds']} |"
        )
    if any(r["status"] == "error" for r in rows):
        lines += ["", "## Runs that did not finish", ""]
        lines += [f"- {r['name']} ({r['group']}): {r['error']}" for r in rows if r["status"] == "error"]
    lines += ["", "## Candidates the rule excluded", ""]
    lines += [f"- {r['name']} ({r['group']}): {r['reason']}" for r in excluded] or ["- None."]
    lines += [
        "",
        "## How to read this honestly",
        "",
        "- The candidates are old packages picked by a written rule (`benchmark/packages.txt`), not a random sample of all",
        "  abandoned packages, so the rate describes this list, not PyPI as a whole.",
        "- Many old packages still work on 3.12. They are counted as alive before, not as wins.",
        "- A package \"importable after revival\" can still have failing tests. See the test columns.",
        "- Only the latest release is tested, and only its sdist `.py` files. C extensions and data files are not exhumed.",
        "- Dependencies install from wheels only. A package that needs a source build for a dependency fails here.",
        "- Each run happens once. A flaky network or a PyPI change can move a result.",
        "",
    ]
    return "\n".join(lines)


def render_history(entries: list[tuple[str, list[dict]]]) -> str:
    """One row per run: what was measured, on which code, and the rate among dead packages. Every group counts here,
    because each row is one result file; the label says whether the list was clean."""
    lines = ["| Run | Packages | Dead before | Brought back | Rate among the dead (95% CI) | Nothing to run |", "|---|---|---|---|---|---|"]
    for label, rows in entries:
        s = summarize(rows)
        lines.append(f"| {label} | {s['ran']} | {s['dead_before']} | {s['resurrected']} | {pct(s['resurrected'], s['dead_before'])} | {s['nothing_to_run']} |")
    return "\n".join(lines) + "\n"


def cmd_history(args: argparse.Namespace) -> int:
    entries = []
    for spec in args.runs:
        label, _, path = spec.partition("=")
        entries.append((label, load_rows(path)))
    sys.stdout.reconfigure(encoding="utf-8")
    print(render_history(entries))
    return 0


def load_rows(path: str | Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def render_comparison(before: list[dict], after: list[dict], label_before: str, label_after: str) -> str:
    """Packages that ran in both result sets, and the ones whose outcome differs."""
    was = {r["name"].lower(): r for r in before if r["status"] == "ran"}
    now = {r["name"].lower(): r for r in after if r["status"] == "ran"}
    common = sorted(set(was) & set(now))
    moved = [(was[k], now[k]) for k in common if (was[k]["verdict"], was[k]["after_imported"]) != (now[k]["verdict"], now[k]["after_imported"])]
    lines = [f"## {label_before} against {label_after}, same packages", "",
             f"{len(common)} packages ran in both. {len(moved)} changed outcome.", ""]
    if moved:
        lines += ["| Package | Before the fixes | After the fixes |", "|---|---|---|"]
        lines += [f"| {a['name']} | {a['verdict']} | {b['verdict']} |" for a, b in moved]
    else:
        lines += ["No package changed outcome."]
    return "\n".join(lines) + "\n"


def cmd_report(args: argparse.Namespace) -> int:
    rows = load_rows(args.results)
    sys.stdout.reconfigure(encoding="utf-8")
    print(render_report(rows, args.results.replace("\\", "/")))
    if args.compare:
        print(render_comparison(load_rows(args.compare), rows, "First run", "after the fixes"))
    if args.notes:
        print(Path(args.notes).read_text(encoding="utf-8"))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("base_url")
    run.add_argument("--packages", default=str(ROOT / "benchmark" / "packages.txt"))
    run.add_argument("--out", default=str(ROOT / "benchmark" / "results.jsonl"))
    run.add_argument("--target", default="3.12")
    run.add_argument("--concurrency", type=int, default=2)
    run.add_argument("--timeout", type=float, default=900)
    run.add_argument("--relabel", help="record every row under this group (for a rerun of packages that are no longer held-out)")
    run.set_defaults(func=cmd_run)
    report = sub.add_parser("report")
    report.add_argument("results")
    report.add_argument("--compare", help="an earlier results file for the same packages, to show what changed")
    report.add_argument("--notes", help="a hand-written markdown file to append")
    report.set_defaults(func=cmd_report)
    history = sub.add_parser("history", help="one table row per result file: LABEL=path ...")
    history.add_argument("runs", nargs="+")
    history.set_defaults(func=cmd_history)
    compare = sub.add_parser("compare", help="what changed between two result files for the same packages")
    compare.add_argument("before")
    compare.add_argument("after")
    compare.set_defaults(func=lambda a: (sys.stdout.reconfigure(encoding="utf-8"),
                                         print(render_comparison(load_rows(a.before), load_rows(a.after), "First run", "after the fixes")))[0] or 0)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
