"""Found by the benchmark: files with a `# coding:` line were dropped, and `setup.py` dependencies were never read."""
import io
import sys
import zipfile
from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from api.exhumer import decode_source, dependencies_from_metadata, extract_archive, install_requires_from_setup_py
from api.sandbox import _write_sources, run_proof_of_life
from coroner.autopsy import run_autopsy
from models import PackageRef, PackageSnapshot, Source
from necromancer.export import build_export
from necromancer.revive import revive
from proof.discovery import encode_source
from tests.test_exhumer import make_tar


# --- decoding by the declared encoding ------------------------------------------------------------------------------


@pytest.mark.parametrize("data, expected", [
    (b"x = 1\n", "x = 1\n"),
    ("# caf\u00e9\nx = 1\n".encode("utf-8"), "# caf\u00e9\nx = 1\n"),
    (b"\xef\xbb\xbfx = 1\n", "x = 1\n"),                                      # UTF-8 with a BOM
    (b"# -*- coding: latin-1 -*-\ns = '\xe9'\n", "# -*- coding: latin-1 -*-\ns = '\u00e9'\n"),
    (b"#!/usr/bin/python\n# coding=iso-8859-15\ns = '\xa4'\n", "#!/usr/bin/python\n# coding=iso-8859-15\ns = '\u20ac'\n"),
])
def test_a_file_is_decoded_the_way_python_would_read_it(data, expected):
    assert decode_source(data) == expected


@pytest.mark.parametrize("data", [
    b"s = '\xff\xfe'\n",                                  # not UTF-8 and no declaration
    b"# coding: utf-8\ns = '\xff'\n",                     # declares UTF-8, is not
    b"# coding: no-such-codec\nx = 1\n",                  # an encoding that does not exist
])
def test_a_file_that_does_not_decode_under_its_own_declaration_is_refused(data):
    assert decode_source(data) is None


def test_the_first_declaration_wins():
    assert decode_source(b"# coding: latin-1\n# coding: utf-8\ns = '\xe9'\n") == "# coding: latin-1\n# coding: utf-8\ns = '\u00e9'\n"


@given(st.binary(max_size=200))
def test_decoding_never_raises(data):
    result = decode_source(data)  # an exception here would fail the test
    assert result is None or "".join(result) == result


def test_a_latin1_file_is_kept_and_does_not_make_the_archive_truncated():
    sdist = make_tar({"p-1/p/__init__.py": b"# -*- coding: latin-1 -*-\nNAME = '\xe9'\n", "p-1/p/b.py": b"y = 1\n"})
    archive = extract_archive(sdist, "p.tar.gz")
    assert sorted(archive.py_files) == ["p/__init__.py", "p/b.py"] and not archive.truncated
    assert archive.py_files["p/__init__.py"].endswith("NAME = '\u00e9'\n")


def test_a_file_that_cannot_be_decoded_still_marks_the_archive_truncated():
    archive = extract_archive(make_tar({"p-1/a.py": b"x = 1\n", "p-1/bad.py": b"s = '\xff\xfe'\n"}), "p.tar.gz")
    assert archive.truncated and list(archive.py_files) == ["a.py"]


# --- encoding it back -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("x = 1\n", b"x = 1\n"),
    ("s = '\u00e9'\n", "s = '\u00e9'\n".encode("utf-8")),
    ("# coding: latin-1\ns = '\u00e9'\n", b"# coding: latin-1\ns = '\xe9'\n"),
    ("#!/x\n# -*- coding: utf-8 -*-\ns = '\u00e9'\n", "#!/x\n# -*- coding: utf-8 -*-\ns = '\u00e9'\n".encode("utf-8")),
    ("# coding: latin-1\ns = '\u20ac'\n", "# coding: latin-1\ns = '\u20ac'\n".encode("utf-8")),   # cannot be latin-1: fall back
    ("# coding: nonsense\ns = '\u00e9'\n", "# coding: nonsense\ns = '\u00e9'\n".encode("utf-8")),
    ("\n\n# coding: latin-1\ns = '\u00e9'\n", "\n\n# coding: latin-1\ns = '\u00e9'\n".encode("utf-8")),  # only the first two lines count
])
def test_encode_source(text, expected):
    assert encode_source(text) == expected


@pytest.mark.parametrize("name", ["rot13", "zlib", "base64", "hex", "bz2", "uu", "x.y", "encodings.aliases", "../x", "a b", ""])
def test_a_declaration_that_is_not_a_plain_text_encoding_falls_back_to_utf8(name):
    text = f"# coding: {name}\ns = '\u00e9'\n"
    assert encode_source(text) == text.encode("utf-8")


@pytest.mark.parametrize("line, captured", [
    ("# coding: latin-1", "latin-1"),
    ("# -*- coding: utf_8 -*-", "utf_8"),
    ("# coding: x.y", "x"),                       # a dot ends the name: a dotted path is never looked up
    ("# coding: encodings.aliases", "encodings"),
    ("#coding=cp1252", "cp1252"),
])
def test_the_declared_name_is_limited_to_letters_digits_underscore_and_hyphen(line, captured):
    from proof.discovery import _CODING_COOKIE

    assert _CODING_COOKIE.match(line).group(1) == captured


