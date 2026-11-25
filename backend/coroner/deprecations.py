"""Known removed APIs and an AST scanner that finds code using them.

Pure: no network, no I/O. Every table entry records the Python version that removed the
API, so a finding is only raised when the target Python is at or past that version.

Facts come from the CPython "What's New" pages for 3.9 through 3.13 and the Python 2 to 3
module renames.

Known limit: imports guarded by `if sys.version_info ...` are not evaluated, so
version-switched code can still produce findings. `try/except ImportError` guards are
understood and skipped.
"""
from __future__ import annotations

import ast
import warnings
from dataclasses import dataclass

from models import Finding

Version = tuple[int, int]
PY3: Version = (3, 0)


@dataclass(frozen=True)
class Removal:
    removed_in: Version
    advice: str


def _py2(advice: str) -> Removal:
    return Removal(PY3, advice)


# --- removed modules -------------------------------------------------------------

REMOVED_MODULES: dict[str, Removal] = {
    # Python 2 only module names
    "urllib2": _py2("use urllib.request"),
    "urlparse": _py2("use urllib.parse"),
    "ConfigParser": _py2("use configparser"),
    "StringIO": _py2("use io.StringIO"),
    "cStringIO": _py2("use io.StringIO"),
    "cPickle": _py2("use pickle"),
    "Queue": _py2("use queue"),
    "Tkinter": _py2("use tkinter"),
    "httplib": _py2("use http.client"),
    "HTMLParser": _py2("use html.parser"),
    "cookielib": _py2("use http.cookiejar"),
    "xmlrpclib": _py2("use xmlrpc.client"),
    "SocketServer": _py2("use socketserver"),
    "commands": _py2("use subprocess"),
    "__builtin__": _py2("use builtins"),
    "copy_reg": _py2("use copyreg"),
    "md5": _py2("use hashlib"),
    "sha": _py2("use hashlib"),
    # removed from the standard library in 3.x
    "xml.etree.cElementTree": Removal((3, 9), "use xml.etree.ElementTree"),
    "imp": Removal((3, 12), "use importlib"),
    "distutils": Removal((3, 12), "use setuptools or packaging"),
    "asyncore": Removal((3, 12), "use asyncio"),
    "asynchat": Removal((3, 12), "use asyncio"),
    "smtpd": Removal((3, 12), "use aiosmtpd"),
    "lib2to3": Removal((3, 13), "no replacement in the standard library"),
    "tkinter.tix": Removal((3, 13), "use tkinter.ttk"),
}

# PEP 594 "dead batteries", removed in 3.13.
for _name in (
    "aifc", "audioop", "cgi", "cgitb", "chunk", "crypt", "imghdr", "mailcap", "msilib",
    "nis", "nntplib", "ossaudiodev", "pipes", "sndhdr", "spwd", "sunau", "telnetlib",
    "uu", "xdrlib",
):
    REMOVED_MODULES[_name] = Removal(
        (3, 13), "PEP 594: find a maintained alternative or a PyPI redistribution"
    )

# --- removed attributes, keyed by dotted path -------------------------------------

