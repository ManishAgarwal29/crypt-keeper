"""Tests for the Exhumer (task 9). All HTTP is mocked; no network."""
import io
import string
import tarfile
import zipfile

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from api.exhumer import (
    ExhumeError,
    exhume,
    exhume_github,
    exhume_pypi,
    extract_py_files,
    parse_pypi_metadata,
    parse_requirements_txt,
    python_supported,
)
from models import PackageRef, Source

SDIST_URL = "https://files.pythonhosted.org/packages/deadpkg-1.0.tar.gz"


def make_tar(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def make_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def pypi_payload(name="deadpkg", version="1.0", requires_dist=None, requires_python=None,
                 uploaded="2019-03-04T10:00:00.000000Z", releases_extra=None):
    files = [{"packagetype": "sdist", "url": SDIST_URL, "upload_time_iso_8601": uploaded}]
    releases = {version: files}
    releases.update(releases_extra or {})
    return {
        "info": {
            "name": name,
            "version": version,
            "requires_dist": requires_dist,
            "requires_python": requires_python,
            "classifiers": [
                "Programming Language :: Python :: 3.6",
                "Programming Language :: Python :: 3.7",
                "Programming Language :: Python :: 3",
                "License :: OSI Approved :: MIT License",
            ],
        },
        "urls": files,
        "releases": releases,
    }


# --- parsing ---------------------------------------------------------------------


def test_parse_pypi_metadata_extracts_core_facts():
    meta = parse_pypi_metadata(
        pypi_payload(requires_dist=["requests==2.0.0", "pytest; extra == 'test'"], requires_python=">=3.6")
    )
    assert meta.last_release_date == "2019-03-04"
    assert meta.declared_pythons == ["3.6", "3.7"]
    assert meta.dependencies == ["requests==2.0.0"]  # extras dropped
    assert meta.requires_python == ">=3.6"
    assert meta.sdist_url == SDIST_URL


def test_parse_pypi_metadata_finds_fully_yanked_versions_only():
    payload = pypi_payload(releases_extra={
        "0.9": [{"yanked": True}],
        "0.8": [{"yanked": True}, {"yanked": False}],
    })
    assert parse_pypi_metadata(payload).yanked_versions == ["0.9"]


def test_parse_pypi_metadata_tolerates_empty_payload():
    meta = parse_pypi_metadata({})
    assert meta.last_release_date is None and meta.dependencies == []


@pytest.mark.parametrize("spec,target,expected", [
    (">=3.6", "3.12", True),
    (">=3.6,<3.10", "3.12", False),
    (None, "3.12", None),
    ("garbage!!", "3.12", None),
    (">=3.6", "not-a-version", None),
])
def test_python_supported(spec, target, expected):
    assert python_supported(spec, target) is expected


def test_parse_requirements_txt_skips_comments_and_options():
    text = "# header\nrequests==2.0  # pinned\n\n-r other.txt\nflask>=1\n"
    assert parse_requirements_txt(text) == ["requests==2.0", "flask>=1"]


# --- archive extraction ----------------------------------------------------------


def test_extract_strips_top_dir_and_keeps_only_python():
    tar = make_tar({"pkg-1.0/pkg/mod.py": b"x = 1", "pkg-1.0/README.md": b"hi"})
    assert extract_py_files(tar, "pkg-1.0.tar.gz") == {"pkg/mod.py": "x = 1"}


def test_extract_handles_zip():
    z = make_zip({"pkg-1.0/a.py": b"a = 1"})
    assert extract_py_files(z, "pkg-1.0.zip") == {"a.py": "a = 1"}


def test_extract_skips_path_traversal_and_absolute_paths():
    tar = make_tar({"pkg/../../evil.py": b"1", "/abs/evil.py": b"2", "pkg/ok.py": b"3"})
    assert extract_py_files(tar, "x.tar.gz") == {"ok.py": "3"}


def test_extract_skips_undecodable_files():
    tar = make_tar({"pkg/bad.py": b"\xff\xfe\x00bad", "pkg/good.py": b"ok"})
    assert extract_py_files(tar, "x.tar.gz") == {"good.py": "ok"}


def test_extract_reports_corrupt_archive():
    with pytest.raises(ExhumeError) as err:
        extract_py_files(b"not an archive", "x.tar.gz")
    assert err.value.status == 422


@given(st.dictionaries(
    st.text(alphabet=string.ascii_letters + "./\\", min_size=1, max_size=30).map(lambda s: s + ".py"),
    st.just(b"x = 1"),
    max_size=8,
))
def test_extracted_paths_are_always_safe(members):
    for path in extract_py_files(make_tar(members), "x.tar.gz"):
        assert not path.startswith("/")
        assert ".." not in path.split("/")


@given(st.text(max_size=300))
def test_parse_requirements_txt_never_raises(text):
    assert all(isinstance(d, str) and d for d in parse_requirements_txt(text))


# --- PyPI exhumation (mocked HTTP) -------------------------------------------------


def pypi_client(routes: dict[str, httpx.Response | Exception]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        result = routes.get(str(request.url))
        if result is None:
            return httpx.Response(404)
        if isinstance(result, Exception):
            raise result
        return result

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_exhume_pypi_builds_full_snapshot():
    routes = {
        "https://pypi.org/pypi/deadpkg/json": httpx.Response(
            200, json=pypi_payload(requires_dist=["oldlib==1.0"]),
        ),
        SDIST_URL: httpx.Response(200, content=make_tar({"deadpkg-1.0/deadpkg/__init__.py": b"import imp"})),
        "https://pypi.org/pypi/oldlib/json": httpx.Response(
            200, json=pypi_payload(name="oldlib", requires_python="<3.8", uploaded="2017-01-01T00:00:00Z"),
        ),
    }
    snap = exhume_pypi("deadpkg", "3.12", pypi_client(routes))
    assert snap.ref.source == Source.pypi
    assert snap.last_release_date == "2019-03-04"
    assert snap.source_map == {"deadpkg/__init__.py": "import imp"}
    assert snap.dependency_info["oldlib"].supports_target_python is False
    assert snap.dependency_info["oldlib"].last_release_date == "2017-01-01"


def test_exhume_pypi_skips_dependency_that_cannot_be_looked_up():
    routes = {
        "https://pypi.org/pypi/deadpkg/json": httpx.Response(200, json=pypi_payload(requires_dist=["ghostlib>=1"])),
        SDIST_URL: httpx.Response(200, content=make_tar({"d/a.py": b"1"})),
    }
    snap = exhume_pypi("deadpkg", "3.12", pypi_client(routes))
    assert snap.dependency_info == {} and snap.dependencies == ["ghostlib>=1"]


def test_exhume_pypi_missing_package_names_what_was_searched():
    with pytest.raises(ExhumeError) as err:
        exhume_pypi("nopkg", "3.12", pypi_client({}))
    assert err.value.status == 404 and "nopkg" in err.value.message and "PyPI" in err.value.message


@pytest.mark.parametrize("name", ["", "../etc/passwd", "a b", "pkg/../x", "-bad"])
def test_exhume_pypi_rejects_invalid_names_without_network(name):
    with pytest.raises(ExhumeError) as err:
        exhume_pypi(name, "3.12", pypi_client({}))
    assert err.value.status == 422


def test_exhume_pypi_reports_unreachable_upstream():
    client = pypi_client({"https://pypi.org/pypi/deadpkg/json": httpx.ConnectError("boom")})
    with pytest.raises(ExhumeError) as err:
        exhume_pypi("deadpkg", "3.12", client)
    assert err.value.status == 502


def test_exhume_pypi_rejects_non_https_source_url():
    payload = pypi_payload()
    payload["urls"][0]["url"] = "http://insecure.example/x.tar.gz"
    client = pypi_client({"https://pypi.org/pypi/deadpkg/json": httpx.Response(200, json=payload)})
    with pytest.raises(ExhumeError) as err:
        exhume_pypi("deadpkg", "3.12", client)
    assert err.value.status == 422


def test_exhume_dispatches_on_source():
    client = pypi_client({})
    with pytest.raises(ExhumeError) as err:
        exhume(PackageRef(source=Source.github, identifier="https://example.com/x/y"), "3.12", client)
    assert err.value.status == 422


# --- GitHub exhumation (mocked HTTP) -----------------------------------------------


def github_routes():
    return {
        "https://api.github.com/repos/o/r": httpx.Response(
            200, json={"default_branch": "main", "pushed_at": "2018-06-01T00:00:00Z"}
        ),
        "https://api.github.com/repos/o/r/git/trees/main?recursive=1": httpx.Response(200, json={"tree": [
            {"type": "blob", "path": "pkg/mod.py", "size": 10},
            {"type": "blob", "path": "README.md", "size": 5},
            {"type": "blob", "path": "requirements.txt", "size": 20},
            {"type": "tree", "path": "pkg"},
        ]}),
        "https://raw.githubusercontent.com/o/r/main/pkg/mod.py": httpx.Response(200, text="x = 1"),
        "https://raw.githubusercontent.com/o/r/main/requirements.txt": httpx.Response(200, text="flask==0.10\n"),
    }


def test_exhume_github_builds_snapshot():
    snap = exhume_github("https://github.com/o/r", "3.12", pypi_client(github_routes()))
    assert snap.ref.source == Source.github
    assert snap.last_release_date == "2018-06-01"
    assert snap.source_map == {"pkg/mod.py": "x = 1"}
    assert snap.dependencies == ["flask==0.10"]


def test_exhume_github_accepts_dot_git_suffix():
    snap = exhume_github("https://github.com/o/r.git", "3.12", pypi_client(github_routes()))
    assert snap.ref.identifier == "https://github.com/o/r"


def test_exhume_github_missing_repo():
    with pytest.raises(ExhumeError) as err:
        exhume_github("https://github.com/o/r", "3.12", pypi_client({}))
    assert err.value.status == 404 and "o/r" in err.value.message


def test_exhume_github_rate_limit():
    client = pypi_client({"https://api.github.com/repos/o/r": httpx.Response(403)})
    with pytest.raises(ExhumeError) as err:
        exhume_github("https://github.com/o/r", "3.12", client)
    assert err.value.status == 429


@pytest.mark.parametrize("url", ["", "http://github.com/o/r", "https://gitlab.com/o/r", "https://github.com/o"])
def test_exhume_github_rejects_bad_urls(url):
    with pytest.raises(ExhumeError) as err:
        exhume_github(url, "3.12", pypi_client({}))
    assert err.value.status == 422
