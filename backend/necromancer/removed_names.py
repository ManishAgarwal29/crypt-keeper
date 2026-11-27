"""Cap a dependency below the release that removed a name the package uses. Pure.

Many old packages break not because of Python but because a dependency they use later deleted something: Jinja2 3.1
removed `evalcontextfilter` and `Markup`, Werkzeug 2.2 removed `parse_rule`, paramiko 4 removed `DSSKey`. The fix is the
requirement the author would have written at the time: stay below the release that removed it.

Every entry in REMOVALS was checked on Python 3.12 by installing wheels only (the same rule as the sandbox): the pins
resolve, the name imports, and the release above the ceiling does not have it. The evidence is in `benchmark/notes.md`.
A name that is not in this table is never guessed; the autopsy and the proof-of-life error still report it.

Only code that runs at import counts: tests, `setup.py`, docs and examples do not, and an import inside
`try/except ImportError` is a deliberate fallback and is left alone.
"""
from __future__ import annotations

import ast
import warnings
from dataclasses import dataclass

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

from coroner.deprecations import guarded_body_node_ids
from proof.discovery import is_runtime_source


@dataclass(frozen=True)
class Removal:
    module: str                  # the module the names are imported from, or the module path itself when `names` is empty
    names: frozenset[str]        # names removed from `module`; empty means the whole module was removed
    pins: tuple[str, ...]        # requirements that bring it back, as checked on Python 3.12
    what: str                    # what happened, for the log


# `import flask.ext` only works on the Flask 0.x line, which needs old companions to import at all (checked together).
_OLD_FLASK = ("Flask<1.0", "Werkzeug<1.0", "Jinja2<3.1", "itsdangerous<2.1")

REMOVALS: tuple[Removal, ...] = (
    Removal("jinja2", frozenset({"evalcontextfilter", "contextfilter", "environmentfilter", "evalcontextfunction",
                                 "contextfunction", "environmentfunction", "Markup", "escape"}),
            ("Jinja2<3.1",), "Jinja2 3.1 removed the context and environment helpers and Markup/escape"),
    Removal("itsdangerous", frozenset({"TimedJSONWebSignatureSerializer", "JSONWebSignatureSerializer"}),
            ("itsdangerous<2.1",), "itsdangerous 2.1 removed the JSON web signature serializers"),
    Removal("itsdangerous.json", frozenset(), ("itsdangerous<2.1",), "itsdangerous 2.1 removed itsdangerous.json"),
    Removal("paramiko", frozenset({"DSSKey"}), ("paramiko<4",), "paramiko 4 removed DSSKey"),
    Removal("werkzeug.routing", frozenset({"parse_rule"}), ("Werkzeug<2.2",), "Werkzeug 2.2 removed routing.parse_rule"),
    Removal("werkzeug.security", frozenset({"safe_str_cmp"}), ("Werkzeug<2.1",), "Werkzeug 2.1 removed security.safe_str_cmp"),
    Removal("werkzeug.contrib", frozenset(), ("Werkzeug<1.0",), "Werkzeug 1.0 removed werkzeug.contrib"),
    Removal("werkzeug", frozenset({"cached_property", "url_quote", "url_encode"}), ("Werkzeug<1.0",),
            "Werkzeug 1.0 removed these top-level names"),
    Removal("werkzeug.urls", frozenset({"url_quote"}), ("Werkzeug<3",), "Werkzeug 3.0 removed urls.url_quote"),
    Removal("flask.ext", frozenset(), _OLD_FLASK, "Flask 1.0 removed flask.ext"),
    Removal("flask", frozenset({"Markup", "escape", "_request_ctx_stack", "_app_ctx_stack"}), ("Flask<3", "Werkzeug<3"),
            "Flask 3.0 removed Markup, escape and the private context stacks"),
    Removal("flask.json", frozenset({"JSONEncoder"}), ("Flask<2.3", "Werkzeug<2.3"), "Flask 2.3 removed json.JSONEncoder"),
)


def _ceiling(pin: str) -> tuple[str, Version]:
    req = Requirement(pin)
    (spec,) = req.specifier
    assert spec.operator == "<", pin
    return canonicalize_name(req.name), Version(spec.version)


