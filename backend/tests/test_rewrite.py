"""Tests for rewriting deprecated APIs (task 7). Pure; no network."""
import ast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from coroner.autopsy import run_autopsy
from coroner.deprecations import REMOVED_ATTRS, REMOVED_TESTCASE_METHODS
from datetime import date
from models import PackageRef, PackageSnapshot, Source
from necromancer.revive import revive
from necromancer.rewriter import (
    ABC_MOVES, BUILTIN_REPLACEMENTS, RENAMES, TESTCASE_RENAMES, rewrite_deprecated,
)


def rw(src: str, target: str = "3.12", path: str = "m.py"):
    return rewrite_deprecated(src, target, path)


# --- collections ABCs: from-imports ------------------------------------------------------------


@pytest.mark.parametrize("src,expected", [
    ("from collections import Mapping, OrderedDict\n",
     "from collections import OrderedDict\nfrom collections.abc import Mapping\n"),
    ("from collections import Mapping\n", "from collections.abc import Mapping\n"),
    ("from collections import Mapping as M, deque\n",
     "from collections import deque\nfrom collections.abc import Mapping as M\n"),
    ("def f():\n    from collections import Mapping, deque\n    return 1\n",
     "def f():\n    from collections import deque\n    from collections.abc import Mapping\n    return 1\n"),
    ("from collections import Mapping, deque\r\nx = 1\r\n",
     "from collections import deque\r\nfrom collections.abc import Mapping\r\nx = 1\r\n"),
    ("from collections import Mapping  # keep me\n", "from collections.abc import Mapping  # keep me\n"),
    ("from collections import (\n    Mapping,\n    deque,\n)\n",
     "from collections import deque\nfrom collections.abc import Mapping\n"),
    ("from collections import Mapping, Sequence", "from collections.abc import Mapping, Sequence"),
])
def test_from_import_abc_moves(src, expected):
    out, applied, manual = rw(src)
    assert out == expected and manual == [] and len(applied) == 1


def test_from_import_on_a_shared_line_is_left_for_a_human():
    src = "if True: from collections import Mapping\n"
    out, applied, manual = rw(src)
    assert out == src and applied == [] and len(manual) == 1


# --- collections ABCs: attribute uses -------------------------------------------------------------


@pytest.mark.parametrize("src,expected", [
    ("import collections\nx = collections.Mapping\n",
     "import collections\nimport collections.abc\nx = collections.abc.Mapping\n"),
    ("import collections\nimport collections.abc\nx = collections.Mapping\n",
     "import collections\nimport collections.abc\nx = collections.abc.Mapping\n"),
    ("import collections as c\nx = c.Mapping\n",
     "import collections as c\nimport collections.abc\nx = c.abc.Mapping\n"),
    ("import collections  # c\nx = collections.Mapping\n",
     "import collections  # c\nimport collections.abc\nx = collections.abc.Mapping\n"),
    ("import collections\nx = collections.Mapping",
     "import collections\nimport collections.abc\nx = collections.abc.Mapping"),
    ("import collections\r\nx = collections.Mapping\r\n",
     "import collections\r\nimport collections.abc\r\nx = collections.abc.Mapping\r\n"),
    ("import collections\ns = 'é'; x = collections.Mapping\n",
     "import collections\nimport collections.abc\ns = 'é'; x = collections.abc.Mapping\n"),
    ("import collections\nx = isinstance(a, collections.Mapping) and isinstance(b, collections.Sequence)\n",
     "import collections\nimport collections.abc\nx = isinstance(a, collections.abc.Mapping) and isinstance(b, collections.abc.Sequence)\n"),
])
def test_attribute_abc_moves(src, expected):
    out, _, manual = rw(src)
    assert out == expected and manual == []


def test_attribute_use_without_a_safe_place_for_the_import_is_left_alone():
    for src in (
        "def f():\n    import collections\n    return collections.Mapping\n",
        "import os; import collections\nx = collections.Mapping\n",
    ):
        out, applied, manual = rw(src)
        assert out == src and applied == [] and len(manual) == 1, src


# --- same-module renames and built-ins ----------------------------------------------------------------


