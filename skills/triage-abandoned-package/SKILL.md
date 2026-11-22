---
name: triage-abandoned-package
description: Use when someone asks whether an old, abandoned or unmaintained Python package can still be used or revived on a modern Python. Looks the package up with the crypt-keeper MCP tools, applies the same age and Python-support rules as Crypt Keeper's Coroner, and says what is a prediction and what needs a real run.
---

# Triage an abandoned Python package

Use the `crypt-keeper` MCP tools. They read PyPI and GitHub only; they never run package code.

## Steps
1. Call `get_pypi_metadata` with the package name. For a GitHub URL, call `get_repo_tree` instead.
2. Read these fields from the result:
   - `last_release_date` and `version`
   - `declared_pythons`: the Python versions the package says it supports
   - `requires_python`, which is often empty on old releases
   - `dependencies`, and `yanked_versions`
3. Apply the Coroner's rules (these are the thresholds in `backend/coroner/autopsy.py`):

   | Signal | Rule |
   |---|---|
   | Release age | under 1 year alive, 1 to 3 years dormant, 3 years or more dead. No date means dead. |
   | Python support | newest declared Python 3 or more minors behind the target is a gap. Declaring only Python 2 is a gap. |
   | Dependencies | an exact pin (`==`) or upper bound (`<`) can block a modern install. |

4. Predict revivability, and label it a prediction:
   - **Likely revivable:** its only blockers are moved or renamed standard-library names
     (`collections.Mapping`, `unittest` aliases, `imp`).
   - **Not revivable automatically:** Python 2 syntax (`print x`), or code that depends on internals
     removed from another library.
   - **Unknown:** anything you cannot see from metadata. Say so.
5. Give one concrete next step: run Crypt Keeper on it (`POST /revive`, or the headstone UI) and trust the
   verdict it prints, not the prediction.

## Rules
- Never say a package "works" or "is revived" from metadata alone. Only proof of life, run on the target
  Python, shows that.
- Report the exact dates and version strings you saw. Do not round them.
- If a tool returns `{"error": ..., "status": ...}`, report that message as written.
