"""The sandbox: runs a revived package in a throwaway virtualenv and reports proof of life.

This is the only code that executes the package under revival. What it does and does not do:

  * Files are written to a fresh temp directory; paths are validated so nothing can escape it.
  * Dependencies are installed into a new virtualenv with `--only-binary=:all:`, so no
    dependency's setup.py runs. Pip options, URLs and paths in dependency strings are rejected.
  * The package runs with a scrubbed environment (no API tokens, HOME pointed at the temp dir),
    a hard timeout per step, and its whole process tree is killed when the step ends.
  * Output, disk use and (on Linux) file size, memory, core dumps and process count are capped.
    Processes left behind by a package are killed after every step: by process group, and on
    Linux also by a per-run marker in their environment. A process that scrubs its own
    environment and starts a new session can still escape; the container is the real boundary.

It is NOT a security boundary. The package's own code runs as the current user, with network
access and read access to the user's files. Only revive packages you would be willing to
import. Hosting this publicly needs a container or VM around it; set
CRYPT_KEEPER_PROOF_OF_LIFE=off to disable it.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from models import PackageSnapshot, ProofOfLife
from proof.discovery import (
    CONFIG_FILENAME,
    RESULT_FILENAME,
    RUNNER_SCRIPT,
    discover_modules,
    encode_source,
    find_shipped_tests,
    is_runtime_source,
    limited_command,
    parse_pytest_summary,
    pick_primary,
    pytest_command,
    redact,
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
MAX_OUTPUT_BYTES = 10_000_000
MAX_RUN_DIR_BYTES = 1_000_000_000
POLL_SECONDS = 0.1
DIR_CHECK_SECONDS = 2.0
MIN_STEP_SECONDS = 2.0
MAX_CACHED_INTERPRETERS = 16
RUN_MARKER_ENV = "CRYPT_KEEPER_RUN"
CHILD_MEMORY_ENV = "CRYPT_KEEPER_CHILD_MEM_MB"
DEFAULT_CHILD_MEMORY_MB = 3072
MAX_CHILD_FILE_BYTES = 256_000_000
MAX_CHILD_PROCESSES = 2048
LOGGER = logging.getLogger("crypt_keeper.sandbox")


class OutOfTime(Exception):
    """The run's total time budget is spent."""

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
    limit: str | None = None  # "timeout", "output" or "disk" when the step was stopped for exceeding one


def child_limits() -> dict[str, int]:
    """Resource limits for every sandboxed process (POSIX only; empty on Windows)."""
    if os.name == "nt":
        return {}
    limits = {"RLIMIT_CORE": 0, "RLIMIT_FSIZE": MAX_CHILD_FILE_BYTES, "RLIMIT_NPROC": MAX_CHILD_PROCESSES}
    try:
        mem_mb = int(os.environ.get(CHILD_MEMORY_ENV, DEFAULT_CHILD_MEMORY_MB))
    except ValueError:
        mem_mb = DEFAULT_CHILD_MEMORY_MB
    if mem_mb > 0:
        limits["RLIMIT_AS"] = mem_mb * 1024 * 1024
    return limits


def _dir_exceeds(path: Path | str, cap: int) -> bool:
    """True once the files under `path` add up to more than `cap` bytes. Stops counting at the cap."""
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
            if total > cap:
                return True
    return False


def run_command(
    cmd: list[str], cwd: Path | str, env: dict[str, str], timeout: float, tail: int = LOG_TAIL,
    *, watch_dir: Path | str | None = None, limits: dict[str, int] | None = None,
) -> RunResult:
    """Run `cmd`, capturing combined output to a temp file.

    Stops the process and its tree when the timeout passes, when the output exceeds MAX_OUTPUT_BYTES, or
    (with `watch_dir`) when that directory grows past MAX_RUN_DIR_BYTES. The tree is killed when the
    command ends normally too, so a daemon the package started does not outlive the step.
    """
    if limits and os.name != "nt":
        cmd = limited_command(sys.executable, cmd, limits)
    popen_kwargs: dict = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    with tempfile.TemporaryFile() as out:
        try:
            proc = subprocess.Popen(
                cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, **popen_kwargs
            )
        except OSError as exc:
            LOGGER.warning("Could not start %s: %s", cmd[0], exc)
            return RunResult(None, f"Could not start {os.path.basename(cmd[0])}.", False)
        limit = _wait(proc, out, timeout, watch_dir)
        if limit or os.name != "nt":
            _kill_tree(proc)
        size = out.seek(0, os.SEEK_END)
        out.seek(max(0, size - tail * 4))
        text = out.read().decode("utf-8", errors="replace")
    return RunResult(None if limit else proc.returncode, text[-tail:], limit == "timeout", limit)


