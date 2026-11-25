"""Static triage for the Undertaker: is this abandoned package worth reviving, and did that change?

Pure: no network, nothing is executed. It reads an autopsy and a revival that already exist and says what
they add up to. This is a **prediction**, not proof: it can tell that every removed API was rewritten, not
that the package then works. Proof of life (the sandbox) is the check that runs it.

Statuses, in the order they are decided:
  * `needs_human`: after revival something in the code is still wrong (a Python 2 syntax error, a removed
    API the Necromancer cannot rewrite).
  * `blocked`: the code is fine but a dependency still does not support the target Python.
  * `revivable`: the code had breakage and the revival fixed all of it.
  * `alive`: nothing known to break on the target Python.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from models import AutopsyReport, RevivalResult

Status = Literal["alive", "revivable", "needs_human", "blocked"]
CODE_KINDS = {"deprecated-api", "syntax-error"}
BLOCKING_DEPENDENCY_KINDS = {"incompatible-dependency", "yanked-dependency"}


class TriageEntry(BaseModel):
    status: Status
    last_release: str | None = None
    code_findings: int = 0
    files_changed: int = 0
    manual_items: int = 0
    blockers: list[str] = Field(default_factory=list)
    checked_on: str = ""
    error: str | None = Field(default=None, description="set when the package could not be exhumed this time")


Change = Literal["new", "newly_revivable", "no_longer_revivable", "new_release", "status_changed", "unreadable", "readable_again"]
ATTENTION = {"newly_revivable", "new_release", "no_longer_revivable"}


class Observation(BaseModel):
    package: str
    change: Change
    before: Status | None = None
    after: Status | None = None
    detail: str


def triage(report: AutopsyReport, revival: RevivalResult, revived_report: AutopsyReport,
           last_release: str | None, checked_on: str) -> TriageEntry:
    """Decide a package's status from its autopsy, its revival and an autopsy of the revived source."""
    code_before = sum(1 for f in report.findings if f.kind in CODE_KINDS)
    blockers = sorted({f.message for f in revived_report.findings if f.kind in BLOCKING_DEPENDENCY_KINDS})
    if revival.manual_items:
        status: Status = "needs_human"
    elif blockers:
        status = "blocked"
    elif code_before > 0:
        status = "revivable"
    else:
        status = "alive"
    return TriageEntry(
        status=status, last_release=last_release, code_findings=code_before, files_changed=len(revival.diffs),
        manual_items=len(revival.manual_items), blockers=blockers[:5], checked_on=checked_on,
    )


def unreadable(previous: TriageEntry | None, error: str, checked_on: str) -> TriageEntry:
    """An entry for a package that could not be exhumed. It keeps the last known status, so one network blip
    does not look like a change, and records the error."""
    base = previous.model_copy() if previous else TriageEntry(status="needs_human")
    return base.model_copy(update={"error": error[:300], "checked_on": checked_on})


def compare(previous: dict[str, TriageEntry], current: dict[str, TriageEntry]) -> list[Observation]:
    """What changed since the last run, in package-name order. Unchanged packages produce nothing."""
    out: list[Observation] = []
    for name in sorted(current):
        now, before = current[name], previous.get(name)
        if before is None:
            out.append(Observation(package=name, change="new", after=now.status, detail=f"first time seen: {now.status}"))
            continue
        if now.error and not before.error:
            out.append(Observation(package=name, change="unreadable", before=before.status, after=now.status,
                                   detail=f"could not be exhumed: {now.error}"))
            continue
        if before.error and not now.error:
            out.append(Observation(package=name, change="readable_again", before=before.status, after=now.status,
                                   detail="could be exhumed again"))
        # A failure that continues from the last run leaves last_release and status untouched (see `unreadable`),
        # so the comparisons below add nothing for it.
        if before.last_release != now.last_release:
            out.append(Observation(package=name, change="new_release", before=before.status, after=now.status,
                                   detail=f"a new release appeared ({before.last_release} -> {now.last_release}); "
                                          "someone may have revived it upstream"))
        if before.status != now.status:
            if now.status == "revivable":
                change: Change = "newly_revivable"
                detail = f"was {before.status}, now revivable: every removed API in it can be rewritten"
            elif before.status == "revivable":
                change = "no_longer_revivable"
                detail = f"was revivable, now {now.status}"
            else:
                change = "status_changed"
                detail = f"{before.status} -> {now.status}"
            out.append(Observation(package=name, change=change, before=before.status, after=now.status, detail=detail))
    return out


def needs_attention(observations: list[Observation]) -> list[Observation]:
    return [o for o in observations if o.change in ATTENTION]


def render_report(current: dict[str, TriageEntry], observations: list[Observation], today: str, target: str) -> str:
    flagged = needs_attention(observations)
    lines = [f"# Undertaker report, {today}", "",
             f"Watched {len(current)} abandoned packages against Python {target}. This is a static triage: nothing was run.",
             "A package marked `revivable` has had every removed API rewritten, which is a prediction, not proof.",
             "Confirm it with proof of life (`python scripts/smoke_test.py <server> --package NAME` or the web page).", ""]
    lines += ["## Needs your attention", ""]
    lines += [f"- **{o.package}**: {o.detail}" for o in flagged] or ["- Nothing changed that needs a decision."]
    others = [o for o in observations if o not in flagged]
    if others:
        lines += ["", "## Other changes", ""] + [f"- {o.package}: {o.detail}" for o in others]
    lines += ["", "## Every package", "", "| Package | Status | Last release | Code findings | Files rewritten | Left for a person | Blockers |",
              "|---|---|---|---|---|---|---|"]
    for name in sorted(current):
        e = current[name]
        status = f"{e.status} (not reachable: {e.error})" if e.error else e.status
        lines.append(f"| {name} | {status} | {e.last_release or '?'} | {e.code_findings} | {e.files_changed} | {e.manual_items} | "
                     f"{'; '.join(e.blockers) or '-'} |")
    return "\n".join(lines) + "\n"
