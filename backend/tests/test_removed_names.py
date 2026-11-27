"""Capping a dependency below the release that removed a name the code uses. Pure: no network."""
import pytest
from hypothesis import given
from hypothesis import strategies as st
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from models import AutopsyReport, Liveness, PackageRef, PackageSnapshot
from necromancer.removed_names import REMOVALS, cap_removed_names, needed_caps, removed_name_uses
from necromancer.revive import bump_dependencies, revive, revived_snapshot


def caps(deps):
    """{canonical name: the requirement as parsed} for easy assertions."""
    return {canonicalize_name(Requirement(d).name): Requirement(d) for d in deps}


def uses(src, path="pkg/mod.py"):
    return [(r.module, text) for r, _, _, text in removed_name_uses({path: src})]


# --- the table ----------------------------------------------------------------------------------------------------


def test_every_entry_is_well_formed():
    for r in REMOVALS:
        assert r.pins and r.what and r.module
        for pin in r.pins:
            req = Requirement(pin)
            specs = list(req.specifier)
            assert len(specs) == 1 and specs[0].operator == "<", f"{pin}: a ceiling is a single '<'"


# --- finding the uses ---------------------------------------------------------------------------------------------


def test_from_import_of_a_removed_name():
    assert uses("from jinja2 import evalcontextfilter\n") == [("jinja2", "from jinja2 import evalcontextfilter")]


def test_the_private_flask_context_stacks_are_a_use():
    assert uses("from flask import _request_ctx_stack\n") == [("flask", "from flask import _request_ctx_stack")]
    out, _, _ = cap_removed_names([], {"a.py": "from flask import _app_ctx_stack\n"})
    assert {canonicalize_name(Requirement(d).name) for d in out} == {"flask", "werkzeug"}


def test_an_import_of_a_name_that_still_exists_is_not_a_use():
    assert uses("from jinja2 import Environment, FileSystemLoader\n") == []


def test_attribute_use_through_an_import_and_through_an_alias():
    assert uses("import jinja2\nx = jinja2.Markup('a')\n") == [("jinja2", "jinja2.Markup")]
    assert uses("import jinja2 as j\nx = j.escape('a')\n") == [("jinja2", "jinja2.escape")]


def test_dotted_module_import_resolves_the_attribute():
    assert uses("import werkzeug.routing\nwerkzeug.routing.parse_rule('/x')\n") == [
        ("werkzeug.routing", "werkzeug.routing.parse_rule")]


def test_a_removed_module_is_found_by_prefix():
    assert uses("from flask.ext.sqlalchemy import SQLAlchemy\n") == [("flask.ext", "from flask.ext.sqlalchemy import ...")]
    assert uses("import flask.ext.script\n") == [("flask.ext", "import flask.ext.script")]
    assert uses("from werkzeug.contrib.cache import SimpleCache\n") == [("werkzeug.contrib", "from werkzeug.contrib.cache import ...")]


def test_an_import_guarded_by_try_except_importerror_is_left_alone():
    src = "try:\n    from jinja2 import Markup\nexcept ImportError:\n    from markupsafe import Markup\n"
    assert uses(src) == []


def test_tests_setup_and_docs_do_not_count():
    src = "from jinja2 import Markup\n"
    for path in ("tests/test_x.py", "setup.py", "docs/conf.py", "examples/demo.py"):
        assert removed_name_uses({path: src}) == [], path


def test_code_that_does_not_parse_is_skipped_not_fatal():
    assert removed_name_uses({"a.py": "print 'py2'\n", "b.py": "from jinja2 import Markup\n"}) != []


def test_one_use_per_entry_and_file():
    src = "from jinja2 import Markup\nfrom jinja2 import escape\nimport jinja2\njinja2.contextfilter\n"
    assert len(removed_name_uses({"a.py": src})) == 1


# --- capping ------------------------------------------------------------------------------------------------------


SRC = {"pkg/mod.py": "from jinja2 import evalcontextfilter\n"}


def test_an_undeclared_dependency_is_added_with_its_ceiling():
    out, applied, manual = cap_removed_names(["flask"], SRC)
    assert out == ["flask", "Jinja2<3.1"]
    assert len(applied) == 1 and "pkg/mod.py:1" in applied[0] and "evalcontextfilter" in applied[0]
    assert manual == []


