# Design: Crypt Keeper Core

_Updated after implementation so it describes the system as built. Changes from the first draft
are listed at the end._

## Overview
Crypt Keeper is a FastAPI backend that also serves a static web UI. A revival has four stages:
**Exhume, Autopsy, Resurrect, Proof of life.** Analysis (Coroner), transformation (Necromancer)
and the decision logic for proof of life are pure modules. Only `api/` touches the network or runs
a subprocess.

## Architecture

```
 Browser ── same origin ──> FastAPI (api/main.py) ── serves frontend/
                               │ orchestrates (generator emitting progress events)
      ┌────────────────────────┼─────────────────────────────┐
      ▼                        ▼                             ▼
 api/exhumer.py          coroner/ + necromancer/        api/sandbox.py
 PyPI / GitHub           pure analysis and rewrites     throwaway venv, subprocess
 (httpx, injected)       (no network, no I/O)           uses proof/discovery.py (pure)
      │                                                       │
 mcp-server/server.py ── reuses exhumer ──                proof/verdict.py (pure)
```

Pipeline, as one generator `_pipeline()` that yields stage events and then a result:
1. **Exhume.** Metadata and source from PyPI (sdist) or GitHub. If PyPI lists no dependencies,
   read the sdist's `requires.txt` / `PKG-INFO` / `pyproject.toml` / `setup.cfg`. Cap at 1,500 .py
   files and mark the snapshot `truncated` if anything was left out.
2. **Autopsy.** Liveness (release age and declared Python support) plus findings: abandonment,
   Python-support gap, dependency problems, removed APIs, Python 2 syntax, truncated source.
3. **Resurrect.** Bump dependencies, rewrite drop-in API replacements as text edits, return unified
   diffs, the revived dependency list, an applied log and manual items.
4. **Proof of life.** Run the original and the revived package in parallel, each in its own virtualenv
   on the target Python. Compare with `judge()` to get a verdict.

## Components

### Models (`models.py`)
`PackageSnapshot` (source map, dependencies, `dependency_info`, `truncated`), `Finding`,
`AutopsyReport`, `FileDiff` (before, after, unified, line counts), `RevivalResult` (diffs, revived
`dependencies`, applied, manual), `ProofOfLife` (imported, exact error, tests passed/failed/errors,
test source, interpreter used, `target_python_matched`, log), `ReviveResponse` (package summary,
autopsy, revival, both proofs, verdict, reason, timings).

### Coroner (`coroner/`) — pure
- `autopsy.py`: `classify_liveness`, `analyze_dependencies` (pins, upper bounds, yanked,
  incompatible, unmaintained, unparseable), `run_autopsy` (orders findings by severity).
- `deprecations.py`: a table of removed APIs with the Python version that removed each, and an
  AST scanner that resolves import aliases and skips `try/except ImportError` compatibility code.

### Necromancer (`necromancer/`) — pure
- `revive.py`: `bump_dependencies` (exact pin to lower bound, drop upper bounds, raise minimums for
  compiled packages from `floors.py`), `revive`, `revived_snapshot`.
- `rewriter.py`: splices precise text edits at AST positions, so comments, formatting and line
  endings survive. Rewrites drop-in replacements only; anything else is re-scanned and returned as
  a manual item.
- `floors.py`: first release with real wheel coverage per Python version, derived from PyPI.
- `removed_names.py`: a table of names a dependency removed (Jinja2 3.1, Werkzeug 2.2, paramiko 4,
  Flask 1.0 and 3.0, ...) with the requirement that brings each back. Every entry was installed
  from wheels on Python 3.12 and checked, with a control above the ceiling. Caps are merged, the
  strictest wins, and `bump_dependencies` keeps a cap only while the code still needs it.
- `declare_imports.py`: declares well-known libraries the package imports but does not declare,
  from a fixed import-name to distribution table. Never guesses a PyPI name.
- `py2convert.py`: converts Python 2 source with `lib2to3` in memory. It runs only when a runtime
  file does not parse as Python 3, keeps only output that compiles, and resolves implicit relative
  imports from the snapshot's own file list instead of the disk, so the module stays pure. It is
  the one transform that is not a drop-in replacement, so `revive(..., convert_python2=)` and
  `CRYPT_KEEPER_CONVERT_PYTHON2` switch it, and the Undertaker leaves it off.

### Proof of life
- `proof/discovery.py` (pure): find importable packages and sys.path roots, shipped tests, safe
  zero-argument functions, safe paths, plain-requirement validation, pytest summary parsing.
- `proof/verdict.py` (pure): `judge(before, after)`.
- `api/sandbox.py` (subprocess): temp dir, venv, wheel-only install, scrubbed environment, timeouts
  with process-tree kill, interpreter lookup that reports when the target Python is not installed.

### API (`api/main.py`)
`POST /exhume`, `/autopsy`, `/resurrect`, `/proof-of-life`, `/revive` (one response) and
`/revive/stream` (newline-delimited JSON progress events, then the result), `GET /health` and
`/proof-of-life/status`. Guards: Origin, content-type, Host allow-list, body size, and a slot limit
on sandbox runs.

### MCP server (`mcp-server/server.py`)
`get_pypi_metadata(name)` and `get_repo_tree(url)`, reusing `api/exhumer.py`. Supports mcp 1.x and 2.x.

## Error handling
- Not found, invalid input and upstream failures raise `ExhumeError` with a themed but accurate
  message and an HTTP status. Unexpected errors return a generic 500 and are logged.
- Pure modules never raise on bad data they can classify; they emit findings or manual items.
- On the stream endpoint the HTTP status is sent before work starts, so failures arrive as an
  `error` event.

## Testing strategy
- **Unit and property-based (Hypothesis)** on every pure module: idempotent rewrites and bumps,
  output always parses, paths never escape, verdict "resurrected" only when it should be.
- **Mocked HTTP** (`httpx.MockTransport`) for the exhumer and API; the sandbox is faked in API tests.
- **Real subprocess tests** for the sandbox (real virtualenvs, no network needed).
- **Real-world validation** after each stage on real PyPI packages, and the UI in a real browser.
  This found the bugs the other layers could not (see `docs/KIRO_USAGE.md`).

## Security and safety
Proof of life executes package code as the current user. It is **not** a security boundary. See
`.kiro/steering/sandbox-safety.md` for the invariants and `api/sandbox.py` for the exact limits.

## Changes from the first draft
- Added `/revive/stream`, the verdict, `truncated`, sdist dependency discovery, the API guards and
  the safety steering, none of which the first design had.
- Replaced "an isolated subprocess/venv" with the concrete sandbox and an honest description of
  what it does not isolate.
- The rewriter edits text at AST positions instead of substring replacement.
- The UI is served by the API (same origin) instead of being a separate static site.
