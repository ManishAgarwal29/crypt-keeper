"""Building a dependency from source: opt-in, local only, and visible in the log."""
from pathlib import Path

import pytest

from api import sandbox
from api.sandbox import RunResult, _install, source_builds_allowed


@pytest.mark.parametrize("value, public, allowed", [
    (None, None, False),
    ("", None, False),
    ("1", None, True),
    ("true", None, True),
    ("YES", None, True),
    ("on", None, True),
    ("0", None, False),
    ("enabled", None, False),        # anything not explicitly on is off
    ("1", "1", False),               # never on a public host
    ("1", "", False),                # a blank public flag counts as public
    ("1", "garbage", False),
    ("1", "off", True),
])
def test_the_switch(monkeypatch, value, public, allowed):
    for name, v in ((sandbox.SOURCE_BUILDS_ENV, value), ("CRYPT_KEEPER_PUBLIC", public)):
        if v is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, v)
    assert source_builds_allowed() is allowed


class Pip:
    """A fake pip: a requirement in `no_wheel` installs only without --only-binary."""

    def __init__(self, no_wheel=(), broken=()):
        self.no_wheel, self.broken, self.calls = set(no_wheel), set(broken), []

    def __call__(self, cmd, cwd, env, timeout):
        self.calls.append(cmd)
        reqs = cmd[cmd.index("--") + 1:]
        wheel_only = "--only-binary=:all:" in cmd
        ok = all(r not in self.broken and (not wheel_only or r not in self.no_wheel) for r in reqs)
        return RunResult(0 if ok else 1, "", False)


def install(pip, reqs):
    log: list[str] = []
    failed = _install("py", reqs, Path("."), Path("."), log, deadline=10**12, step=pip)
    return failed, log


def test_by_default_a_dependency_without_a_wheel_is_not_built(monkeypatch):
    pip = Pip(no_wheel={"cbuild"})
    failed, log = install(pip, ["six", "cbuild"])
    assert failed == ["cbuild"]
    assert all("--only-binary=:all:" in c for c in pip.calls), "no command may build from source unless it is switched on"


def test_switched_on_locally_it_is_built_and_the_log_says_its_setup_py_ran(monkeypatch):
    monkeypatch.setenv(sandbox.SOURCE_BUILDS_ENV, "1")
    pip = Pip(no_wheel={"cbuild"})
    failed, log = install(pip, ["six", "cbuild"])
    assert failed == []
    source_runs = [c for c in pip.calls if "--only-binary=:all:" not in c]
    assert source_runs == [["py", "-m", "pip", "install", "--prefer-binary", "--no-input", "--disable-pip-version-check", "--quiet", "--", "cbuild"]]
    assert "setup.py ran" in log[-1] and "cbuild" in log[-1]


def test_only_the_failed_ones_are_built_from_source(monkeypatch):
    monkeypatch.setenv(sandbox.SOURCE_BUILDS_ENV, "1")
    pip = Pip(no_wheel={"cbuild"})
    install(pip, ["six", "attrs", "cbuild"])
    assert [c[-1] for c in pip.calls if "--prefer-binary" in c] == ["cbuild"]


def test_one_that_fails_from_source_too_is_still_reported(monkeypatch):
    monkeypatch.setenv(sandbox.SOURCE_BUILDS_ENV, "1")
    failed, log = install(Pip(no_wheel={"cbuild"}, broken={"broken"}), ["cbuild", "broken"])
    assert failed == ["broken"] and "still not installed: ['broken']" in log[-1]


def test_on_a_public_host_nothing_is_built_even_if_switched_on(monkeypatch):
    monkeypatch.setenv(sandbox.SOURCE_BUILDS_ENV, "1")
    monkeypatch.setenv("CRYPT_KEEPER_PUBLIC", "1")
    pip = Pip(no_wheel={"cbuild"})
    assert install(pip, ["cbuild"])[0] == ["cbuild"]
    assert all("--only-binary=:all:" in c for c in pip.calls)


def test_a_requirement_is_still_never_read_as_an_option(monkeypatch):
    monkeypatch.setenv(sandbox.SOURCE_BUILDS_ENV, "1")
    pip = Pip(no_wheel={"-e."})
    install(pip, ["-e."])
    for cmd in pip.calls:
        assert cmd.index("--") < cmd.index("-e."), "`--` must come before the requirements in every pip command"


def test_running_out_of_time_stops_the_source_builds(monkeypatch):
    monkeypatch.setenv(sandbox.SOURCE_BUILDS_ENV, "1")
    pip = Pip(no_wheel={"a", "b"})

    def step(cmd, cwd, env, timeout):
        if "--prefer-binary" in cmd:
            raise sandbox.OutOfTime
        return pip(cmd, cwd, env, timeout)

    log: list[str] = []
    assert _install("py", ["a", "b"], Path("."), Path("."), log, deadline=10**12, step=step) == ["a", "b"]
