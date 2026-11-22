"""Pure helpers for proof of life: what to import, which tests ship, what a smoke test may call.

No I/O and no subprocesses. The sandbox in api/sandbox.py does the running; everything it
needs to decide beforehand is here so it can be tested without a virtualenv.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from posixpath import basename, dirname

from packaging.requirements import InvalidRequirement, Requirement

MAX_MODULES = 10
MAX_SMOKE_CALLS_PER_MODULE = 3
MAX_SMOKE_CALLS = 6
MAX_TEST_FILES = 200

_TEST_DIRS = {"test", "tests", "testing"}
_NON_IMPORT_DIRS = _TEST_DIRS | {"doc", "docs", "example", "examples", "benchmarks", "scripts", "build", "dist"}
_NON_IMPORT_FILES = {"setup.py", "conftest.py", "noxfile.py", "fabfile.py", "manage.py", "__main__.py"}
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}

# Smoke calls are limited to functions that look like read-only getters. Calling arbitrary
# zero-argument functions in untrusted code (`clean()`, `main()`) could do real damage.
_SAFE_PREFIXES = ("get", "is_", "has_", "version", "list", "show", "describe", "info", "current",
                  "default", "available", "supported", "count", "name", "today", "now")
_DANGEROUS_WORDS = ("delete", "remove", "kill", "write", "save", "install", "clear", "reset", "drop",
                    "update", "send", "post", "push", "download", "upload", "exec", "run", "start",
                    "stop", "format", "wipe", "cleanup", "shutdown", "connect")

RESULT_FILENAME = "proof_result.json"
CONFIG_FILENAME = "proof_config.json"

# Runs inside the sandbox interpreter. It is static text; all input arrives through a JSON
# config file, so no untrusted value is ever interpolated into code.
RUNNER_SCRIPT = '''\
import importlib, json, sys

with open(sys.argv[1], encoding="utf-8") as f:
    cfg = json.load(f)
sys.path[:0] = cfg["roots"]

def describe(exc):
    return (type(exc).__name__ + ": " + str(exc))[:500]

results = {"imports": {}, "calls": []}
for name in cfg["modules"]:
    try:
        importlib.import_module(name)
        results["imports"][name] = None
    except BaseException as exc:
        results["imports"][name] = describe(exc)

for call in cfg["calls"]:
    target = call["module"] + "." + call["func"]
    if results["imports"].get(call["module"]) is not None:
        continue
    try:
        getattr(importlib.import_module(call["module"]), call["func"])()
        results["calls"].append({"target": target, "error": None})
    except BaseException as exc:
        results["calls"].append({"target": target, "error": describe(exc)})

with open(cfg["result"], "w", encoding="utf-8") as f:
    json.dump(results, f)
'''


@dataclass(frozen=True)
class ImportPlan:
    roots: list[str] = field(default_factory=list)  # directories (relative to the source root) to put on sys.path
    modules: list[str] = field(default_factory=list)  # importable top-level names
    module_files: dict[str, str] = field(default_factory=dict)  # module -> file that defines it


@dataclass(frozen=True)
class PytestCounts:
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0


def safe_relative_path(path: str) -> str | None:
    """Normalise `path` for writing under a directory, or None if it could escape it."""
    if not path or "\x00" in path or "\\" in path or ":" in path or path.startswith("/"):
        return None
    parts = path.split("/")
    for part in parts:
        if part in ("", ".", "..") or part.endswith((".", " ")):
            return None
        if part.split(".")[0].lower() in _WINDOWS_RESERVED:
            return None
    return "/".join(parts)


def is_test_file(path: str) -> bool:
    name = basename(path)
    parts = path.split("/")[:-1]
    return (
        name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"
        or any(p in _TEST_DIRS or p.endswith(("_tests", "_test")) for p in parts)
    )


def find_shipped_tests(paths: list[str]) -> list[str]:
    """Test files pytest would collect by default (`test_*.py`, `*_test.py`), capped."""
    found = sorted(
        p for p in paths
        if p.endswith(".py") and is_test_file(p)
        and (basename(p).startswith("test_") or basename(p).endswith("_test.py"))
    )
    return found[:MAX_TEST_FILES]


def discover_modules(paths: list[str]) -> ImportPlan:
    """Find the top-level packages and modules the source defines, and the sys.path roots for them.

    A top-level package is a directory with `__init__.py` whose parent is not itself a package.
    Test, docs and example directories are never import targets. Packages come before loose
    modules; the result is capped at MAX_MODULES.
    """
    py_files = [p for p in paths if p.endswith(".py")]
    init_dirs = {dirname(p) for p in py_files if basename(p) == "__init__.py"}

    packages: dict[str, tuple[str, str]] = {}  # name -> (root dir, file)
    for d in sorted(init_dirs):
        if not d or dirname(d) in init_dirs:
            continue
        name = basename(d)
        if not name.isidentifier() or any(part in _NON_IMPORT_DIRS or part.startswith(".") for part in d.split("/")):
            continue
        packages.setdefault(name, (dirname(d), f"{d}/__init__.py"))

    roots = {root for root, _ in packages.values()}
    modules: dict[str, tuple[str, str]] = {}
    for p in sorted(py_files):
        stem = basename(p)[:-3]
        directory = dirname(p)
        in_package = directory in init_dirs or any(directory.startswith(d + "/") for d in init_dirs)
        if (
            in_package
            or stem == "__init__"
            or not stem.isidentifier()
            or basename(p) in _NON_IMPORT_FILES
            or is_test_file(p)
            or any(part in _NON_IMPORT_DIRS for part in directory.split("/") if part)
            or not (directory == "" or directory in roots)
        ):
            continue
        modules.setdefault(stem, (directory, p))

    ordered = [*packages.items(), *((n, v) for n, v in modules.items() if n not in packages)][:MAX_MODULES]
    return ImportPlan(
        roots=sorted({root for _, (root, _) in ordered}),
        modules=[name for name, _ in ordered],
        module_files={name: file for name, (_, file) in ordered},
    )


def pick_primary(modules: list[str], identifier: str) -> str | None:
    """The module most likely to be 'the package': name match first, then first package/module."""
    if not modules:
        return None
    leaf = identifier.strip().rstrip("/").split("/")[-1]
    if leaf.endswith(".git"):
        leaf = leaf[:-4]
    wanted = re.sub(r"[-_.]+", "_", leaf.lower())
    for m in modules:
        if m.lower() == wanted:
            return m
    return modules[0]


def zero_arg_functions(source: str) -> list[str]:
    """Public, undecorated, top-level functions callable with no arguments and with a read-only-looking name."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return []
    found: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.decorator_list or node.name.startswith("_"):
            continue
        a = node.args
        required = len(a.posonlyargs) + len(a.args) - len(a.defaults)
        if required > 0 or any(d is None for d in a.kw_defaults):  # needs an argument we can't supply
            continue
        lowered = node.name.lower()
        if lowered.startswith(_SAFE_PREFIXES) and not any(w in lowered for w in _DANGEROUS_WORDS):
            found.append(node.name)
    return found


