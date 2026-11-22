"""Sandbox hardening: processes that outlive a run, runaway output and disk use, and resource limits.

Most of these behaviours are Linux behaviours (process groups, /proc, resource limits), so those tests only
run there. On Windows run them in the project's container:

    docker run --rm -v <repo>:/work -w /work/backend python:3.12-slim sh -c \
        "pip install -q -r requirements.txt && python -m pytest -q tests/test_sandbox_hardening.py"

The platform-independent ones (output and disk watching, the time budget) run everywhere.
"""
import os
import sys
import time
from pathlib import Path

import pytest

from api import sandbox
from api.sandbox import run_command, run_proof_of_life
from models import PackageRef, PackageSnapshot, Source

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs Linux process semantics")
CURRENT = "%d.%d" % sys.version_info[:2]


def snap(files: dict[str, str]) -> PackageSnapshot:
    return PackageSnapshot(ref=PackageRef(source=Source.pypi, identifier="demo"), source_map=files)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a zombie still answers signal 0; treat it as gone
        return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0] != "Z"
    except OSError:
        return False


def wait_gone(pid: int, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.1)
    return not alive(pid)


# --- F5: a process left behind by a package must not outlive the run ---------------------------------------


DAEMONISE = '''
import os, sys, time
def start_daemon(pidfile, new_session):
    if os.fork() == 0:                      # first child
        if new_session:
            os.setsid()                     # leave the process group, as a daemon would
        if os.fork() == 0:                  # grandchild: reparented to init when the parent exits
            open(pidfile, "w").write(str(os.getpid()))
            time.sleep(300)
        os._exit(0)
'''


@linux_only
@pytest.mark.parametrize("new_session", [False, True], ids=["same-process-group", "own-session-setsid"])
def test_a_daemon_started_by_the_package_is_killed_when_the_run_ends(tmp_path, new_session):
    pidfile = tmp_path / "daemon.pid"
    source = DAEMONISE + f"\nstart_daemon({str(pidfile)!r}, {new_session})\ntime.sleep(1)\n"
    result = run_proof_of_life(snap({"demo/__init__.py": source}), CURRENT)
    assert result.imported, result.error
    assert pidfile.exists(), "the package never started its daemon"
    pid = int(pidfile.read_text())
    assert wait_gone(pid), "the daemon survived the end of the run"


@linux_only
def test_reap_run_kills_only_processes_that_carry_the_runs_marker(tmp_path):
    import subprocess

    marked = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], env={**os.environ, "CRYPT_KEEPER_RUN": "run-a"})
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], env={**os.environ, "CRYPT_KEEPER_RUN": "run-b"})
    plain = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert sandbox.reap_run("run-a") == 1
        assert marked.wait(timeout=5) is not None
        assert other.poll() is None and plain.poll() is None, "unrelated processes must be left alone"
    finally:
        for p in (marked, other, plain):
            p.kill()
            p.wait()


@linux_only
def test_a_marker_that_only_looks_similar_does_not_match(tmp_path):
    import subprocess

    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], env={**os.environ, "CRYPT_KEEPER_RUN": "run-a-and-more"})
    try:
        assert sandbox.reap_run("run-a") == 0 and p.poll() is None
    finally:
        p.kill()
        p.wait()


# --- F6: resource limits on every sandboxed process (Linux) --------------------------------------------------


@linux_only
def test_the_child_really_runs_with_the_configured_limits(tmp_path):
    code = "import resource; print([resource.getrlimit(getattr(resource, n))[0] for n in ('RLIMIT_FSIZE', 'RLIMIT_CORE', 'RLIMIT_NPROC')])"
    limits = sandbox.child_limits()
    result = run_command([sys.executable, "-c", code], tmp_path, dict(os.environ), 30, limits=limits)
    assert result.output.strip() == str([limits["RLIMIT_FSIZE"], 0, limits["RLIMIT_NPROC"]])


@linux_only
def test_a_file_bigger_than_the_size_limit_cannot_be_written(tmp_path):
    code = "f = open('big.bin', 'wb'); f.write(b'x' * 300_000_000); f.flush()"
    result = run_command([sys.executable, "-c", code], tmp_path, dict(os.environ), 60, limits={"RLIMIT_FSIZE": 1_000_000})
    assert result.returncode != 0
    assert (tmp_path / "big.bin").stat().st_size <= 1_000_000


@linux_only
def test_the_memory_limit_stops_a_runaway_allocation(tmp_path):
    code = "x = bytearray(2_000_000_000); print('allocated')"
    result = run_command([sys.executable, "-c", code], tmp_path, dict(os.environ), 60, limits={"RLIMIT_AS": 512 * 1024 * 1024})
    assert "allocated" not in result.output and result.returncode != 0


