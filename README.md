# Crypt Keeper

> _Every dead package deserves a second life._

Crypt Keeper digs up an abandoned Python package, finds out why it no longer works on modern
Python, rewrites what it safely can, and then **runs the result** to show whether it came back.

Built for **Kiroween 2026**, category **Frankenstein**.

## What it does

1. **Exhume.** Fetches a package from PyPI (or a GitHub repository): its source, release dates,
   and dependencies. Older releases have no dependency list on PyPI, so it reads the sdist's own
   packaging files.
2. **Autopsy.** The Coroner (pure Python, no network) reports the cause of death: abandonment,
   Python-support gap, pinned or yanked dependencies, APIs removed from the standard library
   (found by parsing the code, with the removal version of each), and Python 2 syntax.
3. **Resurrect.** The Necromancer rewrites only drop-in replacements, as precise text edits that
   keep comments and formatting: `collections.Mapping` to `collections.abc.Mapping`, removed
   `unittest` aliases, same-module renames, exact pins to lower bounds, and raised minimums for
   21 compiled packages. Everything it cannot fix safely is listed as a manual item.
4. **Proof of life.** Imports the original and the revived package in throwaway virtualenvs and
   runs the package's own tests (or a generated smoke check). The verdict compares the two.

A real run, `unittest2` on Python 3.11 (about 40 seconds):

| | Original | Revived |
|---|---|---|
| Imports | no: `module 'collections' has no attribute 'MutableMapping'` | yes |
| Shipped tests | 0 pass, 14 errors | 6 pass, 1 fail, 21 errors |

Not every package comes back. Flask-Script and sqlalchemy-migrate depend on internals that were
removed from Flask and SQLAlchemy, and the verdict says `still dead`. Python 2 code is reported,
not converted.

## Run it

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn api.main:app --port 8000
```

Open <http://127.0.0.1:8000>. The API serves the UI, so there is nothing else to start.
Interactive API docs are at `/docs`. Run the tests with `python -m pytest` from `backend/`.

**Install the target Python.** Proof of life runs on the target version (default 3.12) only if
that interpreter is installed. If it is not, the run falls back to the current Python and the
verdict reads `unverified`, because a package that imports on 3.11 can still fail on 3.12.

## Safety

Proof of life **executes the package's own code** as your user, with network access. It uses a
fresh virtualenv, wheel-only installs, a scrubbed environment, timeouts, and process-tree
kills, but it is **not a security boundary**. Only revive packages you would be willing to
import. Set `CRYPT_KEEPER_PROOF_OF_LIFE=off` to disable it. A public deployment needs a
container or VM around it.

The API rejects cross-origin and non-JSON POSTs and unknown Host headers, so another website
cannot make your browser send code to a local Crypt Keeper. Configure with
`CRYPT_KEEPER_ALLOWED_ORIGINS` and `CRYPT_KEEPER_ALLOWED_HOSTS`.

## Layout

```
backend/
  coroner/       autopsy: liveness, dependencies, removed APIs (pure)
  necromancer/   revival: dependency bumping, source rewriting, diffs (pure)
  proof/         what to import, test and smoke-call; the verdict (pure)
  api/           FastAPI app, exhumer (network), sandbox (subprocess)
frontend/        the UI: plain HTML, CSS and JavaScript, no dependencies
mcp-server/      MCP tools: get_pypi_metadata, get_repo_tree
docs/            how Kiro was used
.kiro/           specs, steering and hooks that drove the build
```

## Limits

- Only `.py` files are exhumed. Packages that need data files or C extensions at import time can
  fail for reasons unrelated to their age.
- A source tree over 1,500 files is truncated and flagged as such in the report.
- GitHub exhumation reads at most 200 files and `requirements.txt` only.
- Large packages are slow: `boto` takes about 3 minutes because its test suite is large.

## How Kiro was used

See [`docs/KIRO_USAGE.md`](docs/KIRO_USAGE.md) and [`.kiro/`](.kiro/).

## License

MIT. See [LICENSE](LICENSE).