def needed_caps(source_map: dict[str, str]) -> frozenset[tuple[str, str]]:
    """(canonical name, ceiling as written) for every cap this code calls for.

    `bump_dependencies` strips upper bounds; it keeps exactly these, so reviving an already revived snapshot does not
    undo a cap and re-add it. A cap the code does not need is not kept: a package that declared `Flask<1.0` itself,
    without using `flask.ext`, still has that bound removed.
    """
    return frozenset(
        (canonicalize_name(Requirement(p).name), next(iter(Requirement(p).specifier)).version)
        for r, *_ in removed_name_uses(source_map) for p in r.pins
    )


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def removed_name_uses(source_map: dict[str, str]) -> list[tuple[Removal, str, int, str]]:
    """Every runtime use of a removed name: (entry, path, line, what was written), in path order, once per entry and file."""
    uses: list[tuple[Removal, str, int, str]] = []
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
        bound: dict[str, str] = {}       # local name -> the module path it stands for
        for node in ast.walk(tree):
            if id(node) in guarded:
                continue
            if isinstance(node, ast.Import):
                for a in node.names:
                    bound[a.asname or a.name.split(".", 1)[0]] = a.name if a.asname else a.name.split(".", 1)[0]
        seen: set[int] = set()

        def add(r: Removal, node: ast.AST, text: str) -> None:
            if id(r) not in seen:
                seen.add(id(r))
                uses.append((r, path, getattr(node, "lineno", 0), text))

        for node in ast.walk(tree):
            if id(node) in guarded:
                continue
            for r in REMOVALS:
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    if r.names:
                        if node.module == r.module:
                            hit = next((a.name for a in node.names if a.name in r.names), None)
                            if hit:
                                add(r, node, f"from {node.module} import {hit}")
                    elif node.module == r.module or node.module.startswith(r.module + "."):
                        add(r, node, f"from {node.module} import ...")
                elif isinstance(node, ast.Import) and not r.names:
                    for a in node.names:
                        if a.name == r.module or a.name.startswith(r.module + "."):
                            add(r, node, f"import {a.name}")
                elif isinstance(node, ast.Attribute) and r.names:
                    full = _dotted(node)
                    if full and "." in full:
                        head, _, rest = full.partition(".")
                        resolved = f"{bound[head]}.{rest}" if head in bound else None
                        if resolved and resolved.rsplit(".", 1)[0] == r.module and node.attr in r.names:
                            add(r, node, resolved)
    return uses


def _has_cap_at_most(req: Requirement, ceiling: Version) -> bool:
    return any(s.operator in ("<", "<=") and Version(s.version) <= ceiling for s in req.specifier)


def _lowest_floor(req: Requirement) -> Version | None:
    lows = [Version(s.version) for s in req.specifier if s.operator in (">=", ">", "==", "~=")]
    return max(lows, default=None)


def cap_removed_names(deps: list[str], source_map: dict[str, str]) -> tuple[list[str], list[str], list[str]]:
    """(new dependencies, what was done, what needs a person). Existing requirements keep their order; caps are merged.

    For each distribution the strictest ceiling any use calls for wins. A requirement that already has a cap at least as
    strict is left alone. A requirement whose own floor is at or above the ceiling is reported, not changed.
    """
    uses = removed_name_uses(source_map)
    if not uses:
        return list(deps), [], []

    wanted: dict[str, tuple[Version, str]] = {}       # canonical name -> (ceiling, requirement text)
    reasons: dict[str, str] = {}
    for r, path, line, text in uses:
        for pin in r.pins:
            name, ceiling = _ceiling(pin)
            if name not in wanted or ceiling < wanted[name][0]:
                wanted[name] = (ceiling, pin)
                reasons[name] = f"{path}:{line} uses `{text}`; {r.what}"

    out = list(deps)
    applied: list[str] = []
    manual: list[str] = []
    index = {}
    for i, raw in enumerate(out):
        try:
            index.setdefault(canonicalize_name(Requirement(raw).name), i)
        except InvalidRequirement:
            continue

    for name in sorted(wanted):
        ceiling, pin = wanted[name]
        if name not in index:
            out.append(pin)
            applied.append(f"capped `{pin}`: {reasons[name]}.")
            continue
        req = Requirement(out[index[name]])
        if _has_cap_at_most(req, ceiling):
            continue
        floor = _lowest_floor(req)
        if floor is not None and floor >= ceiling:
            manual.append(f"`{out[index[name]]}` asks for a release that no longer has what the code uses "
                          f"({reasons[name]}); reconcile them by hand.")
            continue
        before = out[index[name]]
        req.specifier = req.specifier & SpecifierSet(f"<{ceiling}")
        out[index[name]] = str(req)
        applied.append(f"capped `{before}` to `{out[index[name]]}`: {reasons[name]}.")
    return out, applied, manual
