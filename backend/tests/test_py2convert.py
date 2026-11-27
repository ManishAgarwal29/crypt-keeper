"""Python 2 conversion with lib2to3. Pure: nothing is written to disk and nothing is run."""
import pytest
from hypothesis import given
from hypothesis import strategies as st

from api.main import convert_python2_enabled
from models import AutopsyReport, Liveness, PackageRef, PackageSnapshot
from necromancer import py2convert
from necromancer.py2convert import convert_python2, expand_indent_tabs, parses, python2_paths
from necromancer.revive import revive, revived_snapshot

PY2_PRINT = "print 'hello'\n"
PY2_EXCEPT = "try:\n    pass\nexcept ValueError, e:\n    print e\n"


# --- detection ----------------------------------------------------------------------------------------------------


def test_python2_paths_lists_only_files_that_do_not_parse():
    files = {"a.py": PY2_PRINT, "b.py": "x = 1\n", "c.txt": "print 'not python'", "d.py": "def f(:\n"}
    assert python2_paths(files) == ["a.py", "d.py"]


def test_parses_ignores_warnings_and_rejects_null_bytes():
    assert parses("x = '\\d'\n")                 # an invalid escape is a warning, not a failure
    assert not parses("x = 1\x00")


# --- the guarantees -----------------------------------------------------------------------------------------------


def test_a_python3_package_is_never_touched():
    files = {"pkg/__init__.py": "", "pkg/a.py": "print('hi')\nd = {}\nfor k in d.keys():\n    pass\n"}
    assert convert_python2(files) == ({}, [], [])


@pytest.mark.parametrize("path", ["tests/test_old.py", "test_old.py", "setup.py", "docs/conf.py", "examples/demo.py"])
def test_python2_outside_runtime_code_does_not_make_a_package_python2(path):
    # a working package with a stray Python 2 test or example must not be rewritten: the benchmark showed that
    # converting such a package changed working modules and made some of its tests fail
    files = {"pkg/__init__.py": "", "pkg/mod.py": "x = 1\nprint(x)\n", path: PY2_PRINT}
    assert convert_python2(files) == ({}, [], [])


def test_once_the_runtime_code_is_python2_the_tests_are_converted_too():
    files = {"pkg/__init__.py": "", "pkg/mod.py": PY2_PRINT, "tests/test_mod.py": "print 'in a test'\n"}
    converted, applied, _ = convert_python2(files)
    assert set(converted) == {"pkg/mod.py", "tests/test_mod.py"}
    assert "converted 2 files" in applied[0]


def test_a_package_with_one_unreadable_file_is_not_a_python2_package():
    files = {"a.py": "def f(:\n", "b.py": "x = dict().keys()\n"}       # nothing lib2to3 can make parse
    assert convert_python2(files) == ({}, [], [])


def test_print_and_except_are_converted_and_the_result_parses():
    converted, applied, manual = convert_python2({"m.py": PY2_PRINT, "n.py": PY2_EXCEPT})
    assert converted["m.py"] == "print('hello')\n"
    assert "except ValueError as e" in converted["n.py"] and "print(e)" in converted["n.py"]
    assert all(parses(t) for t in converted.values())
    assert len(applied) == 1 and "converted 2 files" in applied[0] and "2 of the 2" in applied[0]
    assert any("mechanical" in m for m in manual)


def test_a_file_without_python2_idioms_is_not_in_the_result():
    converted, _, _ = convert_python2({"old.py": PY2_PRINT, "plain.py": "x = 1\n"})
    assert "plain.py" not in converted and "old.py" in converted


def test_comments_survive_conversion():
    converted, _, _ = convert_python2({"m.py": "# keep this comment\nprint 'a'  # and this one\n"})
    assert converted["m.py"] == "# keep this comment\nprint('a')  # and this one\n"


