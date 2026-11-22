---
inclusion: fileMatch
fileMatchPattern: ["backend/api/sandbox.py", "backend/api/main.py", "backend/api/guards.py", "backend/api/limits.py", "backend/api/exhumer.py", "backend/proof/*.py"]
---

# Sandbox and API safety invariants

The code in `api/sandbox.py`, `api/main.py`, `api/guards.py`, `api/limits.py`, `api/exhumer.py` and
`proof/` runs a package's own code, parses archives an attacker can publish, and decides what a
browser may ask the server to do. These rules came from real attack paths and real bugs in this
project. Do not weaken one without replacing its protection and its test.

## Running untrusted code (`api/sandbox.py`, `proof/discovery.py`)
- **No untrusted value reaches code or a shell.** The runner script is a static string
  (`RUNNER_SCRIPT`); inputs travel in a JSON config file. Commands are argument lists, never strings.
- **Dependencies are plain requirements only.** `validate_dependencies` rejects pip options and URLs
  (`--index-url`, `pkg @ https://...`, `-r file`). Installs use `--only-binary=:all:` so no `setup.py`
  runs, and `--` before the requirements so nothing can be read as an option.
- **Paths cannot escape.** Every path goes through `safe_relative_path`, then a resolved-path
  containment check. Reject absolute paths, `..`, backslashes, drive letters, Windows reserved names.
- **Environment is scrubbed.** Only the keys in `_BASE_ENV_KEYS` (plus proxy and cert keys for pip
  alone). HOME and TEMP point into the temp directory. Never forward `GITHUB_TOKEN` or similar.
- **Every step has a timeout, and a timeout kills the whole process tree.** Output goes to a temp
  file and only a tail is kept.
- **This is not a security boundary.** Do not describe it as a sandbox that makes untrusted code
  safe. Keep the docstring, README and UI footer honest about that.
- **No secrets in the service environment.** A same-user child can read its parent's environment
  on Linux, so scrubbing the child's env is not enough.

## Who may run code (`proof/policy.py`, `api/main.py`)
- **Every ambiguous setting fails closed.** `CRYPT_KEEPER_PUBLIC=1` (set in the Docker image) turns a
  *missing* allow-list into "no package runs". A blank allow-list means no package in any mode. Only
  an explicit `CRYPT_KEEPER_PROOF_ALLOWLIST=*` means every package. A typo in `CRYPT_KEEPER_PUBLIC`
  counts as public. Never make a missing or malformed value mean "unrestricted".
- `/proof-of-life`, which runs caller-supplied source, is refused whenever any list is in force
  (an empty list included). Never route a request-supplied package name or source to the sandbox
  without the `is_allowed` check.

## Hostile archives (`api/exhumer.py`)
- Archives are read in memory and never extracted. Entries with `..` or absolute names are skipped.
- **Entry count and unpacked size are capped before the data is read** (`MAX_ARCHIVE_ENTRIES`,
  `MAX_ARCHIVE_UNPACKED`), because the compressed-size cap alone does not stop a decompression bomb.
  An archive over a cap is refused with 413, not partly analysed.

## Telling the truth about results
- A run on a different Python than the target can show failure, never success: keep
  `target_python_matched` and `proof/verdict.py` strict. "Imports on 3.11" is not "works on 3.12".
- A truncated exhumation (`snapshot.truncated`) must stay visible in the autopsy and the log.
- Do not turn an error into "dead" or "alive" without saying which check produced it.

## API guards (`api/guards.py`, `api/limits.py`, `api/main.py`)
- Code-running endpoints stay behind the Origin check, the `application/json` check, the Host
  allow-list and the body-size limit. Never set CORS origins to `*`.
- **The body-size limit counts the bytes actually received**, not the `Content-Length` header: a
  chunked request has none. It must stop reading once the limit is crossed, and a malformed
  `Content-Length` is a 400, never a crash.
- **Guards are plain ASGI middleware, not `BaseHTTPMiddleware`.** The latter wraps errors raised while
  a body is read in an `ExceptionGroup`, which FastAPI reports as a misleading 400. Raise FastAPI's own
  `HTTPException` from inside the request stream.
- Unexpected errors return a generic message and are logged, never echoed to the client.
- Sandbox runs go through `_run_proof` so the slot limit applies.

## When you change any of this
Update or add the matching tests in `tests/test_sandbox.py`, `tests/test_proof_discovery.py`,
`tests/test_policy.py`, `tests/test_limits.py`, `tests/test_verdict.py`, `tests/test_api.py` and
`tests/test_exhumer.py`, then run the full suite, not just the quick one.
