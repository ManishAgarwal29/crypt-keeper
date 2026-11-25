"""Declaring setuptools for code that imports pkg_resources, distutils or setuptools (found by the benchmark)."""
from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from coroner.autopsy import run_autopsy
from models import PackageRef, PackageSnapshot, Source
from necromancer.export import build_report
from necromancer.revive import bump_dependencies, revive, revived_snapshot
from necromancer.setuptools_dep import declare_setuptools, setuptools_need
from proof.discovery import is_runtime_source

TODAY = date(2026, 11, 20)


def snap(files: dict[str, str], deps: list[str] | None = None) -> PackageSnapshot:
    return PackageSnapshot(ref=PackageRef(source=Source.pypi, identifier="p"), source_map=files, dependencies=deps or [])


def revived(files, deps=None, target="3.12"):
    s = snap(files, deps)
    return s, revive(s, run_autopsy(s, target, TODAY))


# --- what needs setuptools ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("source, expected", [
    ("import pkg_resources\n", "setuptools<81"),
    ("from pkg_resources import resource_filename\n", "setuptools<81"),
    ("import pkg_resources.extern\n", "setuptools<81"),
    ("from distutils.core import setup\n", "setuptools"),
    ("import distutils.version\n", "setuptools"),
    ("import setuptools\n", "setuptools"),
    ("from setuptools.command.build_ext import build_ext\n", "setuptools"),
    ("import pkg_resources\nfrom distutils.core import setup\n", "setuptools<81"),     # the stricter one wins
    ("import os\nimport sys\n", None),
    ("import mypkg_resources\nimport distutils2\n", None),                              # only the exact top-level name counts
    ("from . import pkg_resources\n", None),                                            # a sibling module, not the library
    ("x = 'import pkg_resources'\n# import distutils\n", None),                         # text is not an import
])
def test_requirement_for_a_source(source, expected):
    assert setuptools_need({"p/__init__.py": source})[0] == expected


def test_an_import_inside_a_try_except_importerror_is_a_deliberate_fallback_and_is_left_alone():
    source = "try:\n    import pkg_resources\nexcept ImportError:\n    pkg_resources = None\n"
    assert setuptools_need({"p/__init__.py": source}) == (None, [])


def test_the_fallback_in_an_except_importerror_handler_is_real_code_and_counts():
    """The shape found in oauth2: an optional import in the try body, `distutils` as the fallback that actually runs."""
    source = "try:\n    from pyutil.version_class import Version\nexcept (ImportError, ValueError):\n    from distutils.version import LooseVersion\n"
    assert setuptools_need({"p/_version.py": source}) == ("setuptools", ["p/_version.py:4 imports distutils"])


def test_the_usual_distutils_then_setuptools_fallback_declares_setuptools_because_the_handler_runs_on_312():
    source = "try:\n    from distutils.core import setup\nexcept ImportError:\n    from setuptools import setup\n"
    assert setuptools_need({"p/__init__.py": source}) == ("setuptools", ["p/__init__.py:4 imports setuptools"])


@pytest.mark.parametrize("source, expected", [
    ("__import__('pkg_resources').declare_namespace(__name__)\n", "setuptools<81"),     # the shape found in Flask-Themes
    ("import importlib\nimportlib.import_module('pkg_resources')\n", "setuptools<81"),
    ("from importlib import import_module\nimport_module('distutils.version')\n", "setuptools"),
    ("__import__('os')\n", None),
    ("name = 'pkg_resources'\n__import__(name)\n", None),                              # a computed name cannot be known
    ("__import__()\n", None),
    ("__import__(1)\n", None),
    ("obj.other('pkg_resources')\n", None),
])
def test_a_dynamic_import_by_a_literal_name_counts(source, expected):
    assert setuptools_need({"p/__init__.py": source})[0] == expected


def test_a_dynamic_import_in_a_guarded_try_body_is_still_a_deliberate_fallback():
    source = "try:\n    __import__('pkg_resources')\nexcept ImportError:\n    pass\n"
    assert setuptools_need({"p/__init__.py": source}) == (None, [])


def test_an_unguarded_import_next_to_a_guarded_one_still_counts():
    source = "try:\n    import pkg_resources\nexcept ImportError:\n    pass\nfrom distutils.core import setup\n"
    assert setuptools_need({"p/__init__.py": source})[0] == "setuptools"


@pytest.mark.parametrize("path", ["setup.py", "tests/test_x.py", "p/test_x.py", "p/x_test.py", "conftest.py", "docs/conf.py", "examples/demo.py", "scripts/run.py"])
def test_files_that_do_not_run_when_the_package_is_imported_do_not_count(path):
    assert setuptools_need({path: "import pkg_resources\n"}) == (None, [])
    assert not is_runtime_source(path)


def test_the_reason_names_the_file_and_line():
    need, reasons = setuptools_need({"p/a.py": "x = 1\nimport pkg_resources\n", "p/b.py": "from distutils import core\n"})
    assert need == "setuptools<81" and reasons == ["p/a.py:2 imports pkg_resources", "p/b.py:1 imports distutils"]


@given(st.text(max_size=200))
def test_any_source_is_handled_without_raising(text):
    need, reasons = setuptools_need({"p/__init__.py": text})
    assert (need is None) == (reasons == [])


