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
- **Every step has a timeout, and the process tree is killed when the step ends** (timeout, output
  or disk limit, or a normal exit), so nothing a package started outlives its step. Steps draw on one
  total time budget (`TOTAL_BUDGET`, below the host's request timeout). Output goes to a temp file
  and only a tail is kept; output size and the run directory are watched.
- **Children run under resource limits** (`child_limits`, applied by the static `LIMIT_LAUNCHER`; the
  limits and the command travel as data, never as code).
- **Nothing server-side reaches a client.** Errors are generic, details go to the log, and the temp
  directory, interpreter path and home directory are redacted from `error`, `output` and `import_results`.
- **This is not a security boundary.** Do not describe it as a sandbox that makes untrusted code
  safe. Keep the docstring, README and UI footer honest about that.
- **No secrets in the service environment.** A same-user child can read its parent's environment
  on Linux, so scrubbing the child's env is not enough.

## Who may run code (`proof/policy.py`, `api/main.py`)
- **Every ambiguous setting fails closed.** `CRYPT_KEEPER_PUBLIC=1` (set in the Docker image) turns a
  *missing* allow-list into "no package runs". A blank allow-list means no package in any mode. Only
  an explicit `CRYPT_KEEPER_PROOF_ALLOWLIST=*` means every package. A typo in `CRYPT_KEEPER_PUBLIC`
  counts as public. Never make a missing or malformed value mean "unrestricted".
- **A listed package is not enough: its dependencies need a review too.** On a public host with a list,
  `_run_proof` refuses a package whose direct dependencies are not in `approved_dependencies.json`, and
  a package with no entry. A missing or malformed file approves nothing.
- `/proof-of-life`, which runs caller-supplied source, is refused whenever any list is in force
  (an empty list included). Never route a request-supplied package name or source to the sandbox
  without the `is_allowed` check.

## Hostile archives (`api/exhumer.py`)
- Archives are read in memory and never extracted. Entries with `..` or absolute names are skipped.
- **Entry count and unpacked size are capped before the data is read** (`MAX_ARCHIVE_ENTRIES`,
  `MAX_ARCHIVE_UNPACKED`), because the compressed-size cap alone does not stop a decompression bomb.
  An archive over a cap is refused with 413, not partly analysed.

## Reading and writing source files
- **Metadata is read, never run.** `install_requires` comes from `setup.py` through `ast` (`install_requires_from_setup_py`),
  never by importing or executing it. Anything computed is ignored, not guessed.
- **Source is decoded and encoded by its own `# coding:` line** (`decode_source`, `encode_source`). The codec name is limited to
  letters, digits, `_` and `-`, so a hostile file cannot make the codec registry import an arbitrary module. A file that does not
  decode marks the snapshot truncated.
- **A revival can add a dependency, and the public host must have reviewed it.** `declare_setuptools` adds `setuptools` or
  `setuptools<81` for code that imports `pkg_resources`, `distutils` or `setuptools`. The server runs the revived package, so that
  name has to be in `approved_dependencies.json` or the run is refused. `scripts/review_dependencies.py` reviews both the declared
  and the revival-added names; run it whenever a listed package or the revival rules change, and check the four listed packages
  still run in public mode (`scripts/smoke_test.py`).
- **A rewrite must mean the same, or it stays manual.** `inspect.getargspec` is rewritten only for shapes proven equivalent
  (the four-field unpack, field access, indexes 0 to 3); an unprovable use is reported, never guessed. A rewrite is tested by running
  the rewritten code, not only by comparing text.
- **The export (`POST /revive/export`) executes nothing.** It goes through `_exhume` (so the exhume slot limit applies), the same Origin and
  content-type guards as every POST, and builds the zip in memory from paths that passed `safe_relative_path`. The download name is
  sanitised before it reaches a header.

## Telling the truth about results
- A run on a different Python than the target can show failure, never success: keep
  `target_python_matched` and `proof/verdict.py` strict. "Imports on 3.11" is not "works on 3.12".
- A truncated exhumation (`snapshot.truncated`) must stay visible in the autopsy and the log, and a
  failed run on truncated source is `unverified`, never dead. A file the exhumer cannot read, or that
  shares a path with another, counts as truncation.
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
`tests/test_policy.py`, `tests/test_limits.py`, `tests/test_verdict.py`, `tests/test_api.py`,
`tests/test_exhumer.py`, `tests/test_sandbox_hardening.py`, `tests/test_sandbox_errors.py`,
`tests/test_approved_dependencies.py` and `tests/test_exhumer_fidelity.py`, then run the full suite, not
just the quick one. The Linux-only tests (process groups, `/proc`, rlimits) are skipped on Windows; run
them in the container as described at the top of `tests/test_sandbox_hardening.py`.
