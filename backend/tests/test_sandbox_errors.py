"""What the sandbox tells a client: no server paths, no raw exceptions. Plus the pure command builders."""
import json
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from api import sandbox
from api.sandbox import run_proof_of_life
from models import PackageRef, PackageSnapshot, Source
from proof.discovery import LIMIT_LAUNCHER, limited_command, pytest_command, redact

CURRENT = "%d.%d" % sys.version_info[:2]


def snap(files: dict[str, str], deps: list[str] | None = None) -> PackageSnapshot:
    return PackageSnapshot(ref=PackageRef(source=Source.pypi, identifier="demo"), source_map=files, dependencies=deps or [])


# --- pure builders ------------------------------------------------------------------------------------------------


def test_pytest_paths_come_after_a_double_dash_so_none_can_be_an_option():
    cmd = pytest_command("py", "/src", ["--rootdir=/etc", "-x", "tests/test_a.py"])
    assert cmd[cmd.index("--") + 1:] == ["--rootdir=/etc", "-x", "tests/test_a.py"]
    assert cmd.count("--") == 1 and "-x" not in cmd[:cmd.index("--")]


def test_pytest_command_keeps_its_safety_options():
    cmd = pytest_command("py", "/src", ["t.py"])
    for needed in ("-B", "no:cacheprovider", "addopts=", "--continue-on-collection-errors"):
        assert needed in cmd


@given(st.text(max_size=40), st.dictionaries(st.integers(1, 9).map(lambda n: "k" * n), st.text(max_size=6), max_size=4))
def test_redact_leaves_no_key_behind_when_labels_do_not_contain_keys(text, mapping):
    labels = {k: "<x>" for k in mapping}
    out = redact(text, labels)
    assert all(k not in out for k in labels)  # keys are runs of "k"; the label "<x>" contains none


def test_redact_replaces_the_longest_path_first():
    out = redact("/tmp/run/home/x", {"/tmp/run": "<sandbox>", "/tmp/run/home": "<home>"})
    assert out == "<home>/x"


def test_redact_ignores_an_empty_key():
    assert redact("abc", {"": "X"}) == "abc"


def test_the_launcher_is_static_and_the_command_travels_as_argv():
    hostile = ["python", "-c", "print(1)'; import os"]
    cmd = limited_command("/usr/bin/python3", hostile, {"RLIMIT_CORE": 0})
    assert cmd[:4] == ["/usr/bin/python3", "-I", "-c", LIMIT_LAUNCHER]
    assert json.loads(cmd[4]) == {"RLIMIT_CORE": 0} and cmd[5:] == hostile
    assert "hostile" not in LIMIT_LAUNCHER and "print(1)" not in LIMIT_LAUNCHER


# --- errors a client sees -------------------------------------------------------------------------------------------


def test_an_unexpected_failure_is_generic_and_logged(monkeypatch, caplog):
    def explode(*_a, **_k):
        raise RuntimeError("secret-path /home/svc/.config/token")

    monkeypatch.setattr(sandbox, "_run", explode)
    with caplog.at_level("ERROR", logger="crypt_keeper.sandbox"):
        result = run_proof_of_life(snap({"demo/__init__.py": "x = 1\n"}), CURRENT)
    assert not result.imported and "secret-path" not in (result.error or "") and "RuntimeError" not in (result.error or "")
    assert "secret-path" in caplog.text, "the details belong in the server log"


def test_a_failed_venv_does_not_show_the_tool_output(monkeypatch, caplog):
    real = sandbox.run_command

    def fake(cmd, cwd, env, timeout, tail=sandbox.LOG_TAIL, **kw):
        if "venv" in cmd:
            return sandbox.RunResult(1, f"Error: Command '{cmd[0]}' failed in {cwd}", False)
        return real(cmd, cwd, env, timeout, tail, **kw)

    monkeypatch.setattr(sandbox, "run_command", fake)
    result = run_proof_of_life(snap({"demo/__init__.py": "x = 1\n"}), CURRENT)
    assert result.error == "Could not create the sandbox virtualenv."
    assert "failed in" not in (result.output or "") and sys.executable not in (result.output or "")


def test_a_write_error_does_not_leak_the_os_message(monkeypatch, tmp_path, caplog):
    def refuse(self, *_a, **_k):
        raise OSError(13, "Permission denied: '/srv/crypt/private/x'")

    monkeypatch.setattr(Path, "write_bytes", refuse)
    with caplog.at_level("WARNING", logger="crypt_keeper.sandbox"):
        problem = sandbox._write_sources(tmp_path, {"a.py": "x"})
    assert problem == "Could not write 'a.py' into the sandbox."
    assert "/srv/crypt/private" in caplog.text


def test_paths_of_the_server_are_removed_from_what_the_client_gets():
    source = (
        "import os, sys\n"
        "raise ImportError('failed at ' + os.path.dirname(os.path.abspath(__file__)) + ' using ' + sys.executable"
        " + ' home ' + os.path.expanduser('~'))\n"
    )
    result = run_proof_of_life(snap({"demo/__init__.py": source}), CURRENT)
    text = json.dumps([result.error, result.output, result.import_results])
    assert "ImportError" in text, "the message itself is kept"
    assert "crypt-keeper-" not in text, "the temp directory name must be redacted"
    assert sys.executable.replace("\\", "\\\\") not in text and sys.executable not in text
    assert "<sandbox>" in text


def test_the_users_home_directory_is_redacted_too(monkeypatch):
    home = str(Path.home())
    source = f"raise ImportError({home!r} + ' is where I looked')\n"
    result = run_proof_of_life(snap({"demo/__init__.py": source}), CURRENT)
    assert "ImportError" in json.dumps(result.import_results) and home not in json.dumps([result.error, result.output, result.import_results])


@pytest.mark.parametrize("limit, word", [("output", "output limit"), ("disk", "disk limit"), ("timeout", "timed out")])
def test_a_stopped_step_is_explained(limit, word):
    msg = sandbox._limit_message(sandbox.RunResult(None, "", limit == "timeout", limit), "Import check", 60)
    assert word in msg


def test_a_step_that_was_not_stopped_has_no_limit_message():
    assert sandbox._limit_message(sandbox.RunResult(0, "", False), "Import check", 60) is None
