"""Tests for the pure proof-of-life helpers (task 10). No subprocesses, no network."""
import json
import os
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from api.sandbox import run_command
from proof.discovery import (
    MAX_MODULES, MAX_SMOKE_CALLS, RUNNER_SCRIPT, ImportPlan, PytestCounts, discover_modules,
    find_shipped_tests, is_test_file, parse_pytest_summary, pick_primary, safe_relative_path,
    smoke_calls, validate_dependencies, zero_arg_functions,
)


# --- safe paths ----------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["pkg/a.py", "a.py", "src/pkg/sub/mod.py", "pkg/données.py", "a/b.c/d.py"])
def test_safe_paths_are_accepted_unchanged(path):
    assert safe_relative_path(path) == path


@pytest.mark.parametrize("path", [
    "", "/etc/passwd", "../x.py", "a/../b.py", "a/./b.py", "a//b.py", "a\\b.py", "C:/x.py", "C:x.py",
    "con.py", "pkg/NUL.txt", "pkg/aux", "a/b.", "a /b.py", "x\x00.py", ".", "..",
])
def test_unsafe_paths_are_rejected(path):
    assert safe_relative_path(path) is None


@given(st.text(max_size=40))
def test_accepted_paths_never_escape(path):
    safe = safe_relative_path(path)
    if safe is not None:
        parts = safe.split("/")
        assert ".." not in parts and "" not in parts and not safe.startswith("/") and "\\" not in safe


# --- module discovery --------------------------------------------------------------------------------


def test_flat_layout_finds_the_package_and_skips_tests_docs_and_setup():
    plan = discover_modules(["pkg/__init__.py", "pkg/mod.py", "pkg/sub/__init__.py", "tests/test_a.py", "setup.py", "docs/conf.py"])
    assert plan.modules == ["pkg"] and plan.roots == [""]
    assert plan.module_files == {"pkg": "pkg/__init__.py"}


def test_src_layout_uses_src_as_the_root():
    plan = discover_modules(["src/pkg/__init__.py", "src/pkg/a.py"])
    assert plan.modules == ["pkg"] and plan.roots == ["src"]


def test_lib_layout_with_capitalised_package():
    plan = discover_modules(["lib/Crypto/__init__.py", "lib/Crypto/Cipher/__init__.py", "setup.py"])
    assert plan.modules == ["Crypto"] and plan.roots == ["lib"]


def test_single_module_distribution():
    plan = discover_modules(["mod.py", "setup.py", "test_mod.py", "conftest.py"])
    assert plan.modules == ["mod"] and plan.roots == [""] and plan.module_files == {"mod": "mod.py"}


def test_packages_come_before_loose_modules_and_share_a_root():
    plan = discover_modules(["src/helper.py", "src/pkg/__init__.py"])
    assert plan.modules == ["pkg", "helper"] and plan.roots == ["src"]


@pytest.mark.parametrize("paths", [
    [], ["README.md"], ["tests/__init__.py", "tests/test_a.py"], ["my-pkg/__init__.py"],
    ["docs/__init__.py"], ["pkg/sub/mod.py"], ["setup.py"], [".hidden/__init__.py"],
])
def test_nothing_importable_found(paths):
    assert discover_modules(paths).modules == []


def test_nested_packages_are_not_top_level():
    assert discover_modules(["pkg/__init__.py", "pkg/sub/__init__.py"]).modules == ["pkg"]


def test_module_count_is_capped():
    paths = [f"p{i:02}/__init__.py" for i in range(MAX_MODULES + 5)]
    assert len(discover_modules(paths).modules) == MAX_MODULES


@given(st.lists(st.text(alphabet="abc/_.-", max_size=14).map(lambda s: s + ".py"), max_size=12))
def test_discovery_never_raises_and_only_returns_identifiers(paths):
    plan = discover_modules(paths)
    assert all(m.isidentifier() for m in plan.modules)
    assert len(plan.modules) <= MAX_MODULES


# --- primary module -------------------------------------------------------------------------------------


@pytest.mark.parametrize("modules,identifier,expected", [
    (["other", "requests"], "requests", "requests"),
    (["a", "My_Repo"], "https://github.com/o/My-Repo.git", "My_Repo"),
    (["a", "b"], "python-memcached", "a"),
    (["yaml"], "PyYAML", "yaml"),
    (["a"], "https://github.com/o/a/", "a"),
    ([], "x", None),
])
def test_pick_primary(modules, identifier, expected):
    assert pick_primary(modules, identifier) == expected


# --- shipped tests ------------------------------------------------------------------------------------------


def test_find_shipped_tests_returns_only_pytest_collectable_files_sorted():
    paths = ["tests/test_a.py", "pkg/test_b.py", "pkg/b_test.py", "tests/helpers.py", "conftest.py", "pkg/core.py"]
    assert find_shipped_tests(paths) == ["pkg/b_test.py", "pkg/test_b.py", "tests/test_a.py"]


@pytest.mark.parametrize("path,expected", [
    ("tests/x.py", True), ("pkg/tests/x.py", True), ("functional_tests/x.py", True), ("pkg/test_x.py", True),
    ("pkg/x_test.py", True), ("conftest.py", True), ("pkg/core.py", False), ("pkg/latest.py", False),
])
def test_is_test_file(path, expected):
    assert is_test_file(path) is expected


