"""Does the static autopsy name why a dead package is dead?

The benchmark (scripts/benchmark.py) counts revivals. This script asks a different question about the
same packages: for the dead ones, did the Coroner's autopsy (static, nothing is executed) name the cause?

    python scripts/diagnosis_check.py run    --out benchmark/diagnosis-check.json     # network: reads PyPI sdists in memory
    python scripts/diagnosis_check.py report benchmark/diagnosis-check.json > docs/DIAGNOSIS.md

The ground truth is the author's hand reading of each package's recorded failure (benchmark/notes.md), fixed
below in TRUTH. The matching rule is in `names_cause` and is deliberately strict.

`run` downloads and reads source archives in memory through the app's own exhumer and runs the pure Coroner.
It runs no package code.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET_PYTHON = "3.12"

CAUSE_LABELS = {
    "python2": "Python 2 code",
    "dependency_removed": "A dependency removed a name the package uses",
    "undeclared_import": "Imports something it never declares",
    "install_failure": "A declared dependency could not be installed from a wheel",
}


@dataclass(frozen=True)
class Truth:
    name: str
    cause: str                      # a key of CAUSE_LABELS
    observed: str                   # the failure the proof-of-life run recorded
    culprits: tuple[str, ...] = ()  # names the autopsy must mention (not used for python2)


# The 19 packages in the final held-out list (benchmark/results-run4-third-final.jsonl) that were not importable
# after revival, labelled by hand in benchmark/notes.md. The placeholder (dnspython3) has nothing to diagnose.
TRUTH: tuple[Truth, ...] = (
    Truth("webapp2", "python2", "SyntaxError: multiple exception types must be parenthesized"),
    Truth("mongokit", "python2", "No module named 'cursor' (implicit relative import)"),
    Truth("pycassa", "python2", "SyntaxError: lambda expression parameters cannot be parenthesized"),
    Truth("pydns", "python2", "No module named 'Type' (implicit relative import)"),
    Truth("pyexcelerator", "python2", "No module named 'Workbook' (implicit relative import)"),
    Truth("ClientForm", "python2", "SyntaxError: cannot assign to True"),
    Truth("requests-oauth", "python2", "No module named 'hook' (implicit relative import)"),
    Truth("couchdbkit", "python2", "SyntaxError: multiple exception types must be parenthesized"),
    Truth("Flask-Triangle", "dependency_removed", "cannot import name 'evalcontextfilter' from 'jinja2'", ("jinja2",)),
    Truth("Flask-Autodoc", "dependency_removed", "No module named 'flask.ext'", ("flask.ext",)),
    Truth("Flask-Classy", "dependency_removed", "cannot import name 'parse_rule' from 'werkzeug.routing'", ("werkzeug",)),
    Truth("minimongo", "dependency_removed", "cannot import name 'Connection' from 'pymongo'", ("pymongo",)),
    Truth("pysftp", "dependency_removed", "cannot import name 'DSSKey' from 'paramiko'", ("paramiko",)),
    Truth("nose-testconfig", "undeclared_import", "No module named 'nose'", ("nose",)),
    Truth("nose-ignore-docstring", "undeclared_import", "No module named 'nose'", ("nose",)),
    Truth("bottle-sqlite", "undeclared_import", "No module named 'bottle'", ("bottle",)),
    Truth("ipcalc", "undeclared_import", "No module named 'six'", ("six",)),
    Truth("Pylons", "install_failure", "could not install WebHelpers, WebError", ("webhelpers", "weberror")),
    Truth("Flask-MongoAlchemy", "install_failure", "could not install MongoAlchemy>=0.19", ("mongoalchemy",)),
)


# --- the matching rule (pure) -------------------------------------------------------------------------------------


def mentions(text: str, token: str) -> bool:
    """True when `token` appears in `text` as a whole name, not inside a longer word ("nose" is not in "diagnose")."""
    return re.search(rf"(?<![\w.]){re.escape(token)}(?![\w])", text, re.IGNORECASE) is not None


def names_cause(truth: Truth, findings: list[dict]) -> bool:
    """Did the autopsy name this package's cause?

    * Python 2 code: the autopsy has a `syntax-error` finding (it does not parse as Python 3).
    * Anything else: some finding's MESSAGE (not its file path) names the dependency or module that broke it.
      The `abandoned-package` finding never counts: it is about age, not cause.
    """
    if truth.cause == "python2":
        return any(f.get("kind") == "syntax-error" for f in findings)
    return any(
        f.get("kind") != "abandoned-package" and mentions(f.get("message", ""), token)
        for f in findings
        for token in truth.culprits
    )


def flags_python2(findings: list[dict]) -> bool:
    return any(f.get("kind") == "syntax-error" for f in findings)


def score(truths: tuple[Truth, ...], results: dict[str, dict]) -> dict:
    """Totals per cause. A package whose exhume failed is `unchecked` and counts in no denominator."""
    by_cause: dict[str, dict[str, int]] = {c: {"named": 0, "checked": 0} for c in CAUSE_LABELS}
    unchecked: list[str] = []
    named_total = checked_total = 0
    for t in truths:
        res = results.get(t.name)
        if not res or "error" in res:
            unchecked.append(t.name)
            continue
        checked_total += 1
        by_cause[t.cause]["checked"] += 1
        if names_cause(t, res["findings"]):
            named_total += 1
            by_cause[t.cause]["named"] += 1
    return {"named": named_total, "checked": checked_total, "by_cause": by_cause, "unchecked": unchecked}


def python2_surprises(truths: tuple[Truth, ...], results: dict[str, dict]) -> list[str]:
    """Packages hand-labelled with a cause other than Python 2, where the autopsy still found Python 2 syntax."""
    return [
        t.name for t in truths
        if t.cause != "python2" and t.name in results and "error" not in results[t.name]
        and flags_python2(results[t.name]["findings"])
    ]


# --- the report (pure) --------------------------------------------------------------------------------------------


def _kinds(findings: list[dict]) -> str:
    counts: dict[str, int] = {}
    for f in findings:
        if f["kind"] != "abandoned-package":
            counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    return ", ".join(f"{k} x{n}" for k, n in sorted(counts.items())) or "nothing beyond age"


def render(results: dict[str, dict], run_date: str | None = None) -> str:
    s = score(TRUTH, results)
    lines = [
        "# Does the autopsy name the cause?",
        "",
        "Generated by `scripts/diagnosis_check.py report`. This is a different question from the benchmark: for the "
        "dead packages in the final held-out list, did the **static** autopsy (the Coroner; nothing is executed) "
        "name why they are dead?",
        "",
        f"**It named the cause for {s['named']} of {s['checked']} packages.**",
        "",
        "| Cause (hand-read) | Autopsy named it |",
        "|---|---|",
    ]
    for cause, label in CAUSE_LABELS.items():
        c = s["by_cause"][cause]
        lines.append(f"| {label} | {c['named']} of {c['checked']} |")
    if s["unchecked"]:
        lines += ["", f"Not checked (the source could not be read): {', '.join(s['unchecked'])}."]
    lines += ["", "## How a match is decided", "",
              "- **Python 2 code:** the autopsy reports a `syntax-error` finding (the source does not parse as Python 3).",
              "- **Every other cause:** a finding's message must name the dependency or module that broke the package "
              "(a whole-word match). A file path does not count, and neither does the age finding.",
              "", "## Every package", "",
              "| Package | Cause (hand-read) | What failed when it ran | Named? | What the autopsy raised |",
              "|---|---|---|---|---|"]
    for t in TRUTH:
        res = results.get(t.name)
        if not res or "error" in res:
            lines.append(f"| {t.name} | {CAUSE_LABELS[t.cause]} | {t.observed} | not checked | |")
            continue
        verdict = "yes" if names_cause(t, res["findings"]) else "no"
        lines.append(f"| {t.name} | {CAUSE_LABELS[t.cause]} | {t.observed} | {verdict} | {_kinds(res['findings'])} |")
    surprises = python2_surprises(TRUTH, results)
    if surprises:
        lines += ["", "## Where the autopsy saw more than the hand reading", "",
                  "The hand reading records the first failure each package hit when it ran. For these packages the "
                  "autopsy also found Python 2 syntax, which the first failure did not show: "
                  f"{', '.join(surprises)}. Fixing the named cause would not have been enough.", ""]
    lines += ["", "## What this does and does not show", "",
              "- The causes were labelled by the author from the recorded failures, so they are not an independent "
              "ground truth.",
              "- The matching rule was written after the autopsy output had been read. Treat the totals as indicative, "
              "not as a clean measurement.",
              "- For the packages whose dependency could not be installed, \"named\" means the autopsy flagged that "
              "dependency as old or hard-pinned. It did not predict that the install would fail.",
              "- 19 packages, one list, one day. The Coroner has tables for Python 2 syntax and removed standard-library "
              "APIs; it has none for names a third-party dependency removed, and it does not look for imports a package "
              "forgot to declare. The totals reflect that.",
              "- When a package is run, the proof-of-life error names the exact failing import for every one of these. "
              "That is observed, not predicted, and it is where the hand-read causes came from."]
    if run_date:
        lines += ["", f"Run on {run_date}."]
    return "\n".join(lines) + "\n"


# --- the run (network: reads PyPI sdists in memory, executes nothing) --------------------------------------------


def run(out: Path) -> None:
    sys.path.insert(0, str(ROOT / "backend"))
    import httpx
    from api.exhumer import exhume
    from coroner.autopsy import run_autopsy
    from models import PackageRef

    results: dict[str, dict] = {}
    with httpx.Client(timeout=40, follow_redirects=True) as client:
        for t in TRUTH:
            try:
                snapshot = exhume(PackageRef(source="pypi", identifier=t.name), TARGET_PYTHON, client)
                report = run_autopsy(snapshot, TARGET_PYTHON, date.today())
                results[t.name] = {
                    "liveness": report.liveness.value,
                    "truncated": snapshot.truncated,
                    "findings": [{"kind": f.kind, "severity": f.severity, "message": f.message, "location": f.location}
                                 for f in report.findings],
                }
            except Exception as exc:  # one unreadable package must not stop the rest
                results[t.name] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
            print(f"{t.name}: {'ERROR' if 'error' in results[t.name] else 'ok'}", file=sys.stderr)
    out.write_text(json.dumps({"run_date": date.today().isoformat(), "results": results}, indent=1), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="exhume each package, run the autopsy, save the findings")
    p_run.add_argument("--out", type=Path, default=ROOT / "benchmark" / "diagnosis-check.json")
    p_rep = sub.add_parser("report", help="print the markdown report from a saved run")
    p_rep.add_argument("results", type=Path)
    args = parser.parse_args(argv)
    if args.command == "run":
        run(args.out)
        return 0
    data = json.loads(args.results.read_text(encoding="utf-8"))
    sys.stdout.write(render(data["results"], data.get("run_date")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