@linux_only
def test_the_launcher_passes_the_command_through_as_argv_never_as_code(tmp_path):
    hostile = "x'; import os; os.system('touch pwned'); '"
    result = run_command([sys.executable, "-c", "import sys; print(sys.argv[1])", hostile], tmp_path, dict(os.environ), 30, limits=sandbox.child_limits())
    assert result.output.strip() == hostile and not (tmp_path / "pwned").exists()


@linux_only
def test_a_full_run_still_works_under_the_limits():
    result = run_proof_of_life(snap({"demo/__init__.py": "def get_answer():\n    return 42\n"}), CURRENT)
    assert result.imported and result.error is None and (result.tests_passed, result.tests_failed) == (2, 0)


# --- F6: output and disk are watched on every platform ----------------------------------------------------------


def test_a_flood_of_output_stops_the_process_and_keeps_only_the_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "MAX_OUTPUT_BYTES", 1_000_000)
    code = "import sys\nwhile True:\n    sys.stdout.write('x' * 100000 + '\\n'); sys.stdout.flush()"
    started = time.monotonic()
    result = run_command([sys.executable, "-c", code], tmp_path, dict(os.environ), 60)
    assert result.limit == "output" and result.returncode is None and not result.timed_out
    assert len(result.output) <= sandbox.LOG_TAIL and time.monotonic() - started < 20


def test_filling_the_run_directory_stops_the_process(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "MAX_RUN_DIR_BYTES", 5_000_000)
    monkeypatch.setattr(sandbox, "DIR_CHECK_SECONDS", 0.2)
    code = "import time\ni = 0\nwhile True:\n    open(f'junk{i}.bin', 'wb').write(b'x' * 1_000_000); i += 1; time.sleep(0.05)"
    started = time.monotonic()
    result = run_command([sys.executable, "-c", code], tmp_path, dict(os.environ), 60, watch_dir=tmp_path)
    assert result.limit == "disk" and result.returncode is None
    assert time.monotonic() - started < 20
    assert sum(f.stat().st_size for f in tmp_path.iterdir()) < 50_000_000


def test_a_quiet_well_behaved_process_is_not_stopped(tmp_path):
    result = run_command([sys.executable, "-c", "print('ok')"], tmp_path, dict(os.environ), 30, watch_dir=tmp_path)
    assert result.limit is None and result.returncode == 0 and result.output.strip() == "ok"


