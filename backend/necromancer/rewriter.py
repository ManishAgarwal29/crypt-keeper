"""Rewrites removed-API usage in Python source, one precise text edit at a time.

Pure: no network, no I/O. Uses the Coroner's detection helpers so both agree on what counts
as a use of a removed API (aliases resolved, `try/except ImportError` guards skipped,
target-version aware).

Only drop-in replacements are rewritten:
  - `collections.Mapping` and the other ABCs move to `collections.abc`
  - same-module renames (`base64.encodestring` -> `base64.encodebytes`, ...)
  - `itertools.izip/imap/ifilter` -> the built-in `zip/map/filter`
  - removed `unittest.TestCase` aliases (`assertEquals` -> `assertEqual`, ...)

Everything else is left alone. After rewriting, the result is scanned again and whatever is
still flagged is returned as the manual list, so nothing is skipped silently.

Edits are spliced into the original text at AST positions, so comments, formatting and line
endings are untouched.
"""
from __future__ import annotations

import ast
import re
import warnings
from dataclasses import dataclass

from coroner.deprecations import (
    REMOVED_ATTRS,
    REMOVED_TESTCASE_METHODS,
    guarded_node_ids,
    import_aliases,
    parse_target,
    resolve_attribute,
    scan_source,
)

# Every `collections.X` entry in the Coroner's table is an ABC that moved to collections.abc.
ABC_MOVES = frozenset(k for k in REMOVED_ATTRS if k.startswith("collections."))

# dotted old name -> new name inside the same module
RENAMES: dict[str, str] = {
    "base64.encodestring": "encodebytes",
    "base64.decodestring": "decodebytes",
    "configparser.SafeConfigParser": "ConfigParser",
    "os.getcwdu": "getcwd",
    "string.letters": "ascii_letters",
    "string.lowercase": "ascii_lowercase",
    "string.uppercase": "ascii_uppercase",
    "sys.maxint": "maxsize",
    "itertools.izip_longest": "zip_longest",
}

# dotted old name -> built-in that replaces it
BUILTIN_REPLACEMENTS: dict[str, str] = {
    "itertools.izip": "zip",
    "itertools.imap": "map",
    "itertools.ifilter": "filter",
}

TESTCASE_RENAMES: dict[str, str] = {
    "failUnless": "assertTrue",
    "failIf": "assertFalse",
    "failUnlessEqual": "assertEqual",
    "failIfEqual": "assertNotEqual",
    "failUnlessAlmostEqual": "assertAlmostEqual",
    "failIfAlmostEqual": "assertNotAlmostEqual",
    "failUnlessRaises": "assertRaises",
    "assert_": "assertTrue",
    "assertEquals": "assertEqual",
    "assertNotEquals": "assertNotEqual",
    "assertAlmostEquals": "assertAlmostEqual",
    "assertNotAlmostEquals": "assertNotAlmostEqual",
    "assertRegexpMatches": "assertRegex",
    "assertNotRegexpMatches": "assertNotRegex",
    "assertRaisesRegexp": "assertRaisesRegex",
}

_NEWLINE = re.compile(r"\r\n|\r|\n")


@dataclass
class _Edit:
    start: int
    end: int
    text: str
    line: int
    note: str


