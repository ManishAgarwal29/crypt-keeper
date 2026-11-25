"""The Exhumer: digs a package up from PyPI or GitHub into a PackageSnapshot.

This is the only networked code in the pipeline. Parsing and archive extraction are pure
functions so they can be tested without a network; the HTTP client is injected so tests
can supply httpx.MockTransport. The MCP server reuses these functions.
"""
from __future__ import annotations

import configparser
import io
import os
import ast
import re
import tarfile
import tokenize
import tomllib
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from models import DependencyInfo, PackageRef, PackageSnapshot, Source
from proof.policy import is_public

PYPI_JSON_URL = "https://pypi.org/pypi/{name}/json"
GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"

MAX_PY_FILES = 1500
MAX_GITHUB_FILES = 200  # GitHub files cost one request each
MAX_FILE_BYTES = 200_000
MAX_ARCHIVE_BYTES = 20_000_000  # compressed download
MAX_ARCHIVE_ENTRIES = 20_000  # headers in one archive
MAX_ARCHIVE_UNPACKED = 250_000_000  # sum of the sizes the headers claim
MAX_ZIP_DIRECTORY = 8_000_000  # bytes of zip central directory
MAX_DEP_LOOKUPS = 25

_METADATA_NAMES = {"requires.txt", "PKG-INFO", "pyproject.toml", "setup.cfg", "setup.py"}
_PYPI_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_GITHUB_URL_RE = re.compile(
    r"https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?"
)
_PY_CLASSIFIER_RE = re.compile(r"^Programming Language :: Python :: (\d+\.\d+)$")


class ExhumeError(Exception):
    """A failure the user should hear about. `status` maps to an HTTP status code."""

    def __init__(self, message: str, status: int = 404) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class PyPIMetadata:
    name: str
    version: str
    last_release_date: str | None
    declared_pythons: list[str]
    dependencies: list[str]
    yanked_versions: list[str]
    requires_python: str | None
    sdist_url: str | None = None


# --- pure parsing ---------------------------------------------------------------


def parse_pypi_metadata(data: dict) -> PyPIMetadata:
    """Reduce a PyPI JSON API payload to the facts the Coroner needs."""
    info = data.get("info") or {}
    releases = data.get("releases") or {}
    version = info.get("version") or ""
    files = data.get("urls") or releases.get(version) or []

    last_release = _latest_upload_date(files)
    if last_release is None:
        last_release = _latest_upload_date(
            [f for fs in releases.values() for f in (fs or [])]
        )

    yanked = sorted(
        v for v, fs in releases.items() if fs and all(f.get("yanked") for f in fs)
    )

    pythons: list[str] = []
    for classifier in info.get("classifiers") or []:
        m = _PY_CLASSIFIER_RE.match(classifier)
        if m:
            pythons.append(m.group(1))

    deps = [d for d in (info.get("requires_dist") or []) if not _is_extra_dependency(d)]

    sdist_url = next(
        (f.get("url") for f in files if f.get("packagetype") == "sdist" and f.get("url")),
        None,
    )

    return PyPIMetadata(
        name=info.get("name") or "",
        version=version,
        last_release_date=last_release,
        declared_pythons=pythons,
        dependencies=deps,
        yanked_versions=yanked,
        requires_python=info.get("requires_python") or None,
        sdist_url=sdist_url,
    )


def python_supported(requires_python: str | None, target: str) -> bool | None:
    """True/False if `requires_python` allows `target`; None if unknown or unparseable."""
    if not requires_python:
        return None
    try:
        return SpecifierSet(requires_python).contains(Version(target), prereleases=True)
    except (InvalidSpecifier, InvalidVersion):
        return None


def parse_requirements_txt(text: str) -> list[str]:
    """Extract requirement lines from a requirements.txt, skipping comments and options."""
    deps: list[str] = []
    for line in text.splitlines():
        line = line.split(" #")[0].strip()
        if not line or line.startswith(("#", "-")):
            continue
        deps.append(line)
    return deps


@dataclass
class Archive:
    py_files: dict[str, str]
    metadata: dict[str, str]  # packaging files that can name the dependencies
    truncated: bool  # some .py files were left out (file count, size, encoding or name clashes)


def extract_py_files(content: bytes, filename: str) -> dict[str, str]:
    """The .py files of an sdist/zip archive. See `extract_archive` for limits and metadata."""
    return extract_archive(content, filename).py_files