@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=80))
def test_decode_then_encode_gives_back_the_same_bytes_for_declared_latin1(body):
    data = b"# coding: latin-1\n" + bytes(ord(c) % 256 for c in body)
    assert encode_source(decode_source(data)) == data


# --- a latin-1 package really runs ------------------------------------------------------------------------------------------


def test_a_latin1_package_is_written_as_latin1_and_imports_in_the_sandbox():
    source = "# -*- coding: latin-1 -*-\nNAME = '\u00e9'\nassert len(NAME) == 1 and ord(NAME) == 0xe9\n"
    snapshot = PackageSnapshot(ref=PackageRef(source=Source.pypi, identifier="latin"), source_map={"latin/__init__.py": source})
    result = run_proof_of_life(snapshot, "%d.%d" % sys.version_info[:2])
    assert result.imported, result.error


def test_the_sandbox_writes_the_bytes_the_declaration_promises(tmp_path):
    assert _write_sources(tmp_path, {"m.py": "# coding: latin-1\ns = '\u00e9'\n"}) is None
    assert (tmp_path / "m.py").read_bytes() == b"# coding: latin-1\ns = '\xe9'\n"


def test_the_zip_holds_each_revived_file_in_its_declared_encoding():
    files = {"p/__init__.py": "# coding: latin-1\nimport collections\nx = collections.Mapping\ns = '\u00e9'\n"}
    snapshot = PackageSnapshot(ref=PackageRef(source=Source.pypi, identifier="p"), source_map=files)
    report = run_autopsy(snapshot, "3.12", date(2026, 11, 20))
    zf = zipfile.ZipFile(io.BytesIO(build_export(snapshot, report, revive(snapshot, report))))
    revived = zf.read("revived/p/__init__.py")
    assert b"collections.abc" in revived and revived.endswith(b"s = '\xe9'\n")


# --- dependencies declared in setup.py ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("setup, expected", [
    ("from setuptools import setup\nsetup(name='x', install_requires=['six', 'requests>=2'])\n", ["six", "requests>=2"]),
    ("import setuptools\nsetuptools.setup(install_requires=('six',))\n", ["six"]),
    ("from setuptools import setup\nsetup(install_requires='six\\nattrs\\n')\n", ["six", "attrs"]),
    ("from setuptools import setup\nREQ = ['six']\nsetup(install_requires=REQ)\n", ["six"]),
    ("from setuptools import setup\nsetup(install_requires=['a', '# comment', ''])\n", ["a"]),
    ("from setuptools import setup\nsetup(name='x')\n", []),
    ("from setuptools import setup\nsetup(install_requires=read('requirements.txt'))\n", []),   # computed: ignored, not guessed
    ("from setuptools import setup\nsetup(install_requires=['a'] + extra)\n", []),
    ("from setuptools import setup\nsetup(install_requires=[1, 2])\n", []),
    ("print 'python 2 only'\n", []),                                                              # does not parse
    ("", []),
])
def test_install_requires_is_read_from_the_syntax_tree(setup, expected):
    assert install_requires_from_setup_py(setup) == expected


def test_setup_py_is_never_executed():
    evil = "import os\nos.system('touch /tmp/pwned')\nfrom setuptools import setup\nsetup(install_requires=['six'])\n"
    assert install_requires_from_setup_py(evil) == ["six"]  # read, not run (a run would have raised or touched a file)


@given(st.text(max_size=300))
def test_any_text_is_handled_without_raising(text):
    assert isinstance(install_requires_from_setup_py(text), list)


def test_setup_py_is_the_last_resort_after_the_other_metadata():
    files = {"setup.py": "from setuptools import setup\nsetup(install_requires=['from-setup'])\n",
             "setup.cfg": "[options]\ninstall_requires =\n    from-cfg\n"}
    assert dependencies_from_metadata(files) == ["from-cfg"]
    assert dependencies_from_metadata({"setup.py": files["setup.py"]}) == ["from-setup"]


def test_the_archive_returns_setup_py_as_metadata_and_still_as_source():
    sdist = make_tar({"p-1/setup.py": b"from setuptools import setup\nsetup(install_requires=['six'])\n", "p-1/p/__init__.py": b"x = 1\n"})
    archive = extract_archive(sdist, "p.tar.gz")
    assert "setup.py" in archive.py_files and "setup.py" in archive.metadata
    assert dependencies_from_metadata(archive.metadata) == ["six"]


def test_only_a_top_level_setup_py_counts_as_metadata():
    sdist = make_tar({"p-1/docs/setup.py": b"setup(install_requires=['x'])\n", "p-1/p/__init__.py": b"x = 1\n"})
    assert extract_archive(sdist, "p.tar.gz").metadata == {}