def _wait(proc: subprocess.Popen, out, timeout: float, watch_dir: Path | str | None) -> str | None:
    """Wait for `proc`. Returns the limit that ended the wait, or None if the process exited by itself."""
    started = time.monotonic()
    next_dir_check = started + DIR_CHECK_SECONDS
    while True:
        try:
            proc.wait(timeout=POLL_SECONDS)
            return None
        except subprocess.TimeoutExpired:
            pass
        now = time.monotonic()
        if now - started >= timeout:
            return "timeout"
        try:
            if os.fstat(out.fileno()).st_size > MAX_OUTPUT_BYTES:
                return "output"
        except OSError:
            pass
        if watch_dir is not None and now >= next_dir_check:
            if _dir_exceeds(watch_dir, MAX_RUN_DIR_BYTES):
                return "disk"
            next_dir_check = time.monotonic() + DIR_CHECK_SECONDS


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=20)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def reap_run(run_id: str) -> int:
    """Kill every process that carries this run's marker in its environment (Linux). Returns how many."""
    proc_dir = Path("/proc")
    if not sys.platform.startswith("linux") or not proc_dir.is_dir():
        return 0
    import signal
    marker = f"{RUN_MARKER_ENV}={run_id}".encode()
    killed = 0
    for _ in range(5):  # a process may fork between the scan and the kill, so look again
        found = 0
        for entry in proc_dir.iterdir():
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            try:
                environ = (entry / "environ").read_bytes()
            except OSError:
                continue
            if marker in environ.split(b"\0"):
                try:
                    os.kill(int(entry.name), signal.SIGKILL)
                    found += 1
                except OSError:
                    pass
        killed += found
        if not found:
            break
    return killed


def _step_timeout(step_timeout: float, deadline: float) -> float:
    """The step's own timeout, cut down to what is left of the run's budget."""
    remaining = deadline - time.monotonic()
    if remaining < MIN_STEP_SECONDS:
        raise OutOfTime
    return min(step_timeout, remaining)


def _env(home: Path, *, pip: bool = False, pythonpath: str | None = None, run_id: str | None = None) -> dict[str, str]:
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
    if run_id:
        env[RUN_MARKER_ENV] = run_id
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


def _plausible(version: tuple[int, int]) -> bool:
    return version[0] == 3 and 8 <= version[1] <= 20


_interpreters: dict[str, tuple[str, str, bool]] = {}
_interpreters_lock = threading.Lock()


def clear_interpreter_cache() -> None:
    with _interpreters_lock:
        _interpreters.clear()


def find_interpreter_cached(target_python: str) -> tuple[str, str, bool]:
    """`find_interpreter`, remembered per major.minor so requests do not each spawn probe processes.

    Every target that is not a plausible Python 3 version shares one entry, and the cache is bounded.
    """
    want = _parse_minor(target_python)
    key = f"{want[0]}.{want[1]}" if want and _plausible(want) else "other"
    with _interpreters_lock:
        hit = _interpreters.get(key)
    if hit:
        return hit
    found = find_interpreter(key if key != "other" else target_python)
    with _interpreters_lock:
        while len(_interpreters) >= MAX_CACHED_INTERPRETERS:
            _interpreters.pop(next(iter(_interpreters)))
        _interpreters[key] = found
    return found