def extract_archive(content: bytes, filename: str) -> Archive:
    """Pull .py files and dependency metadata out of an sdist/zip archive held in memory.

    Nothing is written to disk. Entries with absolute or `..` paths are skipped. The top-level
    directory is stripped only when every entry sits under the same one (an sdist); a wheel or an
    archive with several top-level entries keeps its paths. File count and size are capped.
    `truncated` is True when a .py file was left out (a cap, a file that is not UTF-8, or a name
    clash), so callers know the result is incomplete.
    """
    py: list[tuple[str, Callable[[], bytes]]] = []
    meta: list[tuple[str, Callable[[], bytes]]] = []
    oversize = 0
    tops: set[str] = set()
    root_file = False

    def classify(raw_name: str, size: int, read: Callable[[], bytes]) -> None:
        nonlocal oversize, root_file
        path = _clean_member_path(raw_name)
        if not path:
            return
        parts = path.split("/")
        if len(parts) == 1:
            root_file = True
        elif len(tops) < 2:
            tops.add(parts[0])
        if path.endswith(".py"):
            if size <= MAX_FILE_BYTES:
                py.append((path, read))
            else:
                oversize += 1
        if parts[-1] in _METADATA_NAMES and size <= MAX_FILE_BYTES:  # setup.py is both source and metadata
            meta.append((path, read))

    def finish() -> Archive:
        return _build_archive(py, meta, oversize, strip=len(tops) == 1 and not root_file)

    try:
        if filename.endswith((".zip", ".whl")):
            _check_zip_directory(content)  # before ZipFile parses (and allocates for) every entry
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                unpacked = 0
                for entries, zi in enumerate(zf.infolist(), start=1):
                    _check_archive_limits(entries, unpacked := unpacked + zi.file_size)
                    if not zi.is_dir():
                        classify(zi.filename, zi.file_size, lambda zi=zi: zf.read(zi))
                return finish()
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as tf:
            # Walk the headers one at a time. Each header states its size, so a hostile entry is refused
            # *before* its data is decompressed. `tf.getmembers()` would decompress and index everything.
            entries = unpacked = 0
            while (m := tf.next()) is not None:
                entries += 1
                unpacked += m.size if m.isreg() else 0
                _check_archive_limits(entries, unpacked)
                if m.isfile():
                    classify(m.name, m.size, lambda m=m: (tf.extractfile(m) or io.BytesIO()).read())
                tf.members = []  # TarFile remembers every header it reads; do not let that grow with the archive
            return finish()
    except (tarfile.TarError, zipfile.BadZipFile, EOFError, OSError) as exc:
        raise ExhumeError(
            f"The coffin is rotten: the source archive could not be opened ({exc.__class__.__name__}).",
            422,
        ) from exc


def _too_big(what: str) -> ExhumeError:
    return ExhumeError(f"The corpse is too large to exhume ({what}).", 413)


def _check_archive_limits(entries: int, unpacked: int) -> None:
    """Refuse an archive that is a decompression bomb. The compressed-size cap alone does not stop one:
    a few megabytes can hold millions of empty entries, or one file that expands to gigabytes."""
    if entries > MAX_ARCHIVE_ENTRIES:
        raise _too_big(f"more than {MAX_ARCHIVE_ENTRIES:,} entries")
    if unpacked > MAX_ARCHIVE_UNPACKED:
        raise _too_big(f"more than {MAX_ARCHIVE_UNPACKED // 1_000_000} MB unpacked")


def zip_directory_summary(content: bytes) -> tuple[int, int] | None:
    """(entry count, central directory size) from the zip's end-of-central-directory record, or None.

    Read straight from the last bytes, before `zipfile` builds an object for every entry. A count of
    0xFFFF means "see the zip64 record" and is far above our limit anyway.
    """
    tail = content[-(65_535 + 22):]
    i = tail.rfind(b"PK\x05\x06")
    if i < 0 or len(tail) - i < 22:
        return None
    return int.from_bytes(tail[i + 10:i + 12], "little"), int.from_bytes(tail[i + 12:i + 16], "little")


def _check_zip_directory(content: bytes) -> None:
    summary = zip_directory_summary(content)
    if summary is not None:
        count, directory_bytes = summary
        _check_archive_limits(count, 0)
        if directory_bytes > MAX_ZIP_DIRECTORY:
            raise _too_big("zip directory too large")