def test_a_missing_final_newline_is_not_added():
    converted, _, _ = convert_python2({"m.py": "print 'a'"})
    assert converted["m.py"] == "print('a')"


# --- implicit relative imports, read from the snapshot rather than the disk -----------------------------------------


def test_a_sibling_module_becomes_a_relative_import():
    files = {"pkg/__init__.py": "", "pkg/a.py": "import b\nimport os\n", "pkg/b.py": PY2_PRINT}
    converted, _, _ = convert_python2(files)
    assert "from . import b" in converted["pkg/a.py"]
    assert "import os" in converted["pkg/a.py"]               # a standard library module is left alone


def test_from_import_of_a_sibling_becomes_relative():
    files = {"pkg/__init__.py": "", "pkg/a.py": "from b import thing\n", "pkg/b.py": "thing = 1\nprint 'x'\n"}
    assert "from .b import thing" in convert_python2(files)[0]["pkg/a.py"]


def test_a_module_outside_a_package_is_not_made_relative():
    files = {"a.py": "import b\nprint 'x'\n", "b.py": "x = 1\n"}               # no __init__.py: not a package
    assert "import b" in convert_python2(files)[0]["a.py"] and "from . import" not in convert_python2(files)[0]["a.py"]


def test_a_module_that_is_not_a_sibling_is_left_alone():
    files = {"pkg/__init__.py": "", "pkg/a.py": "import elsewhere\nprint 'x'\n"}
    assert "from . import" not in convert_python2(files)[0]["pkg/a.py"]


# --- tabs ---------------------------------------------------------------------------------------------------------


def test_python2_tab_indentation_is_expanded_to_eight_columns():
    assert expand_indent_tabs("if x:\n\tpass\n") == "if x:\n        pass\n"
    assert expand_indent_tabs("if x:\n  \tpass\n") == "if x:\n        pass\n"       # two spaces then a tab -> column 8


def test_a_file_that_mixes_tabs_and_spaces_is_converted():
    mixed = "def f():\n        if True:\n\t    print 'x'\n"       # a tab then 4 spaces is column 12 in Python 2; Python 3 raises TabError
    assert not parses(mixed)
    converted, _, _ = convert_python2({"m.py": mixed})
    assert parses(converted["m.py"]) and "print('x')" in converted["m.py"]


# --- limits and degradation ---------------------------------------------------------------------------------------


def test_a_conversion_that_parses_but_does_not_compile_is_rejected():
    # pdfrw binds builtins as defaults. lib2to3 renames `unicode` to `str`, giving `def f(str=str, str=str)`, which
    # parses but fails to compile ("duplicate argument"). The original must be kept.
    bind = "def f(str=str, unicode=unicode):\n    return str\n"
    converted, _, manual = convert_python2({"old.py": PY2_PRINT, "bind.py": bind})
    assert "old.py" in converted and "bind.py" not in converted
    assert not any("duplicate" in t for t in converted.values())
    assert all(py2convert.compiles(t) for t in converted.values())


def test_compiles_is_stricter_than_parses():
    assert parses("def f(a, a):\n    pass\n") and not py2convert.compiles("def f(a, a):\n    pass\n")
    assert py2convert.compiles("x = 1\n") and not py2convert.compiles("x = (\n")


def test_a_file_that_cannot_be_fixed_is_kept_and_reported():
    files = {"ok.py": PY2_PRINT, "stuck.py": "True = 1\n"}       # assigning to True is a SyntaxError in Python 3
    converted, applied, manual = convert_python2(files)
    assert "stuck.py" not in converted and "ok.py" in converted
    assert any("Still Python 2 after conversion" in m and "stuck.py" in m for m in manual)
    assert "1 of the 2" in applied[0]


def test_a_very_large_file_is_left_and_reported(monkeypatch):
    monkeypatch.setattr(py2convert, "MAX_FILE_BYTES", 20)
    files = {"small.py": PY2_PRINT, "big.py": "print 'x'\n" + "x = 1\n" * 10}
    converted, _, manual = convert_python2(files)
    assert "small.py" in converted and "big.py" not in converted
    assert any("Too large to convert" in m and "big.py" in m for m in manual)


