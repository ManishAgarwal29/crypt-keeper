---
inclusion: fileMatch
fileMatchPattern: "backend/(api/(sandbox|main)|proof/.*)\\.py"
---

# Sandbox and API safety invariants

The code in `api/sandbox.py`, `api/main.py` and `proof/` runs a package's own code and decides what
a browser may ask the server to do. These rules came from real attack paths and real bugs in this
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
- **On a public host, only curated packages run** (`proof/policy.py`, `CRYPT_KEEPER_PROOF_ALLOWLIST`),
  and `/proof-of-life`, which runs caller-supplied source, is refused while a list is set. Never
  route a request-supplied package name or source to the sandbox without that check.
- **No secrets in the service environment.** A same-user child can read its parent's environment
  on Linux, so scrubbing the child's env is not enough.

## Telling the truth about results
- A run on a different Python than the target can show failure, never success: keep
  `target_python_matched` and `proof/verdict.py` strict. "Imports on 3.11" is not "works on 3.12".
- A truncated exhumation (`snapshot.truncated`) must stay visible in the autopsy and the log.
- Do not turn an error into "dead" or "alive" without saying which check produced it.

## API guards (`api/main.py`)
- Code-running endpoints stay behind the Origin check, the `application/json` check, the Host
  allow-list and the body-size limit. Never set CORS origins to `*`.
- Unexpected errors return a generic message and are logged, never echoed to the client.
- Sandbox runs go through `_run_proof` so the slot limit applies.

## When you change any of this
Update or add the matching tests in `tests/test_sandbox.py`, `tests/test_proof_discovery.py`,
`tests/test_verdict.py` and `tests/test_api.py`, then run the full suite, not just the quick one.