def _build_archive(
    py: list[tuple[str, Callable[[], bytes]]],
    meta: list[tuple[str, Callable[[], bytes]]],
    oversize: int,
    strip: bool = True,
) -> Archive:
    def inner(path: str) -> str:
        return path.split("/", 1)[1] if strip and "/" in path else path

    found, capped = _read_best([(inner(path), read) for path, read in py])
    metadata: dict[str, str] = {}
    for path, read in ((inner(p), r) for p, r in meta):
        if not is_dependency_metadata(path):
            continue
        text = decode_source(read())
        if text is not None:
            metadata[path] = text
    return Archive(py_files=found, metadata=metadata, truncated=capped or oversize > 0)


def is_dependency_metadata(path: str) -> bool:
    """Packaging files that can name a package's dependencies, near the top of the archive."""
    parts = path.split("/")
    if len(parts) > 3:
        return False
    name = parts[-1]
    parent = parts[-2] if len(parts) > 1 else ""
    if name == "requires.txt":
        return parent.endswith(".egg-info")
    if name == "PKG-INFO":
        return len(parts) == 1 or parent.endswith(".egg-info")
    return name in ("pyproject.toml", "setup.cfg", "setup.py") and len(parts) == 1


def parse_egg_requires(text: str) -> list[str]:
    """Dependencies from a setuptools `requires.txt`: the unconditional ones, plus marker sections.

    `[:python_version<"3"]` sections become `dep; marker`. Extras sections (`[test]`) are
    optional dependencies and are skipped.
    """
    deps: list[str] = []
    section: str | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section is None:
            deps.append(line)
        elif section.startswith(":"):
            deps.append(f"{line}; {section[1:]}")
    return deps


def dependencies_from_metadata(files: dict[str, str]) -> list[str]:
    """Best-effort dependency list from packaging files, for packages whose PyPI metadata has none.

    Tries setuptools `requires.txt`, then `PKG-INFO` `Requires-Dist`, then `pyproject.toml`,
    then `setup.cfg`. Returns the first non-empty answer. Never raises on malformed files.
    """
    def named(suffix: str) -> list[str]:
        return [files[p] for p in sorted(files) if p == suffix or p.endswith("/" + suffix)]

    for text in named("requires.txt"):
        deps = parse_egg_requires(text)
        if deps:
            return deps
    for text in named("PKG-INFO"):
        deps = [
            line.split(":", 1)[1].strip() for line in text.splitlines()
            if line.lower().startswith("requires-dist:") and not _is_extra_dependency(line.split(":", 1)[1].strip())
        ]
        if deps:
            return deps
    for text in named("pyproject.toml"):
        try:
            deps = [d for d in (tomllib.loads(text).get("project", {}).get("dependencies") or []) if isinstance(d, str)]
        except (tomllib.TOMLDecodeError, AttributeError):
            continue
        if deps:
            return deps
    for text in named("setup.cfg"):
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(text)
            raw = parser.get("options", "install_requires", fallback="")
        except configparser.Error:
            continue
        deps = [line.strip() for line in raw.splitlines() if line.strip() and not line.strip().startswith("#")]
        if deps:
            return deps
    for text in named("setup.py"):
        deps = install_requires_from_setup_py(text)
        if deps:
            return deps
    return []


