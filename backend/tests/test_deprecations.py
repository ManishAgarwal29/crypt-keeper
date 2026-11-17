"""Tests for deprecated-API detection (task 4). Pure; no network."""
import pytest
from hypothesis import given
from hypothesis import strategies as st

from api.exhumer import extract_py_files, file_priority
from coroner.autopsy import detect_deprecated_apis
from coroner.deprecations import REMOVED_ATTRS, REMOVED_MODULES, parse_target, scan_source
from tests.test_exhumer import make_tar


def scan(src: str, target: str = "3.12", path: str = "m.py"):
    return scan_source(path, src, target)


def messages(findings):
    return [f.message for f in findings]


# --- version awareness --------------------------------------------------------------


def test_removed_module_flagged_at_or_after_removal_version():
    assert len(scan("import imp", "3.12")) == 1
    assert len(scan("import imp", "3.13")) == 1


def test_removed_module_not_flagged_before_removal_version():
    assert scan("import imp", "3.11") == []


def test_python2_module_flagged_for_any_python3_target():
    findings = scan("import urllib2", "3.8")
    assert len(findings) == 1 and "Python 2 only" in findings[0].message
    assert "urllib.request" in findings[0].message


def test_unparseable_target_flags_everything():
    assert parse_target("nonsense") == (99, 0)
    assert len(scan("import imp", "nonsense")) == 1


def test_pep594_module_flagged_only_on_313():
    assert scan("import telnetlib", "3.12") == []
    assert len(scan("import telnetlib", "3.13")) == 1


# --- import forms ------------------------------------------------------------------


def test_submodule_import_matches_removed_package():
    assert len(scan("import distutils.core")) == 1
    assert len(scan("from distutils.core import setup")) == 1


def test_from_import_of_removed_attribute():
    findings = scan("from collections import Mapping")
    assert len(findings) == 1 and "collections.abc.Mapping" in findings[0].message


def test_from_import_of_removed_submodule():
    assert len(scan("from tkinter import tix", "3.13")) == 1
    assert scan("from tkinter import ttk", "3.13") == []


def test_aliased_module_attribute_is_resolved():
    findings = scan("import collections as c\nx = c.Mapping")
    assert len(findings) == 1 and findings[0].location == "m.py:2"


def test_decorator_use_is_found():
    assert len(scan("import asyncio\n@asyncio.coroutine\ndef f(): pass")) == 1


# --- precision: things that must NOT be flagged ------------------------------------------


def test_modern_collections_abc_is_not_flagged():
    assert scan("import collections.abc\nx = collections.abc.Mapping") == []
    assert scan("from collections.abc import Mapping") == []


def test_mentions_in_comments_and_strings_are_not_flagged():
    src = '# import imp\nx = "collections.Mapping"\n"""import distutils"""'
    assert scan(src) == []


def test_unresolved_name_is_not_flagged():
    assert scan("x = collections.Mapping") == []


@pytest.mark.parametrize("src", [
    "try:\n    import urllib2\nexcept ImportError:\n    import urllib.request",
    "try:\n    import urllib.request\nexcept ImportError:\n    import urllib2",
    "import collections\ntry:\n    M = collections.Mapping\nexcept AttributeError:\n    M = None",
    "try:\n    from collections import Mapping\nexcept ImportError:\n    from collections.abc import Mapping",
    "try:\n    import imp\nexcept:\n    pass",
])
def test_compatibility_guards_are_not_flagged(src):
    assert scan(src) == []


def test_unrelated_exception_handler_does_not_guard():
    src = "try:\n    import imp\nexcept ValueError:\n    pass"
    assert len(scan(src)) == 1


# --- unittest aliases -----------------------------------------------------------------


def test_removed_testcase_alias_flagged_when_unittest_imported():
    src = "import unittest\nclass T(unittest.TestCase):\n    def t(self):\n        self.assertEquals(1, 1)"
    findings = scan(src)
    assert len(findings) == 1 and "assertEqual" in findings[0].message


