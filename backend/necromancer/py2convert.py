"""Convert Python 2 source to Python 3 with `lib2to3`, in memory. Pure: nothing is written, nothing is run.

The other Necromancer transforms are drop-in replacements. This one is not: Python 2 and 3 differ in ways a mechanical
conversion cannot settle (text versus bytes, integer division, dictionary views). So it is held to a stricter rule:

  * It only runs on a package that really is Python 2: a file that runs when the package is imported (not a test, a
    build script, docs or an example) does not parse as Python 3, and `lib2to3` makes it parse. A Python 3 package is
    never touched, and neither is one whose only Python 2 files are tests or examples: the benchmark showed that
    converting such a package changed working modules and made some of its tests fail.
  * A converted file is kept only if the result compiles as Python 3 (not just parses). Otherwise the original is kept
    and reported.
  * Every conversion is listed, and the revival says plainly that it is mechanical. The verdict still comes from
    running the package, never from the conversion.

`lib2to3` ships with Python 3.12 and is removed in 3.13; without it nothing is converted and the revival says why.
The stock import fixer looks for sibling modules on disk, which this module cannot do, so it is replaced by one that
looks at the snapshot's own file list (this is what turns `import Workbook` into `from . import Workbook`).
"""
from __future__ import annotations

import ast
import logging
import posixpath
import re
import warnings

from proof.discovery import is_runtime_source

MAX_FILE_BYTES = 400_000      # lib2to3 is slow on very large files; those are left and reported
_TAB_INDENT = re.compile(r"^[ \t]+", re.MULTILINE)


def parses(text: str) -> bool:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ast.parse(text)
        return True
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return False


def compiles(text: str) -> bool:
    """Stricter than `parses`: compiling also runs the checks `ast.parse` leaves out, such as a duplicate argument name.

    A converted file must pass this, not only parse. lib2to3 turns `def f(str=str, unicode=unicode)` into
    `def f(str=str, str=str)`, which parses and then fails to compile; the benchmark found it in a working package.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            compile(text, "<converted>", "exec", dont_inherit=True)
        return True
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return False


def python2_paths(source_map: dict[str, str]) -> list[str]:
    """The .py files that do not parse as Python 3, in path order."""
    return [p for p in sorted(source_map) if p.endswith(".py") and not parses(source_map[p])]


def expand_indent_tabs(text: str) -> str:
    """Python 2 counted a tab as up to the next multiple of 8 columns; Python 3 refuses to mix tabs and spaces."""
    return _TAB_INDENT.sub(lambda m: m.group().expandtabs(8), text)


def _tool(paths: frozenset[str]):
    """A RefactoringTool whose import fixer reads the snapshot instead of the disk, or None without lib2to3."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            from lib2to3 import refactor
            from lib2to3.fixes import fix_import
        except ImportError:        # lib2to3 was removed in Python 3.13
            return None

        class SiblingFixImport(fix_import.FixImport):
            def probably_a_local_import(self, imp_name: str) -> bool:
                if imp_name.startswith("."):
                    return False
                root = imp_name.split(".", 1)[0]
                folder = posixpath.dirname(self.filename)
                if posixpath.join(folder, "__init__.py") not in paths:
                    return False
                return posixpath.join(folder, root + ".py") in paths or posixpath.join(folder, root, "__init__.py") in paths

        class Tool(refactor.RefactoringTool):
            def get_fixers(self):
                pre, post = super().get_fixers()
                swap = lambda f: SiblingFixImport(self.options, self.fixer_log) if type(f) is fix_import.FixImport else f
                return [swap(f) for f in pre], [swap(f) for f in post]

        return Tool(refactor.get_fixers_from_package("lib2to3.fixes"))


def convert_text(tool, text: str, path: str) -> str | None:
    """The converted source, or None when lib2to3 cannot read it or the result does not parse as Python 3."""
    for attempt in (text, expand_indent_tabs(text)):
        body = attempt if attempt.endswith("\n") else attempt + "\n"
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tree = tool.refactor_string(body, path)
        except Exception:                                  # lib2to3 raises many kinds of error on code it cannot read
            tree = None
        if tree is not None:
            out = str(tree)
            if not text.endswith("\n") and out.endswith("\n"):
                out = out[:-1]
            if compiles(out):
                return out
    return None


def convert_python2(source_map: dict[str, str]) -> tuple[dict[str, str], list[str], list[str]]:
    """(converted files only: path -> new text, what was done, what needs a person).

    Returns nothing for a package that is already Python 3, nothing when the only files that do not parse are not
    runtime code (tests, setup.py, docs, examples), and nothing when no runtime file can be fixed (a package with one
    corrupt file is not a Python 2 package).
    """
    broken = python2_paths(source_map)
    if not any(is_runtime_source(p) for p in broken):
        return {}, [], []
    logger = logging.getLogger("RefactoringTool")
    previous = logger.level
    logger.setLevel(logging.CRITICAL)                      # lib2to3 logs every file it cannot read
    try:
        tool = _tool(frozenset(source_map))
        if tool is None:
            return {}, [], ["This is Python 2 code, and converting it needs `lib2to3`, which this Python does not have "
                            "(it was removed in 3.13). Convert it by hand or run on Python 3.12."]
        converted: dict[str, str] = {}
        kept_broken: list[str] = []
        too_big: list[str] = []
        for path in sorted(source_map):
            if not path.endswith(".py"):
                continue
            text = source_map[path]
            if len(text) > MAX_FILE_BYTES:
                if path in broken:
                    too_big.append(path)
                continue
            out = convert_text(tool, text, path)
            if out is None:
                if path in broken:
                    kept_broken.append(path)
            elif out != text:
                converted[path] = out
    finally:
        logger.setLevel(previous)

    fixed = [p for p in broken if p in converted]
    if not any(is_runtime_source(p) for p in fixed):       # no runtime file could be fixed: not a Python 2 package
        return {}, [], []

    applied = [f"converted {len(converted)} file{'s' * (len(converted) != 1)} from Python 2 with lib2to3; "
               f"{len(fixed)} of the {len(broken)} that did not parse as Python 3 now do."]
    manual = ["The Python 2 conversion is mechanical (print, except, imports, dictionary methods, ...). Text versus "
              "bytes and integer division can still behave differently: run the package's tests."]
    if kept_broken:
        shown = ", ".join(kept_broken[:5]) + (f" and {len(kept_broken) - 5} more" if len(kept_broken) > 5 else "")
        manual.append(f"Still Python 2 after conversion (lib2to3 could not fix them): {shown}.")
    if too_big:
        manual.append(f"Too large to convert here: {', '.join(too_big[:5])}.")
    return converted, applied, manual