def install_requires_from_setup_py(text: str) -> list[str]:
    """`install_requires` from a `setup(...)` call in a setup.py, read from the syntax tree. Never runs the file.

    Handles a literal list, tuple or string, and a name bound once at module level to such a literal
    (`requires = [...]` then `install_requires=requires`). Anything computed is ignored, so the answer can be
    incomplete but is never invented.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return []
    bound: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            bound.setdefault(node.targets[0].id, node.value)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "setup"):
            continue
        for kw in node.keywords:
            if kw.arg != "install_requires":
                continue
            value = bound.get(kw.value.id, kw.value) if isinstance(kw.value, ast.Name) else kw.value
            try:
                literal = ast.literal_eval(value)
            except (ValueError, TypeError, SyntaxError, RecursionError, MemoryError):
                continue
            items = literal.splitlines() if isinstance(literal, str) else literal
            if isinstance(items, (list, tuple)) and all(isinstance(i, str) for i in items):
                return [i.strip() for i in items if i.strip() and not i.strip().startswith("#")]
    return []


_LOW_PRIORITY_DIRS = {"test", "tests", "testing", "doc", "docs", "example", "examples", "benchmarks", "scripts"}


def file_priority(path: str) -> tuple[int, str]:
    """Sort key: package code first, then tests/docs/examples. Ties break alphabetically."""
    parts = path.lower().split("/")
    name = parts[-1]
    low = (
        any(p in _LOW_PRIORITY_DIRS or p.endswith(("_tests", "_test")) for p in parts[:-1])
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )
    return (1 if low else 0, path)


def _read_best(candidates: list[tuple[str, Callable[[], bytes]]]) -> tuple[dict[str, str], bool]:
    """Read the highest-priority candidates until MAX_PY_FILES decodable files are held.

    Returns (files, capped). `capped` is True when any candidate is missing from the result: it was left
    unread, was not valid UTF-8, or shared its path with another entry.
    """
    found: dict[str, str] = {}
    capped = False
    for path, read in sorted(candidates, key=lambda c: file_priority(c[0])):
        if path in found:
            capped = True
            continue
        if len(found) >= MAX_PY_FILES:
            capped = True
            break
        text = decode_source(read())
        if text is None:
            capped = True
        else:
            found[path] = text
    return dict(sorted(found.items())), capped


def decode_source(data: bytes) -> str | None:
    """Decode a Python source file the way Python itself would: by its PEP 263 `# coding:` line, UTF-8 if none.

    Old packages often declare latin-1 or another legacy encoding, so UTF-8 alone would drop those files and
    leave the package incomplete. None means the file does not decode under the encoding it declares.
    """
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        return data.decode(encoding)
    except (SyntaxError, LookupError, UnicodeDecodeError):
        return None


def _clean_member_path(name: str) -> str | None:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        return None
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if ".." in parts or not parts:
        return None
    return "/".join(parts)


def _latest_upload_date(files: list[dict]) -> str | None:
    stamps = [f.get("upload_time_iso_8601") or f.get("upload_time") for f in files]
    stamps = [s for s in stamps if s]
    return max(stamps)[:10] if stamps else None


def _is_extra_dependency(raw: str) -> bool:
    try:
        req = Requirement(raw)
    except InvalidRequirement:
        return False
    return req.marker is not None and "extra" in str(req.marker)


# --- network ---------------------------------------------------------------------


def _get(client: httpx.Client, url: str, what: str, headers: dict | None = None) -> httpx.Response:
    try:
        return client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise ExhumeError(
            f"The crypt is unreachable ({what}): {exc.__class__.__name__}.", 502
        ) from exc


def _json(resp: httpx.Response, what: str) -> dict:
    try:
        data = resp.json()
    except ValueError as exc:
        raise ExhumeError(f"{what} sent back something that was not JSON.", 502) from exc
    if not isinstance(data, dict):
        raise ExhumeError(f"{what} sent back an unexpected payload.", 502)
    return data


def _download(client: httpx.Client, url: str, limit: int = MAX_ARCHIVE_BYTES) -> bytes:
    if not url.startswith("https://"):
        raise ExhumeError("Refusing to download source from a non-HTTPS URL.", 422)
    buf = bytearray()
    try:
        with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise ExhumeError(f"The source archive could not be fetched ({resp.status_code}).", 502)
            for chunk in resp.iter_bytes():
                buf += chunk
                if len(buf) > limit:
                    raise ExhumeError("The corpse is too large to exhume (archive size limit).", 413)
    except httpx.HTTPError as exc:
        raise ExhumeError(f"The crypt is unreachable (source download): {exc.__class__.__name__}.", 502) from exc
    return bytes(buf)


def fetch_pypi_metadata(name: str, client: httpx.Client) -> PyPIMetadata:
    if not _PYPI_NAME_RE.fullmatch(name or ""):
        raise ExhumeError(f"'{name}' is not a valid PyPI package name.", 422)
    resp = _get(client, PYPI_JSON_URL.format(name=name), "PyPI")
    if resp.status_code == 404:
        raise ExhumeError(f"The grave is empty: no PyPI package named '{name}'.", 404)
    if resp.status_code != 200:
        raise ExhumeError(f"PyPI answered with status {resp.status_code}.", 502)
    return parse_pypi_metadata(_json(resp, "PyPI"))


def fetch_dependency_info(
    deps: list[str], target_python: str, client: httpx.Client
) -> dict[str, DependencyInfo]:
    """Look up PyPI facts for each dependency. Failures skip that dependency.

    Python support is judged from the dependency's latest release, so it is an
    approximation when the package pins an older version.
    """
    info: dict[str, DependencyInfo] = {}
    for raw in deps[:MAX_DEP_LOOKUPS]:
        try:
            name = Requirement(raw).name
            meta = fetch_pypi_metadata(name, client)
        except (InvalidRequirement, ExhumeError):
            continue
        info[name] = DependencyInfo(
            last_release_date=meta.last_release_date,
            yanked_versions=meta.yanked_versions,
            supports_target_python=python_supported(meta.requires_python, target_python),
        )
    return info


def exhume_pypi(name: str, target_python: str, client: httpx.Client) -> PackageSnapshot:
    meta = fetch_pypi_metadata(name, client)
    source_map: dict[str, str] = {}
    truncated = False
    dependencies = meta.dependencies
    if meta.sdist_url:
        archive = extract_archive(_download(client, meta.sdist_url), meta.sdist_url.rsplit("/", 1)[-1])
        source_map, truncated = archive.py_files, archive.truncated
        # Older releases often have no `requires_dist` on PyPI; the sdist's own metadata does.
        if not dependencies:
            dependencies = dependencies_from_metadata(archive.metadata)
    return PackageSnapshot(
        ref=PackageRef(source=Source.pypi, identifier=name),
        last_release_date=meta.last_release_date,
        declared_pythons=meta.declared_pythons,
        dependencies=dependencies,
        dependency_info=fetch_dependency_info(dependencies, target_python, client),
        source_map=source_map,
        truncated=truncated,
    )


def exhume_github(
    url: str, target_python: str, client: httpx.Client, token: str | None = None
) -> PackageSnapshot:
    m = _GITHUB_URL_RE.fullmatch((url or "").strip())
    if not m:
        raise ExhumeError(f"'{url}' is not a GitHub repository URL (https://github.com/owner/repo).", 422)
    owner, repo = m.group("owner"), m.group("repo")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    repo_resp = _get(client, f"{GITHUB_API}/repos/{owner}/{repo}", "GitHub", headers)
    _raise_for_github(repo_resp, f"{owner}/{repo}")
    repo_info = _json(repo_resp, "GitHub")
    branch = repo_info.get("default_branch") or "HEAD"
    pushed_at = repo_info.get("pushed_at")

    tree_resp = _get(
        client,
        f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{quote(branch, safe='')}?recursive=1",
        "GitHub",
        headers,
    )
    _raise_for_github(tree_resp, f"{owner}/{repo}")
    tree = _json(tree_resp, "GitHub")
    entries = tree.get("tree") or []

    blobs = [e for e in entries if e.get("type") == "blob" and e.get("path")]
    all_py = [e for e in blobs if e["path"].endswith(".py")]
    py_paths = sorted(
        (e["path"] for e in all_py if (e.get("size") or 0) <= MAX_FILE_BYTES),
        key=file_priority,
    )[:MAX_GITHUB_FILES]

    def raw(path: str) -> str | None:
        resp = _get(client, f"{GITHUB_RAW}/{owner}/{repo}/{quote(branch, safe='')}/{quote(path)}", "GitHub raw")
        return resp.text if resp.status_code == 200 else None

    source_map = {p: t for p in py_paths if (t := raw(p)) is not None}
    truncated = len(source_map) < len(all_py) or tree.get("truncated") is True  # GitHub cut its own listing

    deps: list[str] = []
    if any(e["path"] == "requirements.txt" for e in blobs):
        text = raw("requirements.txt")
        deps = parse_requirements_txt(text) if text else []

    return PackageSnapshot(
        ref=PackageRef(source=Source.github, identifier=f"https://github.com/{owner}/{repo}"),
        last_release_date=pushed_at[:10] if pushed_at else None,
        dependencies=deps,
        dependency_info=fetch_dependency_info(deps, target_python, client),
        source_map=source_map,
        truncated=truncated,
    )


def _raise_for_github(resp: httpx.Response, what: str) -> None:
    if resp.status_code == 200:
        return
    if resp.status_code == 404:
        raise ExhumeError(f"The grave is empty: no GitHub repository '{what}'.", 404)
    if resp.status_code in (403, 429):
        hint = "" if is_public(os.environ.get("CRYPT_KEEPER_PUBLIC")) else " Set GITHUB_TOKEN to raise the limit."
        raise ExhumeError(f"GitHub rate limit reached. Try again later.{hint}", 429)
    raise ExhumeError(f"GitHub answered with status {resp.status_code}.", 502)


def exhume(ref: PackageRef, target_python: str, client: httpx.Client) -> PackageSnapshot:
    if ref.source == Source.pypi:
        return exhume_pypi(ref.identifier.strip(), target_python, client)
    return exhume_github(ref.identifier, target_python, client, os.environ.get("GITHUB_TOKEN"))
