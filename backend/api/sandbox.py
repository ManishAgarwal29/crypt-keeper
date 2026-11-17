"""The sandbox: runs a revived package in a throwaway virtualenv and reports proof of life.

This is the only code that executes the package under revival. What it does and does not do:

  * Files are written to a fresh temp directory; paths are validated so nothing can escape it.
  * Dependencies are installed into a new virtualenv with `--only-binary=:all:`, so no
    dependency's setup.py runs. Pip options, URLs and paths in dependency strings are rejected.
  * The package runs with a scrubbed environment (no API tokens, HOME pointed at the temp dir),
    a hard timeout per step, and its whole process tree is killed on timeout.

It is NOT a security boundary. The package's own code runs as the current user, with network
access and read access to the user's files. Only revive packages you would be willing to
import. Hosting this publicly needs a container or VM around it; set
CRYPT_KEEPER_PROOF_OF_LIFE=off to disable it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from models import PackageSnapshot, ProofOfLife
from proof.discovery import (
    CONFIG_FILENAME,
    RESULT_FILENAME,
    RUNNER_SCRIPT,
    discover_modules,
    find_shipped_tests,
    parse_pytest_summary,
    pick_primary,
    safe_relative_path,
    smoke_calls,
    validate_dependencies,
)

ENABLE_ENV = "CRYPT_KEEPER_PROOF_OF_LIFE"
MAX_FILES = 2000
MAX_TOTAL_BYTES = 10_000_000
VENV_TIMEOUT = 180
INSTALL_TIMEOUT = 240
SINGLE_INSTALL_TIMEOUT = 90
IMPORT_TIMEOUT = 60
TEST_TIMEOUT = 180
TOTAL_BUDGET = 600
LOG_TAIL = 6000

_BASE_ENV_KEYS = ["PATH", "PATHEXT", "SYSTEMROOT", "SystemRoot", "SystemDrive", "WINDIR", "COMSPEC"]
_PIP_ENV_KEYS = [
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL",
]


@dataclass
class RunResult:
    returncode: int | None
    output: str
    timed_out: bool


def run_command(cmd: list[str], cwd: Path | str, env: dict[str, str], timeout: float, tail: int = LOG_TAIL) -> RunResult:
    """Run `cmd`, capturing combined output to a temp file. Kills the process tree on timeout."""
    popen_kwargs: dict = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    with tempfile.TemporaryFile() as out:
        try:
            proc = subprocess.Popen(
                cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, **popen_kwargs
            )
        except OSError as exc:
            return RunResult(None, f"Could not start {cmd[0]}: {exc}", False)
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc)
        size = out.seek(0, os.SEEK_END)
        out.seek(max(0, size - tail * 4))
        text = out.read().decode("utf-8", errors="replace")
    return RunResult(None if timed_out else proc.returncode, text[-tail:], timed_out)


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=20)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _env(home: Path, *, pip: bool = False, pythonpath: str | None = None) -> dict[str, str]:
    """A minimal environment: no tokens or user config leak into the package or pip."""
    keys = _BASE_ENV_KEYS + (_PIP_ENV_KEYS if pip else [])
    env = {k: os.environ[k] for k in keys if k in os.environ}
    env.update({
        "HOME": str(home), "USERPROFILE": str(home), "TEMP": str(home), "TMP": str(home), "TMPDIR": str(home),
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
        "PIP_NO_INPUT": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    })
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    return env


def _parse_minor(version: str) -> tuple[int, int] | None:
    parts = version.strip().split(".")
    try:
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None


def _interpreter_version(exe: str, env: dict[str, str]) -> str | None:
    r = run_command([exe, "-c", "import sys;print('%d.%d.%d' % sys.version_info[:3])"], Path.cwd(), env, 20, tail=200)
    last = r.output.strip().splitlines()[-1] if r.output.strip() else ""
    return last if r.returncode == 0 and _parse_minor(last) else None


def find_interpreter(target_python: str, env: dict[str, str] | None = None) -> tuple[str, str, bool]:
    """(executable, version, matched): an interpreter for the target Python, else the current one.

    `matched` is False when no interpreter for the target is installed, in which case the run
    cannot prove the package works on the target.
    """
    env = env if env is not None else _env(Path(tempfile.gettempdir()))
    want = _parse_minor(target_python)
    candidates: list[str] = []
    if want:
        if sys.version_info[:2] == want:
            candidates.append(sys.executable)
        if os.name == "nt" and shutil.which("py"):
            r = run_command(["py", f"-{want[0]}.{want[1]}", "-c", "import sys;print(sys.executable)"], Path.cwd(), env, 20, tail=500)
            lines = r.output.strip().splitlines()
            if r.returncode == 0 and lines and Path(lines[-1]).is_file():
                candidates.append(lines[-1])
        found = shutil.which(f"python{want[0]}.{want[1]}")
        if found:
            candidates.append(found)
    for exe in candidates:
        version = _interpreter_version(exe, env)
        if version and _parse_minor(version) == want:
            return exe, version, True
    fallback = _interpreter_version(sys.executable, env) or "%d.%d.%d" % sys.version_info[:3]
    return sys.executable, fallback, False


def _venv_python(venv_dir: Path) -> str:
    return str(venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))


def _write_sources(root: Path, source_map: dict[str, str]) -> str | None:
    """Write the files under `root`. Returns an error message, or None on success."""
    if len(source_map) > MAX_FILES:
        return f"Too many source files to run ({len(source_map)} > {MAX_FILES})."
    if sum(len(c) for c in source_map.values()) > MAX_TOTAL_BYTES:
        return "The source is too large to run in the sandbox."
    base = root.resolve()
    for path, content in source_map.items():
        rel = safe_relative_path(path)
        if rel is None:
            return f"Refusing to write an unsafe path: {path!r}."
        dest = (base / rel).resolve()
        if not dest.is_relative_to(base):
            return f"Refusing to write outside the sandbox: {path!r}."
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content.encode("utf-8"))
        except (OSError, UnicodeEncodeError) as exc:
            return f"Could not write {path!r}: {exc}"
    return None


def _install(py: str, reqs: list[str], cwd: Path, home: Path, log: list[str], deadline: float) -> list[str]:
    """Install requirements wheel-only. Falls back to one at a time. Returns the ones that failed."""
    if not reqs:
        return []
    base = [py, "-m", "pip", "install", "--only-binary=:all:", "--no-input", "--disable-pip-version-check", "--quiet", "--"]
    env = _env(home, pip=True)
    r = run_command(base + reqs, cwd, env, INSTALL_TIMEOUT)
    if r.returncode == 0:
        return []
    log.append(f"Installing all dependencies failed; retrying one by one.\n{r.output[-1500:]}")
    failed: list[str] = []
    for req in reqs:
        if time.monotonic() > deadline:
            failed.append(req)
            continue
        if run_command(base + [req], cwd, env, SINGLE_INSTALL_TIMEOUT).returncode != 0:
            failed.append(req)
    log.append(f"Could not install (wheel-only): {failed}")
    return failed


def _tail(text: str, limit: int = LOG_TAIL) -> str:
    return text if len(text) <= limit else "..." + text[-limit:]


def proof_enabled() -> bool:
    return os.environ.get(ENABLE_ENV, "on").strip().lower() not in ("off", "0", "false", "no")


def run_proof_of_life(snapshot: PackageSnapshot, target_python: str = "3.12") -> ProofOfLife:
    """Import the (revived) package in a sandbox and run its tests, or a generated smoke test."""
    started = time.monotonic()
    if not proof_enabled():
        return ProofOfLife(imported=False, skipped=True, error=f"Proof of life is disabled ({ENABLE_ENV}=off).")
    try:
        result = _run(snapshot, target_python, started)
    except Exception as exc:  # a sandbox failure must never take the API down
        result = ProofOfLife(imported=False, error=f"Sandbox failure: {type(exc).__name__}: {exc}")
    result.duration_seconds = round(time.monotonic() - started, 1)
    return result


def _run(snapshot: PackageSnapshot, target_python: str, started: float) -> ProofOfLife:
    deadline = started + TOTAL_BUDGET
    paths = sorted(snapshot.source_map)
    plan = discover_modules(paths)
    if not plan.modules:
        return ProofOfLife(imported=False, error="No importable Python package or module found in the source.")

    interpreter, version, matched = find_interpreter(target_python)
    tests = find_shipped_tests(paths)
    deps, rejected = validate_dependencies(snapshot.dependencies)
    primary = pick_primary(plan.modules, snapshot.ref.identifier)
    calls = smoke_calls(plan, snapshot.source_map)

    log = [f"Ran on Python {version}."]
    if snapshot.truncated:
        log.append("The source was truncated by the exhumer's limits, so a failed import may only mean files are missing.")
    if not matched:
        log.append(f"Python {target_python} is not installed here, so this run does not prove the package works on it.")
    if rejected:
        log.append(f"Skipped dependencies that are not plain requirements: {rejected}")

    def outcome(**kw) -> ProofOfLife:
        return ProofOfLife(python_used=version, target_python_matched=matched, output=_tail("\n".join(log)), **kw)

    with tempfile.TemporaryDirectory(prefix="crypt-keeper-", ignore_cleanup_errors=True) as tmp_name:
        tmp = Path(tmp_name)
        src, home, venv_dir = tmp / "src", tmp / "home", tmp / "venv"
        src.mkdir()
        home.mkdir()

        problem = _write_sources(src, snapshot.source_map)
        if problem:
            return outcome(imported=False, error=problem)

        made = run_command([interpreter, "-m", "venv", str(venv_dir)], tmp, _env(home), VENV_TIMEOUT)
        if made.returncode != 0:
            log.append(made.output)
            return outcome(imported=False, error="Could not create the sandbox virtualenv.")
        py = _venv_python(venv_dir)

        failed_installs = _install(py, deps + (["pytest"] if tests else []), tmp, home, log, deadline)

        roots = [str(src / r) if r else str(src) for r in plan.roots]
        (tmp / "runner.py").write_text(RUNNER_SCRIPT, encoding="utf-8")
        (tmp / CONFIG_FILENAME).write_text(json.dumps({
            "roots": roots, "modules": plan.modules, "calls": calls, "result": str(tmp / RESULT_FILENAME),
        }), encoding="utf-8")

        ran = run_command([py, "-I", "-B", str(tmp / "runner.py"), str(tmp / CONFIG_FILENAME)], src, _env(home), IMPORT_TIMEOUT)
        if ran.output.strip():
            log.append(f"Import check output:\n{ran.output}")
        results = _read_json(tmp / RESULT_FILENAME)

        counts = None
        if tests:
            tested = run_command(
                [py, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--continue-on-collection-errors",
                 "-o", "addopts=", "--rootdir", str(src), *tests],
                src, _env(home, pythonpath=os.pathsep.join(roots)), TEST_TIMEOUT,
            )
            counts = parse_pytest_summary(tested.output)
            log.append(f"Shipped tests{' (timed out)' if tested.timed_out else ''}:\n{tested.output[-2500:]}")

    imports: dict[str, str | None] = results.get("imports", {}) if results else {}
    imported = bool(results) and primary in imports and imports[primary] is None
    error = None
    if not imported:
        error = imports.get(primary) or (
            f"Import check timed out after {IMPORT_TIMEOUT}s." if ran.timed_out
            else "Import check crashed before reporting a result."
        )
        if failed_installs:
            error += f" (could not install: {', '.join(failed_installs)})"

    if counts and counts.passed + counts.failed + counts.errors > 0:
        return outcome(
            imported=imported, error=error, test_source="shipped", import_results=imports,
            tests_passed=counts.passed, tests_failed=counts.failed, tests_errors=counts.errors,
        )

    call_results = results.get("calls", []) if results else []
    passed = sum(1 for e in imports.values() if e is None) + sum(1 for c in call_results if c["error"] is None)
    failed = sum(1 for e in imports.values() if e is not None) + sum(1 for c in call_results if c["error"] is not None)
    for c in call_results:
        if c["error"]:
            log.append(f"Smoke call {c['target']}() failed: {c['error']}")
    return outcome(
        imported=imported, error=error, test_source="smoke", import_results=imports,
        tests_passed=passed, tests_failed=failed,
    )


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
