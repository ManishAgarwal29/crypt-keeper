"""The downloadable revival: a patch that applies, the revived sources, and a report. Pure, plus the endpoint."""
import io
import shutil
import subprocess
import zipfile
from datetime import date

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from api import main
from coroner.autopsy import run_autopsy
from models import FileDiff, PackageRef, PackageSnapshot, RevivalResult, Source
from necromancer.export import archive_name, build_export, build_patch, file_patch, split_lines
from necromancer.revive import revive
from tests.test_api import fakes, pypi_routes  # noqa: F401  (fakes is a fixture)
from tests.test_exhumer import pypi_client

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is needed to check that patches apply")
client = TestClient(main.app, raise_server_exceptions=False)


def snap(files: dict[str, str], deps: list[str] | None = None) -> PackageSnapshot:
    return PackageSnapshot(ref=PackageRef(source=Source.pypi, identifier="demo"), source_map=files, dependencies=deps or [],
                           last_release_date="2019-01-01")


def apply_patch(tmp_path, path: str, before: str, patch: str) -> str:
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(before.encode("utf-8"))
    (tmp_path / "p.diff").write_bytes(patch.encode("utf-8"))
    result = subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "--whitespace=nowarn", "p.diff"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return target.read_bytes().decode("utf-8")


# --- splitting lines the way git does ---------------------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("", []),
    ("a", ["a"]),
    ("a\n", ["a\n"]),
    ("a\nb", ["a\n", "b"]),
    ("a\r\nb\r\n", ["a\r\n", "b\r\n"]),
    ("a\x0cb\nc\n", ["a\x0cb\n", "c\n"]),  # a form feed does not end a line
    ("a\u2028b\n", ["a\u2028b\n"]),
])
def test_split_lines(text, expected):
    assert split_lines(text) == expected


@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=200))
def test_split_lines_loses_nothing(text):
    assert "".join(split_lines(text)) == text


# --- patches that really apply --------------------------------------------------------------------------------------------


@needs_git
@pytest.mark.parametrize("before, after", [
    ("import collections\nx = collections.Mapping\n", "import collections.abc\nx = collections.abc.Mapping\n"),
    ("a\r\nb\r\nc\r\n", "a\r\nB\r\nc\r\n"),                       # Windows line endings
    ("a\nb", "a\nB"),                                              # no newline at end of file, before and after
    ("a\nb\n", "a\nb"),                                            # newline removed
    ("a\nb", "a\nb\n"),                                            # newline added
    ("a\x0cb\nc\n", "a\x0cb\nC\n"),                                # form feed inside a line
    ("# \u00e9t\u00e9\nx = 1\n", "# \u00e9t\u00e9\nx = 2\n"),      # non-ASCII text
    ("\n".join(f"line{i}" for i in range(100)) + "\n", "\n".join(f"line{i}" if i % 17 else f"LINE{i}" for i in range(100)) + "\n"),
])
def test_the_patch_applies_and_gives_the_revived_file(tmp_path, before, after):
    assert apply_patch(tmp_path, "pkg/mod.py", before, file_patch("pkg/mod.py", before, after)) == after


@needs_git
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    st.lists(st.sampled_from(["x = 1", "import os", "", "\tindent", "# c\u00e9", "a\x0cb", "tail "]), min_size=1, max_size=12),
    st.lists(st.sampled_from(["x = 2", "import sys", "", "\tindent", "# c\u00e9", "a\x0cb", "tail "]), min_size=1, max_size=12),
    st.booleans(), st.booleans(), st.sampled_from(["\n", "\r\n"]),
)
def test_any_edit_round_trips_through_git_apply(tmp_path_factory, old, new, old_eol, new_eol, eol):
    before = eol.join(old) + (eol if old_eol else "")
    after = eol.join(new) + (eol if new_eol else "")
    if before == after:
        return
    tmp = tmp_path_factory.mktemp("rt")
    assert apply_patch(tmp, "m.py", before, file_patch("m.py", before, after)) == after


def test_an_unchanged_file_gives_an_empty_patch():
    assert file_patch("a.py", "x = 1\n", "x = 1\n") == ""


def test_the_patch_names_each_file_with_a_and_b_prefixes():
    patch = file_patch("pkg/m.py", "a\n", "b\n")
    assert patch.startswith("diff --git a/pkg/m.py b/pkg/m.py\n--- a/pkg/m.py\n+++ b/pkg/m.py\n")


def test_a_path_that_could_escape_is_left_out_of_the_patch():
    revival = RevivalResult(diffs=[
        FileDiff(path="../evil.py", before="a\n", after="b\n"),
        FileDiff(path="/etc/x.py", before="a\n", after="b\n"),
        FileDiff(path="ok.py", before="a\n", after="b\n"),
    ])
    patch = build_patch(revival)
    assert "ok.py" in patch and "evil" not in patch and "/etc" not in patch


# --- the zip ----------------------------------------------------------------------------------------------------------------


def exported(files: dict[str, str], deps: list[str] | None = None) -> tuple[bytes, zipfile.ZipFile]:
    snapshot = snap(files, deps)
    report = run_autopsy(snapshot, "3.12", date(2026, 11, 20))
    data = build_export(snapshot, report, revive(snapshot, report))
    return data, zipfile.ZipFile(io.BytesIO(data))


DEAD = {"demo/__init__.py": "import collections\nx = collections.Mapping\n", "demo/other.py": "y = 1\n"}