# --- smoke test selection ------------------------------------------------------------------------------------


SOURCE = '''
def get_version(): return "1"
def get_needs_arg(a): pass
def get_with_default(a=1): pass
def _get_private(): pass
def main(): pass
def get_and_delete(): pass
@decorated
def get_decorated(): pass
async def get_async(): pass
def is_ok(*args, **kwargs): pass
def version(): pass
def get_kwonly(*, k): pass
def get_kwonly_default(*, k=1): pass
def run_everything(): pass
def list_items(): pass
class C:
    def get_method(self): pass
'''


def test_only_safe_zero_argument_functions_are_selected_in_source_order():
    assert zero_arg_functions(SOURCE) == [
        "get_version", "get_with_default", "is_ok", "version", "get_kwonly_default", "list_items",
    ]


def test_unparseable_source_yields_no_functions():
    assert zero_arg_functions("def get_x(:\n") == []


def test_smoke_calls_are_capped_per_module_and_overall():
    many = "".join(f"def get_{i}(): pass\n" for i in range(10))
    plan = ImportPlan(roots=[""], modules=["a", "b", "c"], module_files={"a": "a.py", "b": "b.py", "c": "c.py"})
    calls = smoke_calls(plan, {"a.py": many, "b.py": many, "c.py": many})
    assert len(calls) == MAX_SMOKE_CALLS
    assert [c["func"] for c in calls[:3]] == ["get_0", "get_1", "get_2"]
    assert {c["module"] for c in calls} == {"a", "b"}


def test_smoke_calls_skip_modules_without_source():
    plan = ImportPlan(roots=[""], modules=["a"], module_files={"a": "a.py"})
    assert smoke_calls(plan, {}) == []


# --- dependencies ---------------------------------------------------------------------------------------------


def test_only_plain_requirements_are_installable():
    valid, rejected = validate_dependencies([
        "requests>=2", "--index-url http://evil.example", "pkg @ https://x.example/p.tar.gz",
        "oops !!", "requests>=2", "numpy; python_version > '3'", "-r other.txt", "./local",
    ])
    assert valid == ["requests>=2", "numpy; python_version > '3'"]
    assert rejected == ["--index-url http://evil.example", "pkg @ https://x.example/p.tar.gz", "oops !!", "-r other.txt", "./local"]


@given(st.lists(st.text(max_size=30), max_size=8))
def test_no_option_or_url_ever_passes_validation(deps):
    valid, _ = validate_dependencies(deps)
    assert all(not d.lstrip().startswith("-") and "@" not in d.split(";")[0] for d in valid)


# --- pytest summary ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("....\n3 passed, 1 failed in 0.12s\n", PytestCounts(3, 1, 0, 0)),
    ("1 error in 0.05s", PytestCounts(0, 0, 1, 0)),
    ("2 errors, 1 passed, 3 skipped in 1.00s", PytestCounts(1, 0, 2, 3)),
    ("========== 5 passed in 1.2s ==========", PytestCounts(5, 0, 0, 0)),
    ("no tests ran in 0.01s", PytestCounts()),
    ("garbage with no timing line", None),
    ("", None),
])
def test_parse_pytest_summary(text, expected):
    assert parse_pytest_summary(text) == expected


@given(st.text(max_size=200))
def test_parse_pytest_summary_never_raises(text):
    result = parse_pytest_summary(text)
    assert result is None or result.passed >= 0


# --- the runner script, executed with the host interpreter (no virtualenv) ------------------------------------------


def run_runner(tmp_path: Path, files: dict[str, str], modules: list[str], calls: list[dict]) -> dict:
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    (tmp_path / "runner.py").write_text(RUNNER_SCRIPT, encoding="utf-8")
    result = tmp_path / "result.json"
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"roots": [str(tmp_path)], "modules": modules, "calls": calls, "result": str(result)}))
    run_command([sys.executable, "-I", "-B", str(tmp_path / "runner.py"), str(cfg)], tmp_path, dict(os.environ), 60)
    return json.loads(result.read_text(encoding="utf-8"))


def test_runner_reports_import_errors_exit_and_call_results(tmp_path):
    out = run_runner(
        tmp_path,
        {
            "good.py": "def get_x():\n    return 1\n",
            "bad.py": "import no_such_module_xyz\n",
            "quits.py": "import sys\nsys.exit(3)\n",
            "boom.py": "def get_boom():\n    raise ValueError('nope')\n",
        },
        ["good", "bad", "quits", "boom"],
        [{"module": "good", "func": "get_x"}, {"module": "boom", "func": "get_boom"}, {"module": "bad", "func": "get_y"}],
    )
    assert out["imports"]["good"] is None and out["imports"]["boom"] is None
    assert out["imports"]["bad"] == "ModuleNotFoundError: No module named 'no_such_module_xyz'"
    assert out["imports"]["quits"] == "SystemExit: 3"
    assert out["calls"] == [
        {"target": "good.get_x", "error": None},
        {"target": "boom.get_boom", "error": "ValueError: nope"},
    ]  # no call is attempted for a module that failed to import
