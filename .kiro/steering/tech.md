# Tech Standards: Crypt Keeper

## Stack
- **Backend:** Python 3.11+, FastAPI, Pydantic v2.
- **Frontend:** Plain HTML/CSS/JS kept dependency-light; haunted theme via CSS.
- **Testing:** pytest for unit tests, Hypothesis for property-based testing (PBT).
- **MCP:** a local MCP server exposing PyPI/GitHub exhumation tools.
- **Packaging:** `requirements.txt` for backend; pin only where required for reproducibility.

## Conventions
- Type-hint all Python functions. Prefer Pydantic models for API payloads.
- Keep the Coroner (analysis) and Necromancer (revival) modules independent and testable.
- No network calls in unit tests — mock PyPI/GitHub. PBT runs on pure functions only.
- Every API endpoint returns a typed response model and documents its failure modes.
- Secrets (GitHub tokens, LLM keys) come from environment variables, never committed.

## Commands (Windows PowerShell)
- Create venv: `python -m venv .venv ; .venv\Scripts\activate`
- Install: `pip install -r backend/requirements.txt`
- Run API: `uvicorn api.main:app --reload` (from `backend/`)
- Test: `pytest` (from `backend/`)

## Quality bar
- Run `pytest` before considering any task done.
- New feature or bug fix ⇒ add/extend tests.
- Keep functions small and pure where possible so PBT has clean targets.