# --- declaring it ---------------------------------------------------------------------------------------------------------


def test_it_is_appended_when_the_package_does_not_name_setuptools():
    deps, applied, declared = declare_setuptools(["six>=1"], {"p/__init__.py": "import pkg_resources\n"})
    assert deps == ["six>=1", "setuptools<81"] and declared and "declared `setuptools<81`" in applied[0]


def test_nothing_is_added_when_nothing_needs_it():
    assert declare_setuptools(["six"], {"p/__init__.py": "x = 1\n"}) == (["six"], [], False)


@pytest.mark.parametrize("existing, need_source, expected", [
    ("setuptools", "from distutils import core\n", "setuptools"),                      # already declared: untouched
    ("setuptools>=40", "from distutils import core\n", "setuptools>=40"),
    ("setuptools>=40", "import pkg_resources\n", "setuptools<81,>=40"),               # open-ended: capped so pkg_resources exists
    ("setuptools", "import pkg_resources\n", "setuptools<81"),
    ("setuptools<60", "import pkg_resources\n", "setuptools<60"),                      # the package capped it itself
    ("setuptools==57.0", "import pkg_resources\n", "setuptools==57.0"),
    ("Setuptools_Scm>=1", "import pkg_resources\n", None),                              # a different project: still add ours
])
def test_an_existing_requirement_is_kept_and_capped_only_when_that_is_needed(existing, need_source, expected):
    deps, _, declared = declare_setuptools([existing], {"p/__init__.py": need_source})
    assert declared
    if expected is None:
        assert deps[0] == existing and deps[-1].startswith("setuptools")
    else:
        assert deps == [expected]


def test_the_original_dependencies_keep_their_positions():
    deps, _, _ = declare_setuptools(["a", "b", "c"], {"p/__init__.py": "import pkg_resources\n"})
    assert deps[:3] == ["a", "b", "c"]


# --- in the revival -------------------------------------------------------------------------------------------------------------


def test_the_revival_declares_it_and_says_why():
    _, r = revived({"p/__init__.py": "import pkg_resources\n"}, ["six==1.0"])
    assert r.dependencies == ["six>=1.0", "setuptools<81"] and any("declared `setuptools<81`" in a for a in r.applied)


def test_distutils_stops_being_a_manual_item_once_setuptools_provides_it():
    _, r = revived({"p/__init__.py": "from distutils.core import setup\n"})
    assert r.dependencies == ["setuptools"] and r.manual_items == []


@pytest.mark.parametrize("path", ["setup.py", "tests/test_x.py", "docs/conf.py"])
def test_distutils_in_a_file_that_does_not_run_on_import_is_still_reported_and_declares_nothing(path):
    """Nothing declares setuptools for it, so nothing may hide the finding."""
    _, r = revived({path: "from distutils.core import setup\n", "p/__init__.py": "x = 1\n"})
    assert r.dependencies == [] and len(r.manual_items) == 1 and "distutils" in r.manual_items[0]


def test_distutils_stays_manual_where_setuptools_is_not_declared():
    """Targeting 3.11: distutils is still in the standard library, so there is nothing to declare and nothing flagged."""
    _, r = revived({"p/__init__.py": "from distutils.core import setup\n"}, target="3.11")
    assert r.dependencies == [] and r.manual_items == []


def test_it_is_only_declared_for_a_target_where_a_fresh_environment_lacks_setuptools():
    _, r311 = revived({"p/__init__.py": "import pkg_resources\n"}, target="3.11")
    _, r312 = revived({"p/__init__.py": "import pkg_resources\n"}, target="3.12")
    assert r311.dependencies == [] and r312.dependencies == ["setuptools<81"]


def test_other_removed_apis_are_still_reported_when_setuptools_is_declared():
    _, r = revived({"p/__init__.py": "from distutils.core import setup\nimport imp\n"})
    assert len(r.manual_items) == 1 and "imp" in r.manual_items[0] and "distutils" not in r.manual_items[0]


def test_reviving_a_revived_package_changes_nothing():
    s, first = revived({"p/__init__.py": "import pkg_resources\nfrom distutils.core import setup\n"}, ["six==1.0"])
    again_snapshot = revived_snapshot(s, first)
    again = revive(again_snapshot, run_autopsy(again_snapshot, "3.12", TODAY))
    assert again.dependencies == first.dependencies and again.applied == [] and again.manual_items == []


def test_the_generic_bump_does_not_strip_the_cap_that_makes_pkg_resources_exist():
    assert bump_dependencies(["setuptools<81"], "3.12")[0] == ["setuptools<81"]
    assert bump_dependencies(["setuptools<60"], "3.12")[0] == ["setuptools"], "any other cap is still removed"


def test_the_added_requirement_is_in_the_export_report():
    s, r = revived({"p/__init__.py": "import pkg_resources\n"}, ["six==1.0"])
    from models import AutopsyReport, Liveness

    text = build_report(s, AutopsyReport(liveness=Liveness.dead, target_python="3.12"), r)
    assert "setuptools<81" in text, "an added dependency must not vanish from the report"


def test_the_revived_snapshot_carries_the_new_dependency_to_the_sandbox():
    s, r = revived({"p/__init__.py": "import pkg_resources\n"})
    assert revived_snapshot(s, r).dependencies == ["setuptools<81"]