REMOVED_ATTRS: dict[str, Removal] = {
    "asyncio.coroutine": Removal((3, 11), "use async def"),
    "inspect.getargspec": Removal((3, 11), "use inspect.getfullargspec or inspect.signature"),
    "configparser.SafeConfigParser": Removal((3, 12), "use configparser.ConfigParser"),
    "ssl.wrap_socket": Removal((3, 12), "use ssl.SSLContext.wrap_socket"),
    "ssl.match_hostname": Removal((3, 12), "use ssl.SSLContext with check_hostname"),
    "locale.format": Removal((3, 12), "use locale.format_string"),
    "locale.resetlocale": Removal((3, 13), "use locale.setlocale(locale.LC_ALL, '')"),
    "unittest.makeSuite": Removal((3, 13), "use unittest.TestLoader.loadTestsFromTestCase"),
    "unittest.findTestCases": Removal((3, 13), "use unittest.TestLoader.loadTestsFromModule"),
    "unittest.getTestCaseNames": Removal((3, 13), "use unittest.TestLoader.getTestCaseNames"),
    "fractions.gcd": Removal((3, 9), "use math.gcd"),
    "base64.encodestring": Removal((3, 9), "use base64.encodebytes"),
    "base64.decodestring": Removal((3, 9), "use base64.decodebytes"),
    "time.clock": Removal((3, 8), "use time.perf_counter or time.process_time"),
    "platform.linux_distribution": Removal((3, 8), "use the distro package"),
    "platform.dist": Removal((3, 8), "use the distro package"),
    "os.getcwdu": _py2("use os.getcwd"),
    "string.letters": _py2("use string.ascii_letters"),
    "string.lowercase": _py2("use string.ascii_lowercase"),
    "string.uppercase": _py2("use string.ascii_uppercase"),
    "string.maketrans": _py2("use str.maketrans"),
    "itertools.izip": _py2("use the built-in zip"),
    "itertools.imap": _py2("use the built-in map"),
    "itertools.ifilter": _py2("use the built-in filter"),
    "itertools.izip_longest": _py2("use itertools.zip_longest"),
    "sys.maxint": _py2("use sys.maxsize"),
}

# ABC aliases removed from the collections namespace in 3.10.
for _name in (
    "Mapping", "MutableMapping", "Sequence", "MutableSequence", "Set", "MutableSet",
    "Iterable", "Iterator", "Callable", "Hashable", "Sized", "Container", "KeysView",
    "ItemsView", "ValuesView", "MappingView", "ByteString",
):
    REMOVED_ATTRS[f"collections.{_name}"] = Removal((3, 10), f"use collections.abc.{_name}")

# unittest.TestCase aliases removed in 3.12.
REMOVED_TESTCASE_METHODS: dict[str, Removal] = {
    old: Removal((3, 12), f"use {new}")
    for old, new in {
        "failUnless": "assertTrue",
        "failIf": "assertFalse",
        "failUnlessEqual": "assertEqual",
        "failIfEqual": "assertNotEqual",
        "failUnlessAlmostEqual": "assertAlmostEqual",
        "failIfAlmostEqual": "assertNotAlmostEqual",
        "failUnlessRaises": "assertRaises",
        "assert_": "assertTrue",
        "assertEquals": "assertEqual",
        "assertNotEquals": "assertNotEqual",
        "assertAlmostEquals": "assertAlmostEqual",
        "assertNotAlmostEquals": "assertNotAlmostEqual",
        "assertRegexpMatches": "assertRegex",
        "assertNotRegexpMatches": "assertNotRegex",
        "assertRaisesRegexp": "assertRaisesRegex",
    }.items()
}
REMOVED_TESTCASE_METHODS["assertDictContainsSubset"] = Removal(
    (3, 12), "compare with assertEqual, or check items() with <="
)

_GUARD_EXCEPTIONS = {"ImportError", "ModuleNotFoundError", "AttributeError", "Exception", "BaseException"}
_TRY_TYPES = tuple(t for t in (ast.Try, getattr(ast, "TryStar", None)) if t is not None)


def parse_target(target_python: str) -> Version:
    """'3.12' -> (3, 12). An unparseable target is treated as newer than every table entry."""
    try:
        major, minor, *_ = target_python.strip().split(".")
        return int(major), int(minor)
    except ValueError:
        return (99, 0)


