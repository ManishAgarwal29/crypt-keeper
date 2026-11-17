"""The verdict: what the before/after proof-of-life results add up to. Pure."""
from __future__ import annotations

from models import ProofOfLife, Verdict


def judge(before: ProofOfLife | None, after: ProofOfLife | None) -> tuple[Verdict, str]:
    """Compare the original package's proof of life with the revived package's.

    A run on a different Python than the target can show failure but never success, because a
    package that imports on 3.11 may still be dead on 3.12.
    """
    if after is None or after.skipped:
        return "unverified", "Proof of life was not run."

    wrong_python = after.python_used is not None and not after.target_python_matched
    before_ran = before is not None and not before.skipped

    if after.imported:
        if wrong_python:
            return "unverified", (
                f"The revived package imports on Python {after.python_used}, but the target Python is not "
                "installed here, so this does not prove it works on the target."
            )
        if not before_ran:
            return "unverified", "The revived package is alive, but the original was not checked for comparison."
        if not before.imported:
            return "resurrected", f"Dead before ({_short(before.error)}), alive after revival."
        if after.tests_passed > before.tests_passed:
            return "improved", (
                f"Alive before and after, and {after.tests_passed - before.tests_passed} more tests pass "
                f"({before.tests_passed} -> {after.tests_passed})."
            )
        return "already_alive", "The package already worked; revival changed nothing measurable."

    if before_ran and before.imported:
        return "regressed", f"The original imported but the revived package does not: {_short(after.error)}"
    return "still_dead", f"Revival did not bring it back: {_short(after.error)}"


def _short(message: str | None, limit: int = 160) -> str:
    text = (message or "unknown error").strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."