def test_the_zip_has_the_patch_the_sources_the_requirements_and_the_report():
    _, zf = exported(DEAD, ["six>=1"])
    assert sorted(zf.namelist()) == [
        "REVIVAL_REPORT.md", "patch.diff", "requirements.revived.txt", "revived/demo/__init__.py", "revived/demo/other.py",
    ]


def test_the_revived_file_is_the_rewritten_one_and_untouched_files_are_kept():
    _, zf = exported(DEAD)
    assert b"collections.abc" in zf.read("revived/demo/__init__.py")
    assert zf.read("revived/demo/other.py") == b"y = 1\n"


def test_the_patch_in_the_zip_mentions_only_changed_files():
    _, zf = exported(DEAD)
    patch = zf.read("patch.diff").decode()
    assert "demo/__init__.py" in patch and "demo/other.py" not in patch


def test_the_report_says_what_it_is_and_is_not():
    _, zf = exported(DEAD)
    report = zf.read("REVIVAL_REPORT.md").decode()
    assert "static" in report and "not a complete package" in report and "demo" in report


def test_a_truncated_source_is_called_out_in_the_report():
    snapshot = snap(DEAD).model_copy(update={"truncated": True})
    report = run_autopsy(snapshot, "3.12", date(2026, 11, 20))
    zf = zipfile.ZipFile(io.BytesIO(build_export(snapshot, report, revive(snapshot, report))))
    assert "incomplete" in zf.read("REVIVAL_REPORT.md").decode()


def test_the_same_revival_gives_the_same_bytes():
    assert exported(DEAD)[0] == exported(DEAD)[0]


def test_every_member_has_a_fixed_timestamp_and_a_safe_name():
    _, zf = exported(DEAD)
    for info in zf.infolist():
        assert info.date_time == (1980, 1, 1, 0, 0, 0)
        assert not info.filename.startswith("/") and ".." not in info.filename.split("/")


def test_unsafe_source_paths_never_become_zip_members():
    _, zf = exported({"../evil.py": "x = 1\n", "/abs.py": "x = 1\n", "ok.py": "x = 1\n"})
    names = zf.namelist()
    assert "revived/ok.py" in names and not any("evil" in n or "abs" in n for n in names)


@needs_git
def test_the_patch_in_the_zip_applies_to_the_original_files(tmp_path):
    _, zf = exported(DEAD)
    for path, content in DEAD.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_bytes(content.encode())
    (tmp_path / "patch.diff").write_bytes(zf.read("patch.diff"))
    assert subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "patch.diff"], cwd=tmp_path, capture_output=True).returncode == 0
    assert (tmp_path / "demo/__init__.py").read_bytes() == zf.read("revived/demo/__init__.py")


@pytest.mark.parametrize("identifier, expected", [
    ("attrdict", "crypt-keeper-attrdict-revived.zip"),
    ("https://github.com/o/some-repo.git", "crypt-keeper-some-repo-revived.zip"),
    ("https://github.com/o/r/", "crypt-keeper-r-revived.zip"),
    ('a"b\r\nSet-Cookie: x', "crypt-keeper-a-b-Set-Cookie-x-revived.zip"),
    ("../../etc/passwd", "crypt-keeper-passwd-revived.zip"),
    ("", "crypt-keeper-package-revived.zip"),
])
def test_the_download_name_cannot_carry_a_path_or_a_header_break(identifier, expected):
    assert archive_name(identifier) == expected


@given(st.text(max_size=120))
def test_the_download_name_is_always_a_plain_file_name(identifier):
    name = archive_name(identifier)
    assert name.startswith("crypt-keeper-") and name.endswith("-revived.zip") and len(name) <= 90
    assert all(c.isalnum() or c in "._-" for c in name)


# --- the endpoint ----------------------------------------------------------------------------------------------------------


BODY = {"ref": {"source": "pypi", "identifier": "deadpkg"}}


def test_export_returns_a_zip_download(fakes):  # noqa: F811
    r = client.post("/revive/export", json=BODY)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"] == 'attachment; filename="crypt-keeper-deadpkg-revived.zip"'
    assert r.headers["cache-control"] == "no-store"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert b"collections.abc" in zf.read("revived/deadpkg/__init__.py") and "deadpkg" in zf.read("patch.diff").decode()


def test_export_runs_nothing(fakes):  # noqa: F811
    client.post("/revive/export", json=BODY)
    assert fakes == [], "the sandbox must not be involved"


def test_export_reports_a_missing_package_as_404(monkeypatch):
    monkeypatch.setattr(main, "_http_client", lambda: pypi_client({}))
    assert client.post("/revive/export", json=BODY).status_code == 404


def test_export_is_a_post_with_json_and_the_origin_guard_applies(fakes):  # noqa: F811
    assert client.get("/revive/export").status_code in (404, 405)
    assert client.post("/revive/export", content="x", headers={"Content-Type": "text/plain"}).status_code in (415, 422, 400)
    assert client.post("/revive/export", json=BODY, headers={"Origin": "https://evil.example"}).status_code == 403


def test_export_rejects_a_bad_body():
    assert client.post("/revive/export", json={"ref": {"source": "pypi"}}).status_code == 422


def test_export_is_refused_when_every_exhume_slot_is_taken(monkeypatch):
    import threading

    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(main, "_exhume_slots", slots)
    monkeypatch.setattr(main, "SLOT_WAIT_SECONDS", 0.1)
    assert client.post("/revive/export", json=BODY).status_code == 503
