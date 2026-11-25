"""Declare `setuptools` for code that imports `pkg_resources`, `distutils` or `setuptools`. Pure.

A fresh Python 3.12 virtualenv has no `setuptools`, and `distutils` left the standard library in 3.12, so a package
that imports any of these fails there even though it declares nothing wrong. Checked on 3.12.10 (see
`benchmark/notes.md`):

  * `distutils`      works once `setuptools` is installed (it ships a replacement),
  * `setuptools`     is just the package itself,
  * `pkg_resources`  works with `setuptools<81` and is **missing** from the newest releases (84.2.1 when this was checked).

So the requirement is `setuptools<81` when `pkg_resources` is imported and plain `setuptools` otherwise. Only code that
runs when the package is imported counts: tests, `setup.py`, docs and examples do not, and an import inside
`try/except ImportError` is a deliberate fallback and is left alone.
"""
from __future__ import annotations

import ast
import warnings

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

from coroner.deprecations import guarded_body_node_ids
from proof.discovery import is_runtime_source

PKG_RESOURCES_CAP = "<81"
_MODULES = {"pkg_resources": "pkg_resources", "distutils": "distutils", "setuptools": "setuptools"}


def _is_dynamic_import(call: ast.Call) -> bool:
    """`__import__(...)` or `importlib.import_module(...)` / `import_module(...)`."""
    func = call.func
    return (isinstance(func, ast.Name) and func.id in ("__import__", "import_module")) or (
        isinstance(func, ast.Attribute) and func.attr == "import_module"
    )


def setuptools_need(source_map: dict[str, str]) -> tuple[str | None, list[str]]:
    """(the requirement to declare, where it is needed), or (None, []) when nothing needs it.

    The requirement is `setuptools<81` if any file imports `pkg_resources`, else `setuptools`. Each reason reads
    `path:line imports MODULE`, in path order.
    """
    reasons: list[str] = []
    needs_pkg_resources = False
    for path in sorted(source_map):
        if not is_runtime_source(path):
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tree = ast.parse(source_map[path])
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            continue
        guarded = guarded_body_node_ids(tree)
        for node in ast.walk(tree):
            if id(node) in guarded:
                continue
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            elif isinstance(node, ast.Call) and _is_dynamic_import(node) and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    names = [first.value]  # `__import__("pkg_resources")`; a computed name cannot be known
            for name in names:
                root = name.split(".", 1)[0]
                if root in _MODULES:
                    reasons.append(f"{path}:{node.lineno} imports {root}")
                    needs_pkg_resources = needs_pkg_resources or root == "pkg_resources"
    if not reasons:
        return None, []
    return ("setuptools" + PKG_RESOURCES_CAP if needs_pkg_resources else "setuptools"), reasons


def _has_upper_bound(req: Requirement) -> bool:
    return any(s.operator in ("<", "<=", "==", "~=", "===") for s in req.specifier)


def declare_setuptools(deps: list[str], source_map: dict[str, str]) -> tuple[list[str], list[str], bool]:
    """(new dependency list, what was done, whether setuptools is now a dependency).

    Appends the requirement when none names `setuptools`. When one already does and `pkg_resources` is imported, an
    open-ended requirement gets the `<81` cap, since the newest setuptools has no `pkg_resources`. Anything the
    package already pinned or capped is kept.
    """
    need, reasons = setuptools_need(source_map)
    if need is None:
        return list(deps), [], False
    more = f" (and {len(reasons) - 1} more)" if len(reasons) > 1 else ""
    note = f"declared `{need}`: {reasons[0]}{more}. A fresh Python 3.12 environment has no setuptools."

    out = list(deps)
    for i, raw in enumerate(out):
        try:
            req = Requirement(raw)
        except InvalidRequirement:
            continue
        if canonicalize_name(req.name) != "setuptools":
            continue
        if need.endswith(PKG_RESOURCES_CAP) and not _has_upper_bound(req):
            req.specifier = req.specifier & SpecifierSet(PKG_RESOURCES_CAP)
            out[i] = str(req)
            return out, [f"capped `{raw}` at `{PKG_RESOURCES_CAP}`: {reasons[0]}{more}. The newest setuptools has no pkg_resources."], True
        return out, [], True
    out.append(need)
    return out, [note], True