def find_interpreter(target_python: str, env: dict[str, str] | None = None) -> tuple[str, str, bool]:
    """(executable, version, matched): an interpreter for the target Python, else the current one.

    `matched` is False when no interpreter for the target is installed, in which case the run
    cannot prove the package works on the target.
    """
    env = env if env is not None else _env(Path(tempfile.gettempdir()))
    want = _parse_minor(target_python)
    if want and not _plausible(want):
        want = None
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
            dest.write_bytes(encode_source(content))
        except (OSError, UnicodeEncodeError) as exc:
            LOGGER.warning("Could not write %r: %s", path, exc)
            return f"Could not write {path!r} into the sandbox."
    return None


def _install(py: str, reqs: list[str], cwd: Path, home: Path, log: list[str], deadline: float, step=None, run_id: str | None = None) -> list[str]:
    """Install requirements wheel-only. Falls back to one at a time. Returns the ones that failed."""
    if not reqs:
        return []
    if step is None:
        def step(cmd, cwd, env, timeout):
            return run_command(cmd, cwd, env, _step_timeout(timeout, deadline))
    base = [py, "-m", "pip", "install", "--only-binary=:all:", "--no-input", "--disable-pip-version-check", "--quiet", "--"]
    env = _env(home, pip=True, run_id=run_id)
    r = step(base + reqs, cwd, env, INSTALL_TIMEOUT)
    if r.returncode == 0:
        return []
    log.append(f"Installing all dependencies failed; retrying one by one.\n{r.output[-1500:]}")
    failed: list[str] = []
    for i, req in enumerate(reqs):
        try:
            ok = step(base + [req], cwd, env, SINGLE_INSTALL_TIMEOUT).returncode == 0
        except OutOfTime:
            failed.extend(reqs[i:])
            break
        if not ok:
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
    except OutOfTime:
        result = ProofOfLife(imported=False, error=f"The run used up its time budget ({TOTAL_BUDGET}s) before it could finish.")
    except Exception:  # a sandbox failure must never take the API down, nor show the client server details
        LOGGER.exception("Sandbox failure while running %s", snapshot.ref.identifier)
        result = ProofOfLife(imported=False, error="The sandbox failed unexpectedly. The details were logged on the server.")
    result.duration_seconds = round(time.monotonic() - started, 1)
    return result


def _limit_message(result: RunResult, what: str, timeout: float) -> str | None:
    """Why a step was stopped, in words a user can act on. None if it was not stopped by a limit."""
    if result.limit == "timeout":
        return f"{what} timed out after {timeout:g}s."
    if result.limit == "output":
        return f"{what} was stopped: it went over the output limit ({MAX_OUTPUT_BYTES // 1_000_000} MB)."
    if result.limit == "disk":
        return f"{what} was stopped: it went over the disk limit ({MAX_RUN_DIR_BYTES // 1_000_000} MB)."
    return None


