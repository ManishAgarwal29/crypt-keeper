# Design: Crypt Keeper Core

## Overview
Crypt Keeper is a FastAPI backend + static web frontend, with a standalone MCP server for
PyPI/GitHub access. The revival pipeline has four stages: **Exhume → Autopsy → Resurrect →
Proof of Life**. Analysis (Coroner) and transformation (Necromancer) are pure, testable
modules; the API layer orchestrates them and performs all I/O.

## Architecture

```
          ┌─────────────┐        ┌──────────────┐
  Browser │  frontend/  │ <----> │   backend/   │
          │  graveyard  │  HTTP  │   FastAPI    │
          └─────────────┘        └──────┬───────┘
                                        │ orchestrates
                   ┌────────────────────┼────────────────────┐
                   ▼                    ▼                     ▼
             coroner/autopsy     necromancer/revive     exhumer (I/O)
             (pure analysis)     (pure transforms)      (PyPI/GitHub)
                                                              │
                                                        ┌─────▼──────┐
                                                        │ mcp-server │
                                                        └────────────┘
```

Pipeline:
1. **Exhume** — `exhumer` fetches metadata + source (via MCP tools or direct HTTP).
2. **Autopsy** — `coroner.autopsy` consumes the fetched data (pure) → `AutopsyReport`.
3. **Resurrect** — `necromancer.revive` applies transforms (pure) → `RevivalResult` + diff.
4. **Proof of Life** — runner imports the revived package in a sandbox and runs tests.

## Components and Interfaces

### Models (`backend/models.py`)
- `PackageRef` — `{ source: "pypi" | "github", identifier: str }`
- `PackageSnapshot` — metadata + file map `{ path: content }`
- `Finding` — `{ kind, severity: "low"|"medium"|"high", message, location? }`
- `AutopsyReport` — `{ liveness, findings: list[Finding], target_python }`
- `RevivalResult` — `{ diffs: list[FileDiff], manual_items: list[str], applied: list[str] }`
- `ProofOfLife` — `{ imported: bool, error?: str, tests_passed: int, tests_failed: int }`

### Coroner (`backend/coroner/autopsy.py`) — pure
- `classify_liveness(last_release_date, declared_pythons, now) -> Liveness`
- `analyze_dependencies(deps) -> list[Finding]`
- `detect_deprecated_apis(source_map, target_python) -> list[Finding]`
- `run_autopsy(snapshot, target_python, now) -> AutopsyReport`

### Necromancer (`backend/necromancer/revive.py`) — pure
- `bump_dependencies(deps, target_python) -> (new_deps, applied, manual)`
- `rewrite_deprecated(source, mappings) -> (new_source, applied, manual)`
- `revive(snapshot, report) -> RevivalResult`

### Exhumer (`backend/api/` orchestration layer)
- Talks to PyPI JSON API and GitHub; or calls the MCP server tools. The only networked code.

### MCP server (`mcp-server/server.py`)
- Tool `get_pypi_metadata(name)` → metadata JSON.
- Tool `get_repo_tree(url)` → `{ path: content }`.

### API (`backend/api/main.py`)
- `POST /exhume` → `PackageSnapshot`
- `POST /autopsy` → `AutopsyReport`
- `POST /resurrect` → `RevivalResult`
- `POST /proof-of-life` → `ProofOfLife`
- `POST /revive` → runs the full pipeline, returns a combined result (used by the UI).

## Data flow for the demo
The UI calls `POST /revive` with a package name; the backend runs all four stages and
streams/returns a single payload the frontend renders as: autopsy report → diff → proof
of life. This keeps the demo to one click.

## Error Handling
- Not-found / network errors surface as typed error responses with a themed-but-accurate
  message (e.g. "The grave is empty: no PyPI package named 'foo'.").
- Pure modules never raise on bad data they can classify; they emit `Finding`s instead.
- Revival transforms that can't be applied become `manual_items`, never silent no-ops.

## Testing Strategy
- **Unit (pytest):** `classify_liveness`, `analyze_dependencies`, `detect_deprecated_apis`
  against fixture snapshots with mocked network.
- **Property-based (Hypothesis):** on pure transforms, e.g.:
  - `bump_dependencies` output never contains a pin below the target floor.
  - `rewrite_deprecated` is idempotent: applying it twice equals applying it once.
  - revival never drops a module path present in the input snapshot.
- **No network in tests** — exhumer is mocked.

## Security / Safety
- Revived code is executed only in an isolated subprocess/venv for proof-of-life, never in
  the API process. (For the hackathon demo, document this boundary clearly.)
- Secrets via environment variables only.
