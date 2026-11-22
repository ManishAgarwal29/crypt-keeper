"""The exhumer must not lose files silently: wrong top-level stripping, name clashes, GitHub's own truncation."""
import httpx
import pytest

from api.exhumer import ExhumeError, exhume_github, extract_archive
from tests.test_exhumer import github_routes, make_tar, make_zip, pypi_client

TREE_URL = "https://api.github.com/repos/o/r/git/trees/main?recursive=1"


# --- top-level directory stripping --------------------------------------------------------------------------


def test_an_sdist_with_one_top_level_directory_is_stripped():
    archive = extract_archive(make_tar({"p-1/p/__init__.py": b"1", "p-1/setup.py": b"2"}), "p.tar.gz")
    assert sorted(archive.py_files) == ["p/__init__.py", "setup.py"] and not archive.truncated


def test_a_wheel_keeps_its_package_directories():
    """A wheel has `pkg/` and `pkg-1.0.dist-info/` side by side. Stripping each one's first part would be wrong."""
    wheel = make_zip({"pkg/__init__.py": b"1", "pkg/sub/mod.py": b"2", "pkg-1.0.dist-info/METADATA": b"x"})
    archive = extract_archive(wheel, "pkg-1.0-py3-none-any.whl")
    assert sorted(archive.py_files) == ["pkg/__init__.py", "pkg/sub/mod.py"]


def test_an_archive_with_several_top_level_directories_is_not_stripped():
    archive = extract_archive(make_tar({"a/x.py": b"1", "b/y.py": b"2"}), "p.tar.gz")
    assert sorted(archive.py_files) == ["a/x.py", "b/y.py"]


def test_a_file_at_the_root_means_nothing_is_stripped():
    archive = extract_archive(make_tar({"top.py": b"1", "pkg/x.py": b"2"}), "p.tar.gz")
    assert sorted(archive.py_files) == ["pkg/x.py", "top.py"]


def test_dependency_metadata_is_still_found_after_stripping():
    sdist = make_tar({"d-1/d.egg-info/requires.txt": b"oldlib\n", "d-1/d/__init__.py": b"1"})
    assert "d.egg-info/requires.txt" in extract_archive(sdist, "d.tar.gz").metadata


def test_a_metadata_file_nested_too_deep_is_ignored():
    sdist = make_tar({"d-1/a/b/c/PKG-INFO": b"x", "d-1/d/__init__.py": b"1"})
    assert extract_archive(sdist, "d.tar.gz").metadata == {}


# --- losing a file is reported -------------------------------------------------------------------------------


def test_two_entries_with_the_same_path_are_reported_not_dropped_silently():
    archive = extract_archive(make_tar({"p-1/a.py": b"x = 1", "p-1//a.py": b"x = 2"}), "p.tar.gz")
    assert list(archive.py_files) == ["a.py"] and archive.truncated


def test_a_cleanly_read_archive_is_not_truncated():
    assert not extract_archive(make_tar({"p-1/a.py": b"x = 1", "p-1/b.py": b"y = 2"}), "p.tar.gz").truncated


def test_a_utf8_bom_file_is_still_read():
    archive = extract_archive(make_tar({"p-1/a.py": b"\xef\xbb\xbfx = 1"}), "p.tar.gz")
    assert list(archive.py_files) == ["a.py"] and not archive.truncated


# --- GitHub ---------------------------------------------------------------------------------------------------


def test_a_tree_listing_that_github_itself_truncated_is_reported():
    routes = github_routes()
    routes[TREE_URL] = httpx.Response(200, json={"truncated": True, "tree": [{"type": "blob", "path": "pkg/mod.py", "size": 10}]})
    assert exhume_github("https://github.com/o/r", "3.12", pypi_client(routes)).truncated


def test_a_complete_tree_listing_is_not_truncated():
    assert not exhume_github("https://github.com/o/r", "3.12", pypi_client(github_routes())).truncated


@pytest.mark.parametrize("public, mentions_token", [("1", False), (None, True), ("off", True), ("", False), ("garbage", False)])
def test_the_rate_limit_message_only_suggests_a_token_when_the_server_is_private(monkeypatch, public, mentions_token):
    if public is None:
        monkeypatch.delenv("CRYPT_KEEPER_PUBLIC", raising=False)
    else:
        monkeypatch.setenv("CRYPT_KEEPER_PUBLIC", public)
    client = pypi_client({"https://api.github.com/repos/o/r": httpx.Response(403)})
    with pytest.raises(ExhumeError) as err:
        exhume_github("https://github.com/o/r", "3.12", client)
    assert err.value.status == 429 and ("GITHUB_TOKEN" in err.value.message) is mentions_token