def smoke_calls(plan: ImportPlan, source_map: dict[str, str]) -> list[dict[str, str]]:
    """The (module, function) pairs the smoke test will call, in a stable order."""
    calls: list[dict[str, str]] = []
    for module in plan.modules:
        source = source_map.get(plan.module_files.get(module, ""), "")
        for func in zero_arg_functions(source)[:MAX_SMOKE_CALLS_PER_MODULE]:
            calls.append({"module": module, "func": func})
    return calls[:MAX_SMOKE_CALLS]


def validate_dependencies(deps: list[str]) -> tuple[list[str], list[str]]:
    """Split dependencies into (installable, rejected).

    Rejects anything that is not a plain requirement: pip options (`--index-url`), URLs and
    local paths could redirect the install to attacker-chosen code.
    """
    valid: list[str] = []
    rejected: list[str] = []
    for raw in deps:
        try:
            req = Requirement(raw)
        except InvalidRequirement:
            rejected.append(raw)
            continue
        if req.url:
            rejected.append(raw)
        elif raw not in valid:
            valid.append(raw)
    return valid, rejected


def parse_pytest_summary(text: str) -> PytestCounts | None:
    """Read counts from pytest's final `N passed, M failed in 0.12s` line, or None if absent."""
    for line in reversed(text.splitlines()):
        if re.search(r"\bin\s+[\d.]+s\b", line):
            counts = {kind: int(n) for n, kind in re.findall(r"(\d+) (passed|failed|errors?|skipped)", line)}
            return PytestCounts(
                passed=counts.get("passed", 0),
                failed=counts.get("failed", 0),
                errors=counts.get("error", 0) + counts.get("errors", 0),
                skipped=counts.get("skipped", 0),
            )
    return None


# Static launcher: applies resource limits, then replaces itself with the real command. The limits arrive as
# JSON in argv[1] and the command as plain argv, so no untrusted value is ever interpreted as code.
LIMIT_LAUNCHER = '''\
import json, os, resource, sys
for name, value in json.loads(sys.argv[1]).items():
    which = getattr(resource, name)
    hard = resource.getrlimit(which)[1]
    if hard != resource.RLIM_INFINITY and value > hard:
        value = hard
    resource.setrlimit(which, (value, value))
os.execvp(sys.argv[2], sys.argv[2:])
'''


def limited_command(python: str, cmd: list[str], limits: dict[str, int]) -> list[str]:
    """Wrap `cmd` so it starts with the given resource limits (POSIX). `limits` maps RLIMIT_* names to values."""
    return [python, "-I", "-c", LIMIT_LAUNCHER, json.dumps(limits), *cmd]


def pytest_command(python: str, src: str, tests: list[str]) -> list[str]:
    """The pytest invocation. `--` keeps a hostile test path from being read as an option."""
    return [python, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--continue-on-collection-errors",
            "-o", "addopts=", "--rootdir", src, "--", *tests]


def redact(text: str, replacements: dict[str, str]) -> str:
    """Replace each key with its label, longest key first so a path is not half-replaced by its parent."""
    for key in sorted((k for k in replacements if k), key=len, reverse=True):
        text = text.replace(key, replacements[key])
    return text
