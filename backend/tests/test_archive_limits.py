"""Decompression-bomb limits in the exhumer.

The old code capped only the *compressed* download (20 MB) and then called `tf.getmembers()`, which
decompresses and indexes every entry. A few megabytes can hold millions of empty entries, or one file
that expands to gigabytes. The caps now apply to what the archive headers claim, before data is read.
"""
import gzip
import io
import tarfile
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st

from api import exhumer, main
from api.exhumer import ExhumeError, exhume_pypi, extract_archive, zip_directory_summary
from tests.test_exhumer import SDIST_URL, make_zip, pypi_client, pypi_payload


def tar_with(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def tar_with_empty_entries(count: int) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for i in range(count):
            tf.addfile(tarfile.TarInfo(f"p/f{i}.txt"))
    return buf.getvalue()


def header_only_tar(claimed_size: int) -> bytes:
    """A gzip'd tar that is just one header claiming `claimed_size` bytes, with no data behind it."""
    info = tarfile.TarInfo("p/huge.bin")
    info.size = claimed_size
    return gzip.compress(info.tobuf(format=tarfile.GNU_FORMAT))


def refused(archive: bytes, name: str = "x.tar.gz") -> ExhumeError:
    with pytest.raises(ExhumeError) as err:
        extract_archive(archive, name)
    return err.value


# --- the numbers ------------------------------------------------------------------------------------------


def test_the_documented_limits():
    assert exhumer.MAX_ARCHIVE_ENTRIES == 20_000
    assert exhumer.MAX_ARCHIVE_UNPACKED == 250_000_000
    assert exhumer.MAX_ARCHIVE_BYTES == 20_000_000


# --- tar: entry count -------------------------------------------------------------------------------------------


def test_a_tar_with_too_many_entries_is_refused_with_413(monkeypatch):
    monkeypatch.setattr(exhumer, "MAX_ARCHIVE_ENTRIES", 50)
    error = refused(tar_with_empty_entries(51))
    assert error.status == 413 and "entries" in error.message and "too large" in error.message


def test_a_tar_at_exactly_the_entry_limit_is_accepted(monkeypatch):
    monkeypatch.setattr(exhumer, "MAX_ARCHIVE_ENTRIES", 50)
    assert extract_archive(tar_with_empty_entries(50), "x.tar.gz").py_files == {}


def test_a_million_empty_entries_would_be_refused_without_being_read():
    """Scaled down: 20,001 empty entries against the real limit."""
    started = time.monotonic()
    error = refused(tar_with_empty_entries(exhumer.MAX_ARCHIVE_ENTRIES + 1))
    assert error.status == 413 and time.monotonic() - started < 10


# --- tar: unpacked size -----------------------------------------------------------------------------------------


def test_the_sizes_in_the_headers_are_added_up(monkeypatch):
    monkeypatch.setattr(exhumer, "MAX_ARCHIVE_UNPACKED", 1000)
    assert refused(tar_with({"p/a.bin": b"x" * 600, "p/b.bin": b"y" * 600})).status == 413
    assert extract_archive(tar_with({"p/a.bin": b"x" * 600, "p/b.bin": b"y" * 300}), "x.tar.gz")


def test_a_huge_claimed_size_is_refused_before_its_data_is_read():
    """The header claims 10 GB and nothing follows. If the code tried to skip the data it would hit
    the end of the file and fail with 422; a 413 proves the check ran on the header alone."""
    error = refused(header_only_tar(10_000_000_000))
    assert error.status == 413 and "MB unpacked" in error.message


def test_a_size_just_under_the_limit_in_the_header_is_not_refused(monkeypatch):
    monkeypatch.setattr(exhumer, "MAX_ARCHIVE_UNPACKED", 5000)
    error = refused(header_only_tar(5000))  # allowed by the cap, so it proceeds and then runs out of data
    assert error.status == 422


# --- tar: reading still works after the member list is cleared -----------------------------------------------------


def test_files_are_read_correctly_from_a_large_archive():
    """`tf.members` is emptied as the headers are walked; the contents must still be readable afterwards."""
    members = {f"pkg/m{i:04}.py": f"VALUE = {i}\n".encode() for i in range(3000)}
    archive = extract_archive(tar_with(members), "pkg-1.0.tar.gz")
    assert len(archive.py_files) == exhumer.MAX_PY_FILES and archive.truncated
    for path, text in archive.py_files.items():  # the top-level directory "pkg/" is stripped from the keys
        assert text == members[f"pkg/{path}"].decode(), path


def test_metadata_files_are_still_found_in_a_large_archive():
    members = {f"p/a{i}.py": b"x = 1\n" for i in range(500)}
    members["p/p.egg-info/requires.txt"] = b"six\n"
    assert extract_archive(tar_with(members), "p.tar.gz").metadata == {"p.egg-info/requires.txt": "six\n"}


# --- zip --------------------------------------------------------------------------------------------------------------


def test_a_zip_with_too_many_entries_is_refused_before_it_is_parsed(monkeypatch):
    monkeypatch.setattr(exhumer, "MAX_ARCHIVE_ENTRIES", 10)
    assert refused(make_zip({f"p/f{i}.py": b"1" for i in range(11)}), "x.zip").status == 413
    assert extract_archive(make_zip({f"p/f{i}.py": b"1" for i in range(10)}), "x.zip").py_files


def test_a_zip64_entry_count_marker_is_refused():
    data = bytearray(make_zip({"p/a.py": b"1"}))
    eocd = bytes(data).rfind(b"PK\x05\x06")
    data[eocd + 10:eocd + 12] = b"\xff\xff"  # "see the zip64 record"
    assert refused(bytes(data), "x.zip").status == 413


def test_an_oversized_zip_directory_is_refused(monkeypatch):
    monkeypatch.setattr(exhumer, "MAX_ZIP_DIRECTORY", 100)
    assert refused(make_zip({f"p/{'n' * 40}{i}.py": b"1" for i in range(5)}), "x.zip").status == 413


def test_the_summary_reads_a_real_zip():
    count, directory = zip_directory_summary(make_zip({"p/a.py": b"1", "p/b.py": b"2"}))
    assert count == 2 and directory > 0


@given(st.binary(max_size=300))
def test_the_zip_summary_never_raises_on_arbitrary_bytes(data):
    result = zip_directory_summary(data)
    assert result is None or (len(result) == 2 and all(isinstance(n, int) and n >= 0 for n in result))


@given(st.binary(max_size=400), st.sampled_from(["x.zip", "x.tar.gz", "x.whl"]))
def test_extraction_only_ever_fails_with_an_exhume_error(data, name):
    try:
        extract_archive(data, name)
    except ExhumeError:
        pass


# --- end to end ---------------------------------------------------------------------------------------------------------------


def bomb_routes(archive: bytes) -> dict:
    return {
        "https://pypi.org/pypi/deadpkg/json": httpx.Response(200, json=pypi_payload()),
        SDIST_URL: httpx.Response(200, content=archive),
    }


def test_exhuming_a_bomb_raises_413(monkeypatch):
    monkeypatch.setattr(exhumer, "MAX_ARCHIVE_ENTRIES", 20)
    with pytest.raises(ExhumeError) as err:
        exhume_pypi("deadpkg", "3.12", pypi_client(bomb_routes(tar_with_empty_entries(21))))
    assert err.value.status == 413


def test_the_api_answers_a_bomb_with_a_themed_413_and_stays_up(monkeypatch):
    monkeypatch.setattr(exhumer, "MAX_ARCHIVE_ENTRIES", 20)
    monkeypatch.setattr(main, "_http_client", lambda: pypi_client(bomb_routes(tar_with_empty_entries(21))))
    client = TestClient(main.app, raise_server_exceptions=False)
    for path in ("/exhume", "/revive"):
        body = {"ref": {"source": "pypi", "identifier": "deadpkg"}}
        r = client.post(path, json=body)
        assert r.status_code == 413 and "too large to exhume" in r.json()["detail"], path
    assert client.get("/health").status_code == 200