def test_an_existing_requirement_gets_the_cap_merged_in_and_keeps_its_floor():
    out, applied, _ = cap_removed_names(["Jinja2>=2.4"], SRC)
    req = Requirement(out[0])
    assert {(s.operator, s.version) for s in req.specifier} == {(">=", "2.4"), ("<", "3.1")}
    assert applied and "capped" in applied[0]


def test_a_cap_at_least_as_strict_is_left_alone():
    assert cap_removed_names(["Jinja2<3.0"], SRC) == (["Jinja2<3.0"], [], [])
    assert cap_removed_names(["Jinja2<3.1"], SRC) == (["Jinja2<3.1"], [], [])


def test_a_floor_at_or_above_the_ceiling_is_reported_not_changed():
    out, applied, manual = cap_removed_names(["Jinja2>=3.2"], SRC)
    assert out == ["Jinja2>=3.2"] and applied == []
    assert manual and "reconcile" in manual[0]


def test_the_strictest_ceiling_wins_when_two_uses_disagree():
    src = {"a.py": "from werkzeug import cached_property\nfrom werkzeug.routing import parse_rule\n"}
    out, _, _ = cap_removed_names([], src)
    assert caps(out)["werkzeug"].specifier == Requirement("Werkzeug<1.0").specifier   # <1.0 beats <2.2


def test_the_old_flask_set_is_added_together():
    out, applied, _ = cap_removed_names(["Flask"], {"a.py": "from flask.ext.sqlalchemy import SQLAlchemy\n"})
    c = caps(out)
    for name, bound in (("flask", "1.0"), ("werkzeug", "1.0"), ("jinja2", "3.1"), ("itsdangerous", "2.1")):
        assert {(s.operator, s.version) for s in c[name].specifier} == {("<", bound)}, name
    assert len(applied) == 4


def test_no_use_means_no_change():
    assert cap_removed_names(["a", "b>=1"], {"x.py": "import os\n"}) == (["a", "b>=1"], [], [])


def test_existing_requirements_keep_their_order_and_text():
    deps = ["zeta>=1", "Jinja2", "alpha==2"]
    out, _, _ = cap_removed_names(deps, SRC)
    assert out[0] == "zeta>=1" and out[2] == "alpha==2"


@given(st.sets(st.sampled_from([
    "from jinja2 import Markup\n", "from werkzeug import cached_property\n", "from paramiko import DSSKey\n",
    "from flask.ext import foo\n", "from itsdangerous import TimedJSONWebSignatureSerializer\n", "import os\n",
])))
def test_capping_twice_changes_nothing_the_second_time(snippets):
    src = {"pkg/m.py": "".join(sorted(snippets))}
    once, _, _ = cap_removed_names(["Flask"], src)
    twice, applied, manual = cap_removed_names(once, src)
    assert twice == once and applied == [] and manual == []


# --- keeping caps through bump_dependencies, and a whole revival --------------------------------------------------


def test_bump_keeps_a_cap_the_code_needs_but_strips_one_it_does_not():
    keep = needed_caps(SRC)
    assert bump_dependencies(["Jinja2<3.1"], "3.12", keep_caps=keep)[0] == ["Jinja2<3.1"]
    assert bump_dependencies(["Jinja2<3.1"], "3.12")[0] == ["Jinja2"]          # nothing asks to keep it
    assert bump_dependencies(["Flask<1.0"], "3.12", keep_caps=keep)[0] == ["Flask"]  # a different cap is not kept


def _snapshot(source, deps=()):
    return PackageSnapshot(ref=PackageRef(source="pypi", identifier="demo"), dependencies=list(deps),
                           source_map={"demo/__init__.py": source})


def test_a_whole_revival_adds_the_cap_and_a_second_revival_changes_nothing():
    report = AutopsyReport(liveness=Liveness.dead, target_python="3.12")
    snap = _snapshot("from jinja2 import evalcontextfilter\n", ["flask"])
    first = revive(snap, report)
    assert "Jinja2<3.1" in first.dependencies
    again = revive(revived_snapshot(snap, first), report)
    assert again.dependencies == first.dependencies
    assert again.diffs == [] and again.applied == []


def test_a_package_that_declared_its_own_cap_but_does_not_need_it_still_loses_it():
    report = AutopsyReport(liveness=Liveness.dead, target_python="3.12")
    out = revive(_snapshot("import os\n", ["Jinja2<3.1"]), report)
    assert out.dependencies == ["Jinja2"]