class _Text:
    """Maps AST (line, UTF-8 byte column) positions to character offsets in the source."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.starts = [0] + [m.end() for m in _NEWLINE.finditer(text)]

    def _line_bounds(self, lineno: int) -> tuple[int, int]:
        start = self.starts[lineno - 1]
        end = self.starts[lineno] if lineno < len(self.starts) else len(self.text)
        return start, end

    def offset(self, lineno: int, col_bytes: int) -> int:
        start, end = self._line_bounds(lineno)
        return start + len(self.text[start:end].encode("utf-8")[:col_bytes].decode("utf-8"))

    def line_start(self, lineno: int) -> int:
        return self._line_bounds(lineno)[0]

    def line_end(self, lineno: int) -> int:
        """Offset just before the line terminator."""
        start, end = self._line_bounds(lineno)
        return start + len(self.text[start:end].rstrip("\r\n"))

    def eol(self, lineno: int) -> str:
        start, end = self._line_bounds(lineno)
        return self.text[start:end][len(self.text[start:end].rstrip("\r\n")):] or "\n"


def rewrite_deprecated(
    source: str, target_python: str = "3.12", path: str = "<source>"
) -> tuple[str, list[str], list[str]]:
    """Rewrite drop-in replacements for removed APIs.

    Returns (new_source, applied, manual). `applied` lists each change as "path:line: ...".
    `manual` lists what is still wrong in the new source, found by scanning it again. A file
    that does not parse is returned unchanged with its syntax error in `manual`.
    Idempotent: rewriting the output again changes nothing.
    """
    new_source = source
    applied: list[str] = []
    tree = _parse(source)
    if tree is not None:
        new_source, applied = _Rewriter(source, tree, parse_target(target_python), path).run()
    manual = [f"{f.location}: {f.message}" for f in scan_source(path, new_source, target_python)]
    return new_source, applied, manual


def _parse(source: str) -> ast.AST | None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None


class _Rewriter:
    def __init__(self, source: str, tree: ast.AST, target: tuple[int, int], path: str) -> None:
        self.src = _Text(source)
        self.tree = tree
        self.target = target
        self.path = path
        self.edits: list[_Edit] = []
        self.abc_edits: list[_Edit] = []  # only applied if `import collections.abc` is available
        self.aliases = import_aliases(tree)
        self.uses_unittest = any(v == "unittest" or v.startswith("unittest.") for v in self.aliases.values())

    # -- driver -----------------------------------------------------------------------------

    def run(self) -> tuple[str, list[str]]:
        guarded = guarded_node_ids(self.tree)
        for node in ast.walk(self.tree):
            if id(node) in guarded:
                continue
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                self._from_import(node)
            elif isinstance(node, ast.Attribute):
                self._attribute(node)

        self._getargspec(guarded)

        edits = list(self.edits)
        if self.abc_edits:
            ok, insertion = self._plan_abc_import()
            if ok:
                edits += self.abc_edits
                if insertion:
                    edits.append(insertion)

        kept = self._drop_overlaps(edits)
        text = self.src.text
        for e in sorted(kept, key=lambda e: (e.start, e.end), reverse=True):
            text = text[: e.start] + e.text + text[e.end:]
        applied = [f"{self.path}:{e.line}: {e.note}" for e in sorted(kept, key=lambda e: (e.line, e.start))]
        return text, applied

    @staticmethod
    def _drop_overlaps(edits: list[_Edit]) -> list[_Edit]:
        """Keep non-overlapping edits. A dropped edit leaves its finding in the manual list."""
        kept: list[_Edit] = []
        last_end = -1
        for e in sorted(edits, key=lambda e: (e.start, e.end)):
            if e.start < last_end:
                continue
            kept.append(e)
            last_end = e.end
        return kept

    def _active(self, table_key: str, table: dict) -> bool:
        return self.target >= table[table_key].removed_in

    # -- imports ------------------------------------------------------------------------------

    def _from_import(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        moved = [
            a for a in node.names
            if f"{module}.{a.name}" in ABC_MOVES and self._active(f"{module}.{a.name}", REMOVED_ATTRS)
        ]
        if moved:
            self._split_abc_import(node, moved)
            return
        for alias in node.names:
            dotted = f"{module}.{alias.name}"
            new = RENAMES.get(dotted)
            if new and self._active(dotted, REMOVED_ATTRS):
                a = self.src.offset(alias.lineno, alias.col_offset)
                b = self.src.offset(alias.end_lineno, alias.end_col_offset)
                self.edits.append(_Edit(
                    a, b, f"{new} as {alias.asname or alias.name}", alias.lineno,
                    f"`{dotted}` -> `{module}.{new}`",
                ))

    def _split_abc_import(self, node: ast.ImportFrom, moved: list[ast.alias]) -> None:
        if not self._standalone(node):
            return

        def fmt(a: ast.alias) -> str:
            return a.name + (f" as {a.asname}" if a.asname else "")

        rest = [fmt(a) for a in node.names if a not in moved]
        start = self.src.offset(node.lineno, node.col_offset)
        end = self.src.offset(node.end_lineno, node.end_col_offset)
        indent = self.src.text[self.src.line_start(node.lineno):start]
        eol = self.src.eol(node.end_lineno)

        text = ""
        if rest:
            text += f"from collections import {', '.join(rest)}{eol}{indent}"
        text += f"from collections.abc import {', '.join(fmt(a) for a in moved)}"
        names = ", ".join(a.name for a in moved)
        self.edits.append(_Edit(start, end, text, node.lineno, f"moved `{names}` from `collections` to `collections.abc`"))

    def _standalone(self, node: ast.stmt) -> bool:
        """True when the statement is alone on its line(s), with at most a trailing comment."""
        s = self.src
        a = s.offset(node.lineno, node.col_offset)
        b = s.offset(node.end_lineno, node.end_col_offset)
        before = s.text[s.line_start(node.lineno):a]
        after = s.text[b:s.line_end(node.end_lineno)].strip()
        return before.strip() == "" and (after == "" or after.startswith("#"))

    def _has_abc_import(self) -> bool:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import) and any(
                a.name == "collections.abc" or a.name.startswith("collections.abc.") for a in node.names
            ):
                return True
            if isinstance(node, ast.ImportFrom) and node.level == 0 and (
                node.module == "collections.abc"
                or (node.module == "collections" and any(a.name == "abc" for a in node.names))
            ):
                return True
        return False

    def _plan_abc_import(self) -> tuple[bool, _Edit | None]:
        """Make sure `collections.abc` is imported. Returns (ok, insertion edit if one is needed)."""
        if self._has_abc_import():
            return True, None
        for stmt in self.tree.body:
            if (
                isinstance(stmt, ast.Import)
                and any(a.name == "collections" for a in stmt.names)
                and self._standalone(stmt)
            ):
                at = self.src.line_end(stmt.end_lineno)
                text = f"{self.src.eol(stmt.end_lineno)}import collections.abc"
                return True, _Edit(at, at, text, stmt.end_lineno, "added `import collections.abc`")
        return False, None

    # -- attribute uses -----------------------------------------------------------------------------

    def _attribute(self, node: ast.Attribute) -> None:
        dotted = resolve_attribute(node, self.aliases)
        if dotted in REMOVED_ATTRS:
            if not self._active(dotted, REMOVED_ATTRS):
                return
            if dotted in ABC_MOVES:
                name = dotted.split(".", 1)[1]
                self._rename_attr(node, f"abc.{name}", f"`{dotted}` -> `collections.abc.{name}`", self.abc_edits)
            elif dotted in RENAMES:
                new = RENAMES[dotted]
                self._rename_attr(node, new, f"`{dotted}` -> `{dotted.rsplit('.', 1)[0]}.{new}`", self.edits)
            elif dotted in BUILTIN_REPLACEMENTS:
                new = BUILTIN_REPLACEMENTS[dotted]
                a = self.src.offset(node.lineno, node.col_offset)
                b = self.src.offset(node.end_lineno, node.end_col_offset)
                self.edits.append(_Edit(a, b, new, node.lineno, f"`{dotted}` -> built-in `{new}`"))
        elif (
            self.uses_unittest
            and node.attr in TESTCASE_RENAMES
            and isinstance(node.value, ast.Name)
            and node.value.id in ("self", "cls")
            and self._active(node.attr, REMOVED_TESTCASE_METHODS)
        ):
            new = TESTCASE_RENAMES[node.attr]
            self._rename_attr(node, new, f"`TestCase.{node.attr}` -> `TestCase.{new}`", self.edits)

    # -- inspect.getargspec -----------------------------------------------------------------------------------
    #
    # `getargspec` returns 4 fields (args, varargs, keywords, defaults); `getfullargspec` returns 7 (and calls the
    # third `varkw`). A plain rename is wrong wherever the result is unpacked or measured, so only the uses that
    # are provably the same are rewritten, and every other use stays in the manual list:
    #   a, b, c, d = getargspec(f)      ->  a, b, c, d = getfullargspec(f)[:4]
    #   getargspec(f).args / .varargs / .defaults, and [0] to [3]  ->  renamed
    #   getargspec(f).keywords          ->  getfullargspec(f).varkw
    #   spec = getargspec(f)            ->  renamed, when `spec` is a local used only as spec.args/varargs/defaults/keywords

    _ARGSPEC = "inspect.getargspec"
    _ARGSPEC_FIELDS = {"args": None, "varargs": None, "defaults": None, "keywords": "varkw"}

    def _getargspec(self, guarded: set[int]) -> None:
        if not self._active(self._ARGSPEC, REMOVED_ATTRS):
            return
        parents: dict[int, ast.AST] = {}
        for parent in ast.walk(self.tree):
            for child in ast.iter_child_nodes(parent):
                parents[id(child)] = parent

        attr_sites: list[ast.Attribute] = []
        name_sites: list[ast.Name] = []
        imports: list[tuple[ast.ImportFrom, ast.alias, bool]] = []  # (statement, alias, inside a guard)
        local_names: set[str] = set()
        for node in ast.walk(self.tree):
            in_guard = id(node) in guarded
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "inspect":
                for alias in node.names:
                    if alias.name == "getargspec":
                        imports.append((node, alias, in_guard))
                        local_names.add(alias.asname or alias.name)
            elif in_guard:
                continue
            elif isinstance(node, ast.Attribute) and resolve_attribute(node, self.aliases) == self._ARGSPEC:
                attr_sites.append(node)
            elif isinstance(node, ast.Name) and self.aliases.get(node.id) == self._ARGSPEC:
                name_sites.append(node)

        for site in attr_sites:
            plan = self._argspec_plan(site, parents)
            if plan is not None:
                self._rename_attr(site, "getfullargspec", "`inspect.getargspec` -> `inspect.getfullargspec`", self.edits)
                self.edits += plan

        # A name imported with `from inspect import getargspec` is renamed in the import and at every use, or not at all.
        if not imports or any(guarded_import for _, _, guarded_import in imports):
            return
        every_use = [n for n in ast.walk(self.tree) if isinstance(n, ast.Name) and n.id in local_names]
        plans = [self._argspec_plan(site, parents) for site in name_sites]
        # Rewriting the import breaks any use that is not rewritten with it: a store, a use inside a guard, or an unknown shape.
        if len(every_use) != len(name_sites) or any(p is None for p in plans):
            return
        for node, alias, _ in imports:
            text = "getfullargspec" + (f" as {alias.asname}" if alias.asname else "")
            a = self.src.offset(alias.lineno, alias.col_offset)
            b = self.src.offset(alias.end_lineno, alias.end_col_offset)
            self.edits.append(_Edit(a, b, text, alias.lineno, "`inspect.getargspec` -> `inspect.getfullargspec`"))
        for site, plan in zip(name_sites, plans):
            if not any(a.asname for _, a, _ in imports):  # with `as x` the call sites keep their own name
                a = self.src.offset(site.lineno, site.col_offset)
                b = self.src.offset(site.end_lineno, site.end_col_offset)
                self.edits.append(_Edit(a, b, "getfullargspec", site.lineno, "`inspect.getargspec` -> `inspect.getfullargspec`"))
            self.edits += plan or []

    def _argspec_plan(self, func: ast.expr, parents: dict[int, ast.AST]) -> list[_Edit] | None:
        """The extra edits that keep this one use of `getargspec` meaning the same, or None if it cannot be proven."""
        call = parents.get(id(func))
        if not (isinstance(call, ast.Call) and call.func is func):
            return None
        outer = parents.get(id(call))
        end = self.src.offset(call.end_lineno, call.end_col_offset)

        if isinstance(outer, ast.Attribute) and outer.value is call and outer.attr in self._ARGSPEC_FIELDS:
            new = self._ARGSPEC_FIELDS[outer.attr]
            return [] if new is None else self._attr_rename_edits(outer, new)
        if (
            isinstance(outer, ast.Subscript) and outer.value is call
            and isinstance(outer.slice, ast.Constant) and type(outer.slice.value) is int and 0 <= outer.slice.value <= 3
        ):
            return []
        if isinstance(outer, ast.Assign) and outer.value is call and len(outer.targets) == 1:
            target = outer.targets[0]
            if isinstance(target, (ast.Tuple, ast.List)):
                # Any unpacking sees the same four values after `[:4]`, so even a starred or wrongly sized target behaves as before.
                return [_Edit(end, end, "[:4]", call.lineno, "kept the 4-field shape of `getargspec` with `[:4]`")]
            if isinstance(target, ast.Name):
                return self._argspec_local(target, outer, parents)
        return None

    def _attr_rename_edits(self, node: ast.Attribute, new: str) -> list[_Edit]:
        edits: list[_Edit] = []
        self._rename_attr(node, new, f"`.{node.attr}` -> `.{new}` (the field is named `{new}` in `getfullargspec`)", edits)
        return edits

    def _argspec_local(self, target: ast.Name, assign: ast.Assign, parents: dict[int, ast.AST]) -> list[_Edit] | None:
        """For `spec = getargspec(f)`: the `.keywords` renames, if `spec` is a local only ever read as an argspec field."""
        scope: ast.AST | None = parents.get(id(assign))
        while scope is not None and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(scope, (ast.Module, ast.ClassDef, ast.Lambda)):
                return None  # module and class variables can be read from other modules
            scope = parents.get(id(scope))
        if scope is None:
            return None
        name = target.id
        edits: list[_Edit] = []
        stores = 0
        for node in ast.walk(scope):
            if isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
                return None
            if isinstance(node, ast.arg) and node.arg == name:
                return None
            if not (isinstance(node, ast.Name) and node.id == name):
                continue
            if isinstance(node.ctx, ast.Store):
                stores += 1  # the assignment being rewritten is one; any other store means the name is reused
            elif isinstance(node.ctx, ast.Del):
                return None
            else:
                use = parents.get(id(node))
                if not (isinstance(use, ast.Attribute) and use.value is node and isinstance(use.ctx, ast.Load)
                        and use.attr in self._ARGSPEC_FIELDS):
                    return None
                new = self._ARGSPEC_FIELDS[use.attr]
                if new is not None:
                    edits += self._attr_rename_edits(use, new)
        return edits if stores == 1 else None

    def _rename_attr(self, node: ast.Attribute, new: str, note: str, into: list[_Edit]) -> None:
        """Replace just the attribute name token at the end of `node`."""
        end = self.src.offset(node.end_lineno, node.end_col_offset)
        start = end - len(node.attr)
        if self.src.text[start:end] != node.attr:
            return
        into.append(_Edit(start, end, new, node.lineno, note))