def test_the_timeout_is_reported_as_a_limit_too(tmp_path):
    result = run_command([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path, dict(os.environ), 1)
    assert result.limit == "timeout" and result.timed_out


def test_the_directory_scan_stops_counting_once_over_the_cap(tmp_path):
    for i in range(5):
        (tmp_path / f"f{i}").write_bytes(b"x" * 1000)
    assert sandbox._dir_exceeds(tmp_path, 4000) is True
    assert sandbox._dir_exceeds(tmp_path, 5000) is False
    assert sandbox._dir_exceeds(tmp_path / "missing", 1) is False


def test_a_package_that_floods_its_output_gets_an_explained_failure(monkeypatch):
    monkeypatch.setattr(sandbox, "MAX_OUTPUT_BYTES", 2_000_000)
    source = "import sys\nwhile True:\n    sys.stdout.write('x' * 100000 + '\\n'); sys.stdout.flush()\n"
    result = run_proof_of_life(snap({"demo/__init__.py": source}), CURRENT)
    assert not result.imported and "output" in result.error.lower() and "limit" in result.error.lower()


# --- F12: the whole run has one time budget -------------------------------------------------------------------------


def test_every_step_gets_only_the_time_that_is_left():
    now = time.monotonic()
    assert sandbox._step_timeout(180, now + 1000) == 180
    assert 40 < sandbox._step_timeout(180, now + 50) <= 50


def test_a_step_is_refused_when_the_budget_is_spent():
    with pytest.raises(sandbox.OutOfTime):
        sandbox._step_timeout(60, time.monotonic() + 1)
    with pytest.raises(sandbox.OutOfTime):
        sandbox._step_timeout(60, time.monotonic() - 5)


def test_a_run_whose_budget_is_already_spent_stops_before_doing_any_work(monkeypatch):
    monkeypatch.setattr(sandbox, "TOTAL_BUDGET", 0)
    started = time.monotonic()
    result = run_proof_of_life(snap({"demo/__init__.py": "x = 1\n"}), CURRENT)
    assert not result.imported and "time budget" in result.error
    assert time.monotonic() - started < 5, "no virtualenv should have been built"


def test_the_budget_is_below_the_hosts_request_timeout():
    """DEPLOY.md deploys with --timeout 900; the budget has to leave room for the work around it."""
    assert sandbox.TOTAL_BUDGET <= 600


# --- F12: interpreter lookups are cached and bounded ---------------------------------------------------------------------


def test_the_interpreter_is_looked_up_once_per_target(monkeypatch):
    calls: list[str] = []

    def fake(target, env=None):
        calls.append(target)
        return ("/x/python", "3.12.1", True)

    sandbox.clear_interpreter_cache()
    monkeypatch.setattr(sandbox, "find_interpreter", fake)
    assert [sandbox.find_interpreter_cached("3.12") for _ in range(5)] == [("/x/python", "3.12.1", True)] * 5
    assert calls == ["3.12"]
    sandbox.clear_interpreter_cache()


def test_spellings_of_the_same_target_share_one_lookup(monkeypatch):
    calls: list[str] = []
    sandbox.clear_interpreter_cache()
    monkeypatch.setattr(sandbox, "find_interpreter", lambda target, env=None: calls.append(target) or ("/x", "3.12.1", True))
    for spelling in ("3.12", "3.12.4", " 3.12 ", "3.12.0rc1"):
        sandbox.find_interpreter_cached(spelling)
    assert len(calls) == 1
    sandbox.clear_interpreter_cache()


def test_the_cache_cannot_be_grown_without_limit_by_a_client(monkeypatch):
    sandbox.clear_interpreter_cache()
    monkeypatch.setattr(sandbox, "find_interpreter", lambda target, env=None: ("/x", "3.12.1", False))
    for i in range(500):
        sandbox.find_interpreter_cached(f"9.{i}")
        sandbox.find_interpreter_cached(f"junk-{i}")
    assert len(sandbox._interpreters) <= sandbox.MAX_CACHED_INTERPRETERS
    sandbox.clear_interpreter_cache()


@pytest.mark.parametrize("target", ["", "abc", "2.7", "9.99", "3.1", "3.99", "../../x", "3.12; rm -rf /"])
def test_an_implausible_target_never_spawns_a_probe(monkeypatch, target):
    spawned: list = []
    monkeypatch.setattr(sandbox, "_interpreter_version", lambda exe, env: spawned.append(exe) or "3.11.6")
    exe, version, matched = sandbox.find_interpreter(target)
    assert matched is False and exe == sys.executable
    assert len(spawned) <= 1, "only the fallback version probe is allowed"


# --- each mechanism has to hold on its own, not only because another one covers for it ---------------------------------


@linux_only
def test_run_command_alone_kills_a_background_process_left_in_its_group(tmp_path):
    """No run marker in the environment here, so only the process-group kill can stop it."""
    pidfile = tmp_path / "bg.pid"
    code = (
        "import subprocess, sys\n"
        f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
    )
    result = run_command([sys.executable, "-c", code], tmp_path, {"PATH": os.environ.get("PATH", "")}, 30)
    assert result.returncode == 0 and pidfile.exists()
    assert wait_gone(int(pidfile.read_text())), "a background process outlived a command that exited normally"


def test_every_step_is_followed_by_a_sweep_and_runs_with_limits_and_a_watched_directory(monkeypatch):
    commands: list[dict] = []
    sweeps: list[str] = []
    real = sandbox.run_command

    def spy(cmd, cwd, env, timeout, tail=sandbox.LOG_TAIL, **kw):
        commands.append(kw)
        return real(cmd, cwd, env, timeout, tail, **kw)

    sandbox.find_interpreter_cached(CURRENT)  # warm the cache so interpreter probes are not counted as steps
    monkeypatch.setattr(sandbox, "run_command", spy)
    monkeypatch.setattr(sandbox, "reap_run", lambda run_id: sweeps.append(run_id) or 0)
    result = run_proof_of_life(snap({"demo/__init__.py": "x = 1\n"}), CURRENT)
    assert result.imported
    assert len(commands) >= 2, "at least the virtualenv and the import check"
    assert all(kw.get("limits") == sandbox.child_limits() for kw in commands), "every step runs under the resource limits"
    assert all(kw.get("watch_dir") is not None for kw in commands), "every step has its directory watched"
    assert len(sweeps) >= len(commands) + 1, "a sweep after each step, and a last one when the run ends"
    assert len(set(sweeps)) == 1, "one marker per run"


def test_an_implausible_target_does_not_even_look_for_an_interpreter(monkeypatch):
    probed: list[str] = []
    monkeypatch.setattr(sandbox.shutil, "which", lambda name, *a, **k: f"/fake/{name}")
    monkeypatch.setattr(sandbox, "_interpreter_version", lambda exe, env: probed.append(exe) or "3.11.6")
    for target in ("9.99", "2.7", "3.99", "3.1"):
        sandbox.find_interpreter(target)
    assert not [p for p in probed if p.startswith("/fake/")], "an implausible target must not reach any interpreter probe"
