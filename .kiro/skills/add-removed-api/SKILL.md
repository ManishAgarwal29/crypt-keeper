---
name: add-removed-api
description: Use when adding a removed or deprecated Python API to Crypt Keeper, either so the Coroner flags it (backend/coroner/deprecations.py) or so the Necromancer rewrites it (backend/necromancer/rewriter.py). Covers the three detection tables, the version rule, when a rewrite is allowed, the tests that must change, and how to validate on real packages.
---

# Adding a removed API to Crypt Keeper

Two modules must agree on what counts as a removed API, and they live in separate files on purpose:
the **Coroner** detects (`backend/coroner/deprecations.py`), the **Necromancer** rewrites
(`backend/necromancer/rewriter.py`). Both are pure: no network, no I/O.

## 1. Get the removal version from a source, not from memory
Open the CPython "What's New" page for the release that removed it
(`https://docs.python.org/3/whatsnew/3.12.html`, "Removed" section). A wrong version flags code that
still works, or misses code that is already broken. Put the page in a comment next to the entry.

## 2. Add it to the Coroner (detection)
Entries are `Removal(removed_in, advice)`: `removed_in` is a `(major, minor)` tuple and `advice` is
a short "use X" sentence. Pick the table by what is being removed:

| Table | Key | Use for |
|---|---|---|
| `REMOVED_MODULES` | dotted module name | `import imp`, `distutils`, `asyncore`. Submodules match by prefix. |
| `REMOVED_ATTRS` | dotted attribute path | `collections.Mapping`, `inspect.getargspec`. Resolved through import aliases. |
| `REMOVED_TESTCASE_METHODS` | method name | `assertEquals`. Flagged only on `self.` or `cls.` in a file that imports `unittest`. |

For names that only existed on Python 2, use the helper `_py2("use X")`, which sets `removed_in` to
`(3, 0)`. A finding appears only when the target Python is at or after `removed_in`. Code inside
`try: ... except ImportError:` is deliberate compatibility code and is skipped. Never add a substring
check: matching text finds comments and strings.

## 3. Rewrite it only if it is a true drop-in
A rewrite must give the same behaviour with no other change to the file. If the replacement takes
different arguments, returns a different type, or needs more than one edit, **do not rewrite**. The
Necromancer re-scans its own output, so anything unhandled is reported as a manual item with the file
and line. That is the correct outcome, not a gap to fill.

| Table in `rewriter.py` | Shape | Example |
|---|---|---|
| `RENAMES` | `"module.old": "new_name"` in the same module | `"base64.encodestring": "encodebytes"` |
| `BUILTIN_REPLACEMENTS` | `"module.old": "builtin"` | `"itertools.izip": "zip"` |
| `TESTCASE_RENAMES` | `"oldMethod": "newMethod"` | `"assertEquals": "assertEqual"` |
| `ABC_MOVES` | derived: every `collections.*` key in `REMOVED_ATTRS` | adds `import collections.abc` when safe |

An API appears in at most one rewrite table, and every rewrite key must also be in the Coroner's
tables. Tests enforce both.

## 4. Tests to add or update
1. `backend/tests/test_deprecations.py`: flagged at and after `removed_in`, not before (follow the
   existing `test_removed_module_*` and `test_findings_only_appear_when_target_reaches_removal`).
2. `backend/tests/test_rewrite.py`: if you added a rewrite, add an exact before and after case.
   `test_every_table_entry_rewrites_cleanly` already runs each `RENAMES` and `BUILTIN_REPLACEMENTS` key
   and checks the output parses and rewriting it again changes nothing.
3. Run from `backend/`: `python -m pytest -q tests/test_deprecations.py tests/test_rewrite.py`, then
   the whole suite.

## 5. Validate on real code before calling it done
Unit tests only prove your own assumptions. Run the change over real packages that use the API:

```python
import ast, httpx
from api.exhumer import exhume_pypi
from necromancer.rewriter import rewrite_deprecated

with httpx.Client(timeout=60, follow_redirects=True) as client:
    snapshot = exhume_pypi("PACKAGE", "3.12", client)
for path, source in snapshot.source_map.items():
    new, applied, manual = rewrite_deprecated(source, "3.12", path)
    if new != source:
        ast.parse(new)                                    # still valid Python
        assert rewrite_deprecated(new, "3.12", path)[0] == new   # idempotent
        print(path, applied)
```

Read the diffs. Confirm each one is what a person would write by hand.

## Pitfalls seen in this project
- A file that does not parse (Python 2 syntax) cannot be scanned; it gets a `syntax-error` finding
  instead of API findings. That is expected.
- Do not edit `scan_source` to special-case one API. Put the knowledge in a table.
- Keep the Coroner and Necromancer independent: the rewriter imports the Coroner's detection helpers,
  never the other way round.
