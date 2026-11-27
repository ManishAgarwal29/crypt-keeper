"""Declare third-party imports a package forgot to declare. Pure.

Some old packages import a library they never list as a dependency (`import six`, `import bottle`). It happened to be
installed on the author's machine; in a fresh environment the import fails. This adds the missing requirement.

It only ever adds a distribution named in IMPORT_TO_DIST, a short table of well-known libraries that publish wheels for
Python 3.12. An import name that is not in the table is left alone: guessing a PyPI name from an import name is how a
typo-squatted package gets installed. Names the table knows are dead on 3.12 are reported instead.

Only code that runs at import counts: tests, `setup.py`, docs and examples do not, an import inside
`try/except ImportError` is a deliberate fallback, and a module the package ships itself is never "missing".
"""
from __future__ import annotations

import ast
import sys
import warnings

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from coroner.deprecations import guarded_body_node_ids
from proof.discovery import is_runtime_source

# import name -> the distribution that provides it. Each publishes wheels for Python 3.12.
IMPORT_TO_DIST: dict[str, str] = {
    "six": "six",
    "bottle": "bottle",
    "yaml": "PyYAML",
    "requests": "requests",
    "simplejson": "simplejson",
    "mock": "mock",
    "pytz": "pytz",
    "dateutil": "python-dateutil",
    "docutils": "docutils",
    "jinja2": "Jinja2",
    "markupsafe": "MarkupSafe",
    "flask": "Flask",
    "werkzeug": "Werkzeug",
    "httplib2": "httplib2",
    "paramiko": "paramiko",
    "pymongo": "pymongo",
    "bs4": "beautifulsoup4",
    "PIL": "Pillow",
    "certifi": "certifi",
    "chardet": "chardet",
    "idna": "idna",
    "urllib3": "urllib3",
    "decorator": "decorator",
    "pyparsing": "pyparsing",
    "attr": "attrs",
    "click": "click",
    "tornado": "tornado",
    "lxml": "lxml",
    "numpy": "numpy",
    # added after the fourth benchmark list showed packages importing gevent and twisted without declaring them;
    # each was installed from wheels on Python 3.12 and imported (benchmark/notes.md)
    "gevent": "gevent",
    "twisted": "Twisted",
    "eventlet": "eventlet",
    "greenlet": "greenlet",
    "zmq": "pyzmq",
    "msgpack": "msgpack",
    "ujson": "ujson",
    "markdown": "Markdown",
    "pygments": "Pygments",
    "babel": "Babel",
    "sqlalchemy": "SQLAlchemy",
    "django": "Django",
    "redis": "redis",
    "pika": "pika",
    "jwt": "PyJWT",
    "OpenSSL": "pyOpenSSL",
    "cryptography": "cryptography",
    "boto3": "boto3",
    "psutil": "psutil",
    "tqdm": "tqdm",
    "colorama": "colorama",
    "tabulate": "tabulate",
    "unidecode": "Unidecode",
    "termcolor": "termcolor",
    "xlrd": "xlrd",
    "openpyxl": "openpyxl",
    "cffi": "cffi",
}

# import name -> why declaring it would not help
DEAD_ON_312: dict[str, str] = {
    "nose": "nose does not run on Python 3.10 or later; port the tests to pytest or unittest",
}

_STDLIB = frozenset(sys.stdlib_module_names)


def _local_names(source_map: dict[str, str]) -> set[str]:
    """Every directory and module name the package ships, so a vendored `six.py` is not 'missing'."""
    names: set[str] = set()
    for path in source_map:
        parts = path.split("/")
        names.update(parts[:-1])
        stem = parts[-1].rsplit(".", 1)[0]
        if stem != "__init__":
            names.add(stem)
    return names


def external_imports(source_map: dict[str, str]) -> dict[str, tuple[str, int]]:
    """import root -> (path, line) of its first runtime use, for every non-stdlib module the package does not ship."""
    local = _local_names(source_map)
    found: dict[str, tuple[str, int]] = {}
    for path in sorted(source_map):
        if not is_runtime_source(path):
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tree = ast.parse(source_map[path])
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            continue
        guarded = guarded_body_node_ids(tree)
        for node in ast.walk(tree):
            if id(node) in guarded:
                continue
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [a.name.split(".", 1)[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".", 1)[0]]
            for root in roots:
                if root not in _STDLIB and root not in local:
                    found.setdefault(root, (path, node.lineno))
    return found


def declare_missing(deps: list[str], source_map: dict[str, str]) -> tuple[list[str], list[str], list[str]]:
    """(new dependencies, what was done, what needs a person). Existing requirements are kept, in order."""
    declared: set[str] = set()
    for raw in deps:
        try:
            declared.add(canonicalize_name(Requirement(raw).name))
        except InvalidRequirement:
            continue

    out = list(deps)
    applied: list[str] = []
    manual: list[str] = []
    for root, (path, line) in sorted(external_imports(source_map).items()):
        if root in DEAD_ON_312:
            manual.append(f"{path}:{line} imports `{root}`: {DEAD_ON_312[root]}.")
            continue
        dist = IMPORT_TO_DIST.get(root)
        if dist is None or canonicalize_name(dist) in declared:
            continue
        out.append(dist)
        declared.add(canonicalize_name(dist))
        applied.append(f"declared `{dist}`: {path}:{line} imports `{root}`, which the package does not declare.")
    return out, applied, manual
