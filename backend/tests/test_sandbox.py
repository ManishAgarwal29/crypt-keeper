"""Tests for the proof-of-life sandbox (task 10).

These create real virtualenvs and run real subprocesses, but need no network: the packages
under test have no dependencies. The test that installs pytest from PyPI is opt-in
(set CRYPT_KEEPER_NETWORK_TESTS=1).
"""
import os
import sys
import time
from pathlib import Path

import pytest

from api import sandbox
from api.sandbox import _env, _write_sources, find_interpreter, run_command, run_proof_of_life
from models import PackageRef, PackageSnapshot, Source

CURRENT = "%d.%d" % sys.version_info[:2]


def snap(files: dict[str, str], deps: list[str] | None = None, name: str = "demo") -> PackageSnapshot:
    return PackageSnapshot(
        ref=PackageRef(source=Source.pypi, identifier=name), dependencies=deps or [], source_map=files,
    )


# --- run_command -----------------------------------------------------------------------------------------


def test_run_command_captures_output_and_exit_code(tmp_path):
    r = run_command([sys.executable, "-c", "print('hi'); raise SystemExit(4)"], tmp_path, dict(os.environ), 30)
    assert r.returncode == 4 and "hi" in r.output and not r.timed_out


def test_run_command_kills_a_runaway_process_and_returns_promptly(tmp_path):
    started = time.monotonic()
    r = run_command([sys.executable, "-c", "import time; time.sleep(120)"], tmp_path, dict(os.environ), 2)
    assert r.timed_out and r.returncode is None
    assert time.monotonic() - started < 30


def test_run_command_kills_child_processes_too(tmp_path):
    marker = tmp_path / "survivor.txt"
    code = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', \"import time; time.sleep(6); open(r'{marker}', 'w').write('x')\"])\n"
        "time.sleep(120)\n"
    )
    run_command([sys.executable, "-c", code], tmp_path, dict(os.environ), 2)
    time.sleep(8)
    assert not marker.exists(), "the grandchild process should have been killed with its parent"


def test_run_command_keeps_only_the_tail_of_huge_output(tmp_path):
    r = run_command([sys.executable, "-c", "print('a' * 200000); print('THE-END')"], tmp_path, dict(os.environ), 30, tail=500)
    assert len(r.output) <= 500 and r.output.rstrip().endswith("THE-END")


def test_run_command_reports_a_missing_executable(tmp_path):
    r = run_command(["definitely-not-a-real-program-xyz"], tmp_path, dict(os.environ), 5)
    assert r.returncode is None and not r.timed_out and "Could not start" in r.output


# --- environment ------------------------------------------------------------------------------------------------


def test_environment_is_scrubbed_of_tokens_and_user_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example")
    env = _env(tmp_path)
    assert "GITHUB_TOKEN" not in env and "AWS_SECRET_ACCESS_KEY" not in env and "HTTPS_PROXY" not in env
    assert env["HOME"] == str(tmp_path) and env["USERPROFILE"] == str(tmp_path)
    assert _env(tmp_path, pip=True)["HTTPS_PROXY"] == "http://proxy.example"
    assert "GITHUB_TOKEN" not in _env(tmp_path, pip=True)


def test_pythonpath_is_only_set_when_asked(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/host/secret/path")
    assert "PYTHONPATH" not in _env(tmp_path)
    assert _env(tmp_path, pythonpath="/x")["PYTHONPATH"] == "/x"


# --- interpreter lookup --------------------------------------------------------------------------------------------


def test_find_interpreter_matches_the_running_python():
    exe, version, matched = find_interpreter(CURRENT)
    assert matched and version.startswith(CURRENT + ".")
    assert Path(exe).is_file()


@pytest.mark.parametrize("target", ["3.99", "bad", "", "2.7"])
def test_find_interpreter_falls_back_and_says_so(target):
    exe, version, matched = find_interpreter(target)
    assert not matched and exe == sys.executable and version.startswith(CURRENT + ".")


# --- writing sources ------------------------------------------------------------------------------------------------


def test_write_sources_writes_nested_files_exactly(tmp_path):
    assert _write_sources(tmp_path, {"pkg/__init__.py": "x = 'é'\r\n", "pkg/sub/m.py": ""}) is None
    assert (tmp_path / "pkg/__init__.py").read_bytes() == "x = 'é'\r\n".encode("utf-8")
    assert (tmp_path / "pkg/sub/m.py").exists()


@pytest.mark.parametrize("path", ["../escape.py", "/abs.py", "a/../../b.py", "C:/x.py", "a\\b.py"])
def test_write_sources_refuses_unsafe_paths_and_writes_nothing(tmp_path, path):
    target = tmp_path / "root"
    target.mkdir()
    message = _write_sources(target, {path: "x"})
    assert message is not None and "unsafe path" in message.lower()
    assert not (tmp_path / "escape.py").exists() and not (tmp_path / "b.py").exists()


def test_write_sources_enforces_size_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "MAX_FILES", 2)
    assert "Too many" in _write_sources(tmp_path, {"a.py": "", "b.py": "", "c.py": ""})
    monkeypatch.setattr(sandbox, "MAX_TOTAL_BYTES", 5)
    assert "too large" in _write_sources(tmp_path, {"a.py": "123456"})