def _run(snapshot: PackageSnapshot, target_python: str, started: float) -> ProofOfLife:
    deadline = started + TOTAL_BUDGET
    paths = sorted(snapshot.source_map)
    plan = discover_modules(paths)
    if not plan.modules:
        # Nothing here can be imported, so nothing can be checked. That is not the same as dead: a distribution that is
        # only a `setup.py` is usually a placeholder that installs another package. Say so and run nothing.
        only_build_files = all(not is_runtime_source(p) for p in paths)
        hint = (
            f" It looks like a placeholder: only build or test files, and it depends on {', '.join(snapshot.dependencies[:5])}."
            if only_build_files and snapshot.dependencies else ""
        )
        return ProofOfLife(
            imported=False, skipped=True,
            error="No importable Python package or module was found in the source, so there is nothing to run." + hint,
        )

    interpreter, version, matched = find_interpreter_cached(target_python)
    tests = find_shipped_tests(paths)
    deps, rejected = validate_dependencies(snapshot.dependencies)
    primary = pick_primary(plan.modules, snapshot.ref.identifier)
    calls = smoke_calls(plan, snapshot.source_map)
    run_id = uuid.uuid4().hex
    limits = child_limits()
    replacements: dict[str, str] = {interpreter: "<python>", sys.executable: "<python>", str(Path.home()): "~"}

    log = [f"Ran on Python {version}."]
    if snapshot.truncated:
        log.append("The source was truncated by the exhumer's limits, so a failed import may only mean files are missing.")
    if not matched:
        log.append(f"Python {target_python} is not installed here, so this run does not prove the package works on it.")
    if rejected:
        log.append(f"Skipped dependencies that are not plain requirements: {rejected}")

    def clean(text: str | None) -> str | None:
        return redact(text, replacements) if text else text

    def outcome(**kw) -> ProofOfLife:
        if kw.get("error"):
            kw["error"] = clean(kw["error"])
        if kw.get("import_results"):
            kw["import_results"] = {k: clean(v) for k, v in kw["import_results"].items()}
        return ProofOfLife(python_used=version, target_python_matched=matched, output=_tail(clean("\n".join(log))), **kw)

    with tempfile.TemporaryDirectory(prefix="crypt-keeper-", ignore_cleanup_errors=True) as tmp_name:
        tmp = Path(tmp_name)
        for form in (tmp, tmp.resolve()):
            replacements[str(form)] = "<sandbox>"
            replacements[form.as_posix()] = "<sandbox>"
        src, home, venv_dir = tmp / "src", tmp / "home", tmp / "venv"
        src.mkdir()
        home.mkdir()

        def step(cmd, cwd, env, timeout, tail: int = LOG_TAIL) -> RunResult:
            """One sandboxed command: clipped to the remaining budget, limited, watched, and cleaned up after."""
            try:
                return run_command(cmd, cwd, env, _step_timeout(timeout, deadline), tail, watch_dir=tmp, limits=limits)
            finally:
                reap_run(run_id)

        try:
            problem = _write_sources(src, snapshot.source_map)
            if problem:
                return outcome(imported=False, error=problem)

            made = step([interpreter, "-m", "venv", str(venv_dir)], tmp, _env(home, run_id=run_id), VENV_TIMEOUT)
            if made.returncode != 0:
                LOGGER.warning("Could not create the virtualenv: %s", made.output)
                return outcome(imported=False, error="Could not create the sandbox virtualenv.")
            py = _venv_python(venv_dir)

            failed_installs = _install(py, deps + (["pytest"] if tests else []), tmp, home, log, deadline, step, run_id)

            roots = [str(src / r) if r else str(src) for r in plan.roots]
            (tmp / "runner.py").write_text(RUNNER_SCRIPT, encoding="utf-8")
            (tmp / CONFIG_FILENAME).write_text(json.dumps({
                "roots": roots, "modules": plan.modules, "calls": calls, "result": str(tmp / RESULT_FILENAME),
            }), encoding="utf-8")

            ran = step([py, "-I", "-B", str(tmp / "runner.py"), str(tmp / CONFIG_FILENAME)], src, _env(home, run_id=run_id), IMPORT_TIMEOUT)
            if ran.output.strip():
                log.append(f"Import check output:\n{ran.output}")
            results = _read_json(tmp / RESULT_FILENAME)

            counts = None
            if tests:
                try:
                    tested = step(
                        pytest_command(py, str(src), tests), src, _env(home, pythonpath=os.pathsep.join(roots), run_id=run_id), TEST_TIMEOUT,
                    )
                except OutOfTime:
                    log.append("Shipped tests were skipped: the run's time budget was spent.")
                else:
                    counts = parse_pytest_summary(tested.output)
                    note = _limit_message(tested, "The shipped tests", TEST_TIMEOUT)
                    log.append(f"Shipped tests{' (' + note + ')' if note else ''}:\n{tested.output[-2500:]}")
        finally:
            reap_run(run_id)

    imports: dict[str, str | None] = results.get("imports", {}) if results else {}
    imported = bool(results) and primary in imports and imports[primary] is None
    error = None
    if not imported:
        error = imports.get(primary) or _limit_message(ran, "Import check", IMPORT_TIMEOUT) or "Import check crashed before reporting a result."
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