def test_without_lib2to3_nothing_is_converted_and_the_reason_is_given(monkeypatch):
    monkeypatch.setattr(py2convert, "_tool", lambda paths: None)
    converted, applied, manual = convert_python2({"m.py": PY2_PRINT})
    assert converted == {} and applied == []
    assert manual and "lib2to3" in manual[0]


# --- properties ---------------------------------------------------------------------------------------------------


SNIPPETS = [PY2_PRINT, PY2_EXCEPT, "x = raw_input('?')\n", "d = {}\nif d.has_key('a'):\n    pass\n",
            "import urllib2\n", "def f(a, (b, c)):\n    return a\n", "s = u'text'\nn = 10L\n", "print >>f, 'x'\n",
            "x = 0777\n", "for k, v in d.iteritems():\n    print k\n"]


@given(st.sets(st.sampled_from(range(len(SNIPPETS)))))
def test_every_converted_file_parses_and_converting_again_changes_nothing(chosen):
    files = {f"pkg/m{i}.py": SNIPPETS[i] for i in sorted(chosen)}
    converted, _, _ = convert_python2(files)
    assert all(parses(t) for t in converted.values())
    merged = {**files, **converted}
    assert convert_python2(merged) == ({}, [], [])           # once converted, it is Python 3 and left alone


# --- inside a whole revival ---------------------------------------------------------------------------------------


def _snap(files, deps=()):
    return PackageSnapshot(ref=PackageRef(source="pypi", identifier="demo"), dependencies=list(deps), source_map=files)


REPORT = AutopsyReport(liveness=Liveness.dead, target_python="3.12")


def test_revive_converts_and_diffs_against_the_original():
    snap = _snap({"demo/__init__.py": "", "demo/a.py": "import b\nprint 'x'\n", "demo/b.py": "x = 1\n"})
    out = revive(snap, REPORT)
    by_path = {d.path: d for d in out.diffs}
    assert set(by_path) == {"demo/a.py"}
    assert by_path["demo/a.py"].before == "import b\nprint 'x'\n"                # the file the author shipped
    assert "from . import b" in by_path["demo/a.py"].after and "print('x')" in by_path["demo/a.py"].after
    assert any("lib2to3" in a for a in out.applied)


def test_revive_can_turn_conversion_off():
    snap = _snap({"m.py": PY2_PRINT})
    out = revive(snap, REPORT, convert_python2=False)
    assert out.diffs == [] and not any("lib2to3" in a for a in out.applied)


def test_a_revived_python2_package_is_left_alone_by_a_second_revival():
    snap = _snap({"demo/__init__.py": "", "demo/a.py": "import b\nprint 'x'\n", "demo/b.py": "x = 1\n"})
    first = revive(snap, REPORT)
    again = revive(revived_snapshot(snap, first), REPORT)
    assert again.diffs == [] and again.applied == [] and again.dependencies == first.dependencies


def test_conversion_feeds_the_dependency_levers():
    # `import six` is only visible once the file parses; the revival declares it after converting
    snap = _snap({"m.py": "import six\nprint 'x'\n"})
    assert revive(snap, REPORT).dependencies == ["six"]
    assert revive(snap, REPORT, convert_python2=False).dependencies == []


# --- the switch ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [(None, True), ("on", True), ("1", True), ("off", False), ("OFF", False),
                                            ("0", False), ("false", False), ("no", False), (" off ", False)])
def test_the_environment_switch(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("CRYPT_KEEPER_CONVERT_PYTHON2", raising=False)
    else:
        monkeypatch.setenv("CRYPT_KEEPER_CONVERT_PYTHON2", value)
    assert convert_python2_enabled() is expected
