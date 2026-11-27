"""Declaring imports a package forgot to declare. Pure: no network."""
from hypothesis import given
from hypothesis import strategies as st

from models import AutopsyReport, Liveness, PackageRef, PackageSnapshot
from necromancer.declare_imports import DEAD_ON_312, IMPORT_TO_DIST, declare_missing, external_imports
from necromancer.revive import revive, revived_snapshot


def src(text, path="pkg/mod.py"):
    return {path: text}


# --- finding the imports ------------------------------------------------------------------------------------------


def test_finds_third_party_roots_and_where_they_are_first_used():
    found = external_imports({"a.py": "import six\n", "b.py": "import six.moves\nfrom bottle import route\n"})
    assert found == {"six": ("a.py", 1), "bottle": ("b.py", 2)}


def test_standard_library_and_relative_imports_are_ignored():
    assert external_imports(src("import os, sys\nfrom collections import abc\nfrom . import sibling\nfrom .x import y\n")) == {}


def test_a_module_the_package_ships_is_never_missing():
    files = {"pkg/__init__.py": "", "pkg/six.py": "x = 1\n", "pkg/mod.py": "import six\n"}
    assert external_imports(files) == {}
    files = {"pkg/__init__.py": "", "pkg/bottle/__init__.py": "", "pkg/mod.py": "from bottle import route\n"}
    assert external_imports(files) == {}


def test_a_guarded_import_is_a_deliberate_fallback():
    assert external_imports(src("try:\n    import simplejson\nexcept ImportError:\n    import json\n")) == {}


def test_tests_setup_and_docs_do_not_count():
    for path in ("tests/test_x.py", "setup.py", "docs/conf.py", "examples/demo.py"):
        assert external_imports({path: "import six\n"}) == {}, path


def test_code_that_does_not_parse_is_skipped_not_fatal():
    assert external_imports({"a.py": "print 'py2'\n", "b.py": "import six\n"}) == {"six": ("b.py", 1)}


# --- declaring ----------------------------------------------------------------------------------------------------


def test_a_known_undeclared_import_is_declared():
    out, applied, manual = declare_missing([], src("import six\n"))
    assert out == ["six"] and manual == []
    assert len(applied) == 1 and "pkg/mod.py:1" in applied[0] and "`six`" in applied[0]


def test_libraries_that_old_packages_forget_to_declare_are_known():
    # twisted and gevent were imported without being declared by packages in the fourth benchmark list
    out, applied, _ = declare_missing([], src("import gevent\nfrom twisted.internet import reactor\n"))
    assert out == ["gevent", "Twisted"] and len(applied) == 2


def test_the_import_name_is_mapped_to_the_distribution_name():
    out, _, _ = declare_missing([], src("import yaml\nfrom dateutil import parser\nimport bs4\n"))
    assert out == ["beautifulsoup4", "python-dateutil", "PyYAML"]       # sorted by import name: bs4, dateutil, yaml


def test_an_already_declared_distribution_is_not_added_again():
    assert declare_missing(["Six>=1.0"], src("import six\n"))[0] == ["Six>=1.0"]       # case and spelling do not matter
    assert declare_missing(["python_dateutil"], src("import dateutil\n"))[0] == ["python_dateutil"]


def test_an_import_the_table_does_not_know_is_never_guessed():
    out, applied, manual = declare_missing([], src("import some_unknown_library\n"))
    assert (out, applied, manual) == ([], [], [])


def test_a_library_that_is_dead_on_312_is_reported_not_declared():
    assert "nose" in DEAD_ON_312
    out, applied, manual = declare_missing([], src("import nose\n"))
    assert out == [] and applied == []
    assert manual and "nose" in manual[0] and "pkg/mod.py:1" in manual[0]


def test_existing_requirements_keep_their_order_and_new_ones_come_last():
    out, _, _ = declare_missing(["zeta>=1", "alpha"], src("import six\nimport bottle\n"))
    assert out == ["zeta>=1", "alpha", "bottle", "six"]


def test_the_table_only_names_distributions_that_look_like_distributions():
    for root, dist in IMPORT_TO_DIST.items():
        assert root.isidentifier() and dist and " " not in dist and "/" not in dist and ":" not in dist


@given(st.sets(st.sampled_from(["import six\n", "import bottle\n", "import yaml\n", "import nose\n", "import os\n",
                                "import never_heard_of_it\n"])),
       st.lists(st.sampled_from(["zeta>=1", "alpha", "six"]), unique=True, max_size=3))
def test_declaring_twice_changes_nothing_and_never_removes_a_requirement(snippets, deps):
    files = src("".join(sorted(snippets)))
    once, _, _ = declare_missing(list(deps), files)
    assert once[:len(deps)] == list(deps)                                  # nothing removed or reordered
    twice, applied, _ = declare_missing(once, files)
    assert twice == once and applied == []


# --- inside a whole revival ---------------------------------------------------------------------------------------


def test_a_whole_revival_declares_the_import_and_a_second_revival_changes_nothing():
    report = AutopsyReport(liveness=Liveness.dead, target_python="3.12")
    snap = PackageSnapshot(ref=PackageRef(source="pypi", identifier="demo"), dependencies=[],
                           source_map={"demo.py": "import six\nprint(six.PY3)\n"})
    first = revive(snap, report)
    assert first.dependencies == ["six"]
    again = revive(revived_snapshot(snap, first), report)
    assert again.dependencies == ["six"] and again.diffs == [] and again.applied == []
