"""Why a revived package still does not import, and what a person can do next. Pure.

It reads the error the sandbox reported (the exact exception text) and the package's own file list, and names one
cause with one next step. It is a reading of an error message, so it can be wrong: the category says which rule
matched, and the summary quotes the error, so a reader can check it.
"""
from __future__ import annotations

import re
from posixpath import basename, dirname

from coroner.deprecations import REMOVED_MODULES
from models import FailureHint, ProofOfLife

_NO_MODULE = re.compile(r"No module named '([\w.]+)'")
_CANNOT_IMPORT = re.compile(r"cannot import name '(\w+)' from '([\w.]+)'(?: \(([^)]*)\))?")
_NO_ATTRIBUTE = re.compile(r"module '([\w.]+)' has no attribute '(\w+)'")
_COULD_NOT_INSTALL = re.compile(r"could not install: ([^)]+)\)")
_SITE_PACKAGES = re.compile(r"site-packages/([\w.-]+)")


def _removed_module(name: str) -> tuple[str, str] | None:
    """(the removed module, the advice) when `name` or a parent of it left the standard library."""
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        prefix = ".".join(parts[:i])
        if prefix in REMOVED_MODULES:
            removal = REMOVED_MODULES[prefix]
            version = "Python 3" if removal.removed_in == (3, 0) else f"Python {removal.removed_in[0]}.{removal.removed_in[1]}"
            return prefix, f"`{prefix}` is not in {version}; {removal.advice}"
    return None


def _sibling(name: str, paths: list[str]) -> str | None:
    """The package's own file that `name` most likely refers to, if a module of that name sits inside a package."""
    top = name.split(".", 1)[0]
    for p in paths:
        stem = basename(p)[:-3] if p.endswith(".py") else ""
        if dirname(p) and (stem == top or basename(dirname(p)) == top and basename(p) == "__init__.py"):
            return p
    return None


def _is_top_level(name: str, paths: list[str]) -> bool:
    top = name.split(".", 1)[0]
    return any(p in (f"{top}.py", f"{top}/__init__.py") or p.endswith(f"/{top}/__init__.py") and p.count("/") == 1 for p in paths)


def _quote(error: str, limit: int = 160) -> str:
    text = " ".join(error.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def diagnose(proof: ProofOfLife | None, paths: list[str]) -> FailureHint | None:
    """One cause and one next step for a run that did not import. None if it imported, or did not run."""
    if proof is None or proof.imported:
        return None
    error = proof.error or ""
    if proof.skipped:
        if "nothing to run" in error:
            return FailureHint(category="nothing-to-run", summary=_quote(error),
                               next_step="Check the package it installs instead (its dependencies are listed above).")
        return None

    installs = _COULD_NOT_INSTALL.search(error)
    if "SyntaxError" in error:
        return FailureHint(
            category="python2-syntax", summary=_quote(error),
            next_step="This is Python 2 code that the automatic conversion could not finish. Run `2to3 -w` on the file "
                      "named in the error and fix what remains by hand.",
        )
    if "DistributionNotFound" in error:
        return FailureHint(
            category="looks-itself-up", summary=_quote(error),
            next_step="The package asks pkg_resources for its own installed metadata. Install it (`pip install .` in its "
                      "folder) instead of putting its source on the path.",
        )
    if match := _NO_MODULE.search(error):
        name = match.group(1)
        if removed := _removed_module(name):
            return FailureHint(category="removed-from-python", summary=_quote(error), next_step=removed[1] + ".")
        if (sibling := _sibling(name, paths)) and not _is_top_level(name, paths):
            return FailureHint(
                category="python2-relative-import", summary=_quote(error),
                next_step=f"`import {name.split('.', 1)[0]}` means the sibling file `{sibling}` (a Python 2 implicit relative "
                          f"import). Change it to `from . import {name.split('.', 1)[0]}`.",
            )
        if installs:
            return FailureHint(
                category="dependency-not-installable", summary=_quote(error),
                next_step=f"`{installs.group(1).strip()}` has no wheel for this Python. Install it from source locally "
                          "(set CRYPT_KEEPER_ALLOW_SOURCE_BUILDS=1 on a local server) or find a release that has one.",
            )
        return FailureHint(
            category="undeclared-dependency", summary=_quote(error),
            next_step=f"The package imports `{name.split('.', 1)[0]}` but does not declare it. Add the project that "
                      "provides it to the requirements.",
        )
    if installs:
        return FailureHint(
            category="dependency-not-installable", summary=_quote(error),
            next_step=f"`{installs.group(1).strip()}` has no wheel for this Python. Install it from source locally "
                      "(set CRYPT_KEEPER_ALLOW_SOURCE_BUILDS=1 on a local server) or find a release that has one.",
        )
    if match := _CANNOT_IMPORT.search(error):
        name, module, where = match.group(1), match.group(2), match.group(3) or ""
        site = _SITE_PACKAGES.search(where.replace("\\", "/"))
        if site:
            return FailureHint(
                category="dependency-removed-a-name", summary=_quote(error),
                next_step=f"A newer release of `{module.split('.', 1)[0]}` removed `{name}`. Change the code to its "
                          f"replacement, or pin `{module.split('.', 1)[0]}` to an older release if one installs on this "
                          "Python (old releases of compiled libraries often do not).",
            )
        return FailureHint(
            category="removed-from-python", summary=_quote(error),
            next_step=f"`{module}.{name}` is no longer in the standard library. Look up its replacement in the Python "
                      "release notes (What's New, section Removed).",
        )
    if match := _NO_ATTRIBUTE.search(error):
        module, name = match.group(1), match.group(2)
        return FailureHint(
            category="attribute-removed", summary=_quote(error),
            next_step=f"`{module}.{name}` no longer exists. If `{module}` is a dependency, change the code to its replacement, "
                      "or pin it to an older release if one installs on this Python; if it is the standard library, use "
                      "its replacement.",
        )
    if "timed out" in error:
        return FailureHint(category="timed-out", summary=_quote(error),
                           next_step="Importing it takes too long or waits for something (a network service, input). "
                                     "Run it locally to see where it stops.")
    if "limit" in error and ("output" in error or "disk" in error):
        return FailureHint(category="resource-limit", summary=_quote(error),
                           next_step="Importing it writes a great deal. Run it locally to see what it produces.")
    return FailureHint(category="other", summary=_quote(error) or "The import failed without an error message.",
                       next_step="Read the sandbox log below for the full traceback.")