def test_write_sources_reports_conflicting_paths(tmp_path):
    assert "Could not write" in _write_sources(tmp_path, {"a": "x", "a/b.py": "y"})


# --- run_proof_of_life: cases that never create a virtualenv ----------------------------------------------------------


def test_disabled_by_environment(monkeypatch):
    monkeypatch.setenv(sandbox.ENABLE_ENV, "off")
    r = run_proof_of_life(snap({"demo/__init__.py": ""}), CURRENT)
    assert not r.imported and "disabled" in r.error


def test_nothing_to_import_is_reported():
    r = run_proof_of_life(snap({"README.md": "hi", "setup.py": ""}), CURRENT)
    assert not r.imported and "No importable" in r.error


def test_unsafe_paths_are_refused_before_anything_runs():
    r = run_proof_of_life(snap({"demo/__init__.py": "", "../evil.py": "x"}), CURRENT)
    assert not r.imported and "unsafe path" in r.error.lower()


# --- run_proof_of_life: real virtualenvs --------------------------------------------------------------------------------


def test_healthy_package_is_alive_with_a_passing_smoke_test():
    files = {"demo/__init__.py": "def get_answer():\n    return 42\n\ndef delete_everything():\n    raise RuntimeError\n"}
    r = run_proof_of_life(snap(files), CURRENT)
    assert r.imported and r.error is None
    assert r.test_source == "smoke" and (r.tests_passed, r.tests_failed) == (2, 0)  # the import and get_answer()
    assert r.import_results == {"demo": None}
    assert r.target_python_matched and r.python_used.startswith(CURRENT + ".")
    assert r.duration_seconds > 0


def test_broken_import_reports_the_exact_error():
    r = run_proof_of_life(snap({"demo/__init__.py": "import no_such_module_xyz\n"}), CURRENT)
    assert not r.imported
    assert r.error == "ModuleNotFoundError: No module named 'no_such_module_xyz'"
    assert r.tests_passed == 0 and r.tests_failed == 1


def test_hanging_import_times_out_instead_of_hanging(monkeypatch):
    monkeypatch.setattr(sandbox, "IMPORT_TIMEOUT", 3)
    r = run_proof_of_life(snap({"demo/__init__.py": "while True:\n    pass\n"}), CURRENT)
    assert not r.imported and "timed out" in r.error


def test_wrong_target_python_is_flagged_and_unsafe_dependencies_are_skipped():
    files = {"demo/__init__.py": "def get_answer():\n    return 1\n"}
    r = run_proof_of_life(snap(files, ["--index-url http://evil.example", "pkg @ https://x.example/p.tar.gz"]), "3.99")
    assert r.imported and not r.target_python_matched
    assert "does not prove" in r.output and "not plain requirements" in r.output


def test_failing_smoke_call_is_counted_as_a_failure():
    files = {"demo/__init__.py": "def get_config():\n    raise KeyError('missing')\n"}
    r = run_proof_of_life(snap(files), CURRENT)
    assert r.imported and (r.tests_passed, r.tests_failed) == (1, 1)
    assert "get_config() failed: KeyError" in r.output


@pytest.mark.skipif(not os.environ.get("CRYPT_KEEPER_NETWORK_TESTS"), reason="installs pytest from PyPI")
def test_shipped_tests_run_under_pytest_and_are_counted():
    files = {
        "demo/__init__.py": "def add(a, b):\n    return a + b\n",
        "tests/test_demo.py": "from demo import add\n\ndef test_ok():\n    assert add(1, 2) == 3\n\ndef test_bad():\n    assert add(1, 1) == 3\n",
    }
    r = run_proof_of_life(snap(files), CURRENT)
    assert r.imported and r.test_source == "shipped" and (r.tests_passed, r.tests_failed) == (1, 1)