def scan_source(path: str, source: str, target_python: str) -> list[Finding]:
    """Find uses of APIs removed at or before `target_python` in one source file."""
    target = parse_target(target_python)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        line = exc.lineno or 1
        return [Finding(
            kind="syntax-error",
            severity="high",
            message=f"Does not parse as Python 3 (line {line}: {exc.msg}); likely Python 2 code.",
            location=f"{path}:{line}",
        )]
    except (ValueError, RecursionError, MemoryError):
        return [Finding(
            kind="syntax-error", severity="high",
            message="Could not be parsed as Python source.", location=path,
        )]

    guarded = guarded_node_ids(tree)
    aliases = import_aliases(tree)
    uses_unittest = any(v == "unittest" or v.startswith("unittest.") for v in aliases.values())

    findings: list[Finding] = []
    seen: set[tuple[int, str]] = set()

    def emit(node: ast.AST, name: str, removal: Removal) -> None:
        line = getattr(node, "lineno", 1)
        if target < removal.removed_in or (line, name) in seen:
            return
        seen.add((line, name))
        if removal.removed_in == PY3:
            what = f"`{name}` is Python 2 only; {removal.advice}."
        else:
            what = f"`{name}` was removed in Python {removal.removed_in[0]}.{removal.removed_in[1]}; {removal.advice}."
        findings.append(Finding(
            kind="deprecated-api", severity="high", message=what, location=f"{path}:{line}",
        ))

    for node in ast.walk(tree):
        if id(node) in guarded:
            continue

        if isinstance(node, ast.Import):
            for alias in node.names:
                hit = _module_removal(alias.name)
                if hit:
                    emit(node, *hit)

        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            hit = _module_removal(node.module)
            if hit:
                emit(node, *hit)
            for alias in node.names:
                dotted = f"{node.module}.{alias.name}"
                hit = _module_removal(dotted)
                if hit:
                    emit(node, *hit)
                if dotted in REMOVED_ATTRS:
                    emit(node, dotted, REMOVED_ATTRS[dotted])

        elif isinstance(node, ast.Attribute):
            dotted = resolve_attribute(node, aliases)
            if dotted in REMOVED_ATTRS:
                emit(node, dotted, REMOVED_ATTRS[dotted])
            elif (
                uses_unittest
                and node.attr in REMOVED_TESTCASE_METHODS
                and isinstance(node.value, ast.Name)
                and node.value.id in ("self", "cls")
            ):
                emit(node, f"TestCase.{node.attr}", REMOVED_TESTCASE_METHODS[node.attr])

    return findings


def _module_removal(dotted: str) -> tuple[str, Removal] | None:
    parts = dotted.split(".")
    for i in range(len(parts), 0, -1):
        key = ".".join(parts[:i])
        if key in REMOVED_MODULES:
            return key, REMOVED_MODULES[key]
    return None


def import_aliases(tree: ast.AST) -> dict[str, str]:
    """Map each locally bound import name to the dotted path it refers to."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    aliases[a.asname] = a.name
                else:
                    root = a.name.split(".")[0]
                    aliases[root] = root
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for a in node.names:
                if a.name != "*":
                    aliases[a.asname or a.name] = f"{node.module}.{a.name}"
    return aliases


def resolve_attribute(node: ast.Attribute, aliases: dict[str, str]) -> str | None:
    parts = [node.attr]
    cur = node.value
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    root = aliases.get(cur.id)
    if root is None:
        return None
    return ".".join([root, *reversed(parts)])


def guarded_node_ids(tree: ast.AST) -> set[int]:
    """Ids of every node inside a try block (body and handlers) that catches import errors.

    Compatibility code such as `try: import urllib2 except ImportError: import urllib.request`
    is deliberate, so it should not be reported as dead.
    """
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, _TRY_TYPES) and any(_catches_import_errors(h) for h in node.handlers):
            for stmt in [*node.body, *(s for h in node.handlers for s in h.body)]:
                guarded.update(id(n) for n in ast.walk(stmt))
    return guarded


def guarded_body_node_ids(tree: ast.AST) -> set[int]:
    """Ids of every node in the *body* of a try that catches import errors, not in its handlers.

    The body is what is allowed to fail; the handler is what runs when it does, so an import in a handler is real code
    (`try: import pyutil ... except ImportError: from distutils.version import LooseVersion`).
    """
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, _TRY_TYPES) and any(_catches_import_errors(h) for h in node.handlers):
            for stmt in node.body:
                guarded.update(id(n) for n in ast.walk(stmt))
    return guarded


def _catches_import_errors(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    for t in types:
        name = t.id if isinstance(t, ast.Name) else t.attr if isinstance(t, ast.Attribute) else None
        if name in _GUARD_EXCEPTIONS:
            return True
    return False