@pytest.mark.parametrize("src,expected", [
    ("import base64\nx = base64.encodestring(b)\n", "import base64\nx = base64.encodebytes(b)\n"),
    ("import base64 as b64\nx = b64.decodestring(b)\n", "import base64 as b64\nx = b64.decodebytes(b)\n"),
    ("from base64 import encodestring\n", "from base64 import encodebytes as encodestring\n"),
    ("from base64 import encodestring as es\n", "from base64 import encodebytes as es\n"),
    ("import configparser\np = configparser.SafeConfigParser()\n", "import configparser\np = configparser.ConfigParser()\n"),
    ("import sys\nx = sys.maxint\n", "import sys\nx = sys.maxsize\n"),
    ("import itertools\nx = itertools.izip_longest(a)\n", "import itertools\nx = itertools.zip_longest(a)\n"),
    ("import itertools\nx = itertools.izip(a, b)\n", "import itertools\nx = zip(a, b)\n"),
    ("import itertools\nx = list(itertools.imap(f, a))\n", "import itertools\nx = list(map(f, a))\n"),
    ("import itertools as it\nx = it.ifilter(f, a)\n", "import itertools as it\nx = filter(f, a)\n"),
    ("import unittest\nr = unittest._TextTestResult\n", "import unittest\nr = unittest.TextTestResult\n"),
    ("from unittest import _TextTestResult\n", "from unittest import TextTestResult as _TextTestResult\n"),
])
def test_drop_in_renames(src, expected):
    out, _, manual = rw(src)
    assert out == expected and manual == []


def test_testcase_aliases_are_renamed():
    src = "import unittest\nclass T(unittest.TestCase):\n    def t(self):\n        self.assertEquals(1, 1)\n        self.assert_(True)\n"
    out, applied, manual = rw(src)
    assert out == src.replace("assertEquals", "assertEqual").replace("assert_(", "assertTrue(")
    assert manual == [] and len(applied) == 2


# --- things that must not change -------------------------------------------------------------------------


@pytest.mark.parametrize("src", [
    "# collections.Mapping\nx = 'collections.Mapping'\n",
    "x = collections.Mapping\n",
    "import collections.abc\nx = collections.abc.Mapping\n",
    "try:\n    from collections import Mapping\nexcept ImportError:\n    from collections.abc import Mapping\n",
    "class T:\n    def t(self):\n        self.assertEquals(1, 1)\n",
    "x = foo.encodestring(b)\n",
    "import base64\nx = other.encodestring(b)\n",
    "",
])
def test_unrelated_code_is_unchanged(src):
    assert rw(src) == (src, [], [])


@pytest.mark.parametrize("src,target", [
    ("import collections\nx = collections.Mapping\n", "3.9"),
    ("import unittest\nclass T(unittest.TestCase):\n    def t(self):\n        self.assertEquals(1, 1)\n", "3.11"),
    ("from collections import Mapping\n", "3.9"),
])
def test_nothing_is_rewritten_before_the_removal_version(src, target):
    assert rw(src, target) == (src, [], [])


# --- manual items --------------------------------------------------------------------------------------------


def test_api_without_a_safe_rewrite_is_reported_as_manual_with_location():
    out, applied, manual = rw("import imp\n")
    assert out == "import imp\n" and applied == []
    assert manual == ["m.py:1: `imp` was removed in Python 3.12; use importlib."]


def test_syntax_errors_are_reported_and_the_file_is_untouched():
    src = "print 'hi'\nimport collections\nx = collections.Mapping\n"
    out, applied, manual = rw(src)
    assert out == src and applied == [] and "Does not parse as Python 3" in manual[0]


def test_rewritten_and_manual_items_are_split_correctly():
    src = "import imp\nimport collections\nx = collections.Mapping\n"
    out, applied, manual = rw(src)
    assert "collections.abc.Mapping" in out and "import imp" in out
    assert len(applied) == 2 and len(manual) == 1 and "`imp`" in manual[0]