def test_testcase_alias_ignored_without_unittest():
    assert scan("class T:\n    def t(self):\n        self.assert_(True)") == []


def test_testcase_alias_not_flagged_before_312():
    src = "import unittest\nclass T(unittest.TestCase):\n    def t(self):\n        self.assertEquals(1, 1)"
    assert scan(src, "3.11") == []


# --- python 2 syntax ------------------------------------------------------------------


def test_python2_syntax_is_reported_with_line():
    findings = scan("x = 1\nprint 'hello'\n")
    assert len(findings) == 1
    assert findings[0].kind == "syntax-error" and findings[0].location == "m.py:2"


def test_null_bytes_are_reported_not_raised():
    assert scan("x = 1\x00")[0].kind == "syntax-error"


def test_each_finding_has_file_and_line_location():
    findings = scan("import imp\nimport distutils\n", path="pkg/a.py")
    assert [f.location for f in findings] == ["pkg/a.py:1", "pkg/a.py:2"]


def test_detect_deprecated_apis_scans_all_files_in_order():
    findings = detect_deprecated_apis({"b.py": "import imp", "a.py": "import imp"}, "3.12")
    assert [f.location for f in findings] == ["a.py:1", "b.py:1"]


# --- table sanity -----------------------------------------------------------------------


def test_table_entries_are_well_formed():
    for table in (REMOVED_MODULES, REMOVED_ATTRS):
        for name, removal in table.items():
            assert name and removal.advice
            major, minor = removal.removed_in
            assert major == 3 and 0 <= minor <= 14


# --- property-based -----------------------------------------------------------------------


@given(st.text(max_size=300), st.sampled_from(["3.8", "3.12", "3.13", "bad"]))
def test_scan_never_raises_on_arbitrary_text(src, target):
    for f in scan_source("x.py", src, target):
        assert f.location and f.location.startswith("x.py")


@given(st.sampled_from(sorted(REMOVED_MODULES)))
def test_every_removed_module_is_detected_at_latest_target(name):
    assert scan_source("m.py", f"import {name}", "3.99")


@given(st.sampled_from(sorted(REMOVED_MODULES)), st.integers(0, 11))
def test_findings_only_appear_when_target_reaches_removal(name, minor):
    removed_in = REMOVED_MODULES[name].removed_in
    findings = scan_source("m.py", f"import {name}", f"3.{minor}")
    assert bool(findings) == ((3, minor) >= removed_in)


# --- file prioritisation in the exhumer ---------------------------------------------------


def test_package_code_outranks_tests_and_docs():
    paths = ["tests/test_a.py", "doc/conf.py", "pkg/core.py", "examples/demo.py", "pkg/test_helpers.py"]
    ranked = sorted(paths, key=file_priority)
    assert ranked[0] == "pkg/core.py"
    assert set(ranked[1:]) == {"tests/test_a.py", "doc/conf.py", "examples/demo.py", "pkg/test_helpers.py"}


@pytest.mark.parametrize("path", ["functional_tests/a.py", "unit_tests/support/b.py", "pkg/tests/c.py"])
def test_suffixed_test_directories_rank_as_tests(path):
    assert file_priority(path)[0] == 1


def test_package_named_like_tests_substring_is_not_demoted():
    assert file_priority("latest/core.py")[0] == 0
    assert file_priority("pkg/contest.py")[0] == 0


def test_extraction_cap_keeps_package_code_over_tests(monkeypatch):
    monkeypatch.setattr("api.exhumer.MAX_PY_FILES", 2)
    tar = make_tar({
        "p-1/tests/x.py": b"1",
        "p-1/doc/conf.py": b"2",
        "p-1/pkg/core.py": b"3",
        "p-1/pkg/util.py": b"4",
    })
    assert set(extract_py_files(tar, "p.tar.gz")) == {"pkg/core.py", "pkg/util.py"}