def test_applied_entries_carry_path_and_line():
    _, applied, _ = rw("import collections\nx = collections.Mapping\n", path="pkg/a.py")
    assert "pkg/a.py:2: `collections.Mapping` -> `collections.abc.Mapping`" in applied
    assert any(a.startswith("pkg/a.py:1: added `import collections.abc`") for a in applied)


# --- revive() integration -----------------------------------------------------------------------------------------


def test_revive_produces_diffs_applied_and_manual_items():
    snap = PackageSnapshot(
        ref=PackageRef(source=Source.pypi, identifier="p"),
        last_release_date="2015-01-01",
        source_map={
            "a.py": "import collections\nx = collections.Mapping\n",
            "b.py": "import imp\n",
            "c.py": "x = 1\n",
        },
    )
    result = revive(snap, run_autopsy(snap, "3.12", date(2026, 1, 1)))
    assert [d.path for d in result.diffs] == ["a.py"]
    assert "collections.abc.Mapping" in result.diffs[0].after
    assert any("a.py:2" in a for a in result.applied)
    assert result.manual_items == ["b.py:1: `imp` was removed in Python 3.12; use importlib."]


# --- table consistency -----------------------------------------------------------------------------------------------


def test_rewrite_tables_only_name_apis_the_coroner_flags():
    for key in [*RENAMES, *BUILTIN_REPLACEMENTS, *ABC_MOVES]:
        assert key in REMOVED_ATTRS, key
    assert set(TESTCASE_RENAMES) <= set(REMOVED_TESTCASE_METHODS)


def test_no_api_is_in_two_rewrite_tables():
    names = [*RENAMES, *BUILTIN_REPLACEMENTS, *ABC_MOVES]
    assert len(names) == len(set(names))


def test_renames_actually_rename():
    for old, new in RENAMES.items():
        assert old.rsplit(".", 1)[1] != new
    for old, new in TESTCASE_RENAMES.items():
        assert old != new


@pytest.mark.parametrize("dotted", sorted([*RENAMES, *BUILTIN_REPLACEMENTS]))
def test_every_table_entry_rewrites_cleanly(dotted):
    module, name = dotted.rsplit(".", 1)
    src = f"import {module}\nx = {dotted}\n"
    out, applied, manual = rw(src)
    assert out != src and len(applied) == 1 and manual == []
    ast.parse(out)
    assert rw(out) == (out, [], [])


# --- property-based -------------------------------------------------------------------------------------------------------------

POOL = [
    "import collections\nA = collections.Mapping\n",
    "from collections import Mapping, deque\n",
    "import base64\nB = base64.encodestring(b'')\n",
    "from base64 import decodestring\n",
    "import itertools\nC = itertools.izip([1], [2])\n",
    "import sys\nD = sys.maxint\n",
    "import unittest\nclass T(unittest.TestCase):\n    def t(self):\n        self.assertEquals(1, 1)\n",
    "import configparser\nP = configparser.SafeConfigParser\n",
]
NEUTRAL = ["# collections.Mapping\n", "x = 'collections.Mapping'\n", "def f():\n    pass\n", "\n"]


@given(
    st.lists(st.sampled_from(POOL + NEUTRAL), max_size=10).map("".join),
    st.sampled_from(["\n", "\r\n"]),
)
def test_rewriting_pooled_programs_is_valid_complete_and_idempotent(program, newline):
    program = program.replace("\n", newline)
    out, _, manual = rw(program)
    ast.parse(out)
    assert manual == []
    assert rw(out) == (out, [], [])
    assert ("\r\n" in out) == ("\r\n" in program)


@given(st.lists(st.sampled_from(POOL + ["import imp\n", "import distutils.core\n"]), max_size=8).map("".join))
def test_unfixable_items_survive_rewriting_as_manual(program):
    out, _, manual = rw(program)
    ast.parse(out)
    assert len([m for m in manual if "`imp`" in m]) == program.count("import imp\n")


@given(st.text(max_size=200), st.sampled_from(["3.8", "3.12", "3.13", "bad"]))
def test_rewrite_never_raises_on_arbitrary_text(src, target):
    out, applied, manual = rw(src, target)
    assert isinstance(out, str) and out == rw(out, target)[0]
