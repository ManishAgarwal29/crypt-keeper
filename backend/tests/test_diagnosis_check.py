"""The diagnosis check's matching rule and report. No network: findings here are made up."""
import importlib.util
import sys
from collections import Counter
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("diagnosis_check_script", ROOT / "scripts" / "diagnosis_check.py")
dc = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = dc   # @dataclass looks its module up here; a file-path load does not register it
spec.loader.exec_module(dc)


def finding(kind, message="", location=None):
    return {"kind": kind, "message": message, "location": location}


def truth(name):
    return next(t for t in dc.TRUTH if t.name == name)


# --- the ground truth ---------------------------------------------------------------------------------------------


def test_truth_has_the_19_packages_the_benchmark_notes_describe():
    assert len(dc.TRUTH) == 19
    assert len({t.name for t in dc.TRUTH}) == 19, "a package listed twice would be counted twice"
    counts = Counter(t.cause for t in dc.TRUTH)
    # benchmark/notes.md: 8 Python 2, 5 dependency removed a name, 4 undeclared import, 2 install failure
    assert counts == {"python2": 8, "dependency_removed": 5, "undeclared_import": 4, "install_failure": 2}


def test_every_cause_but_python2_names_what_to_look_for():
    for t in dc.TRUTH:
        assert t.cause in dc.CAUSE_LABELS
        assert t.observed, f"{t.name} needs the failure it was labelled from"
        if t.cause != "python2":
            assert t.culprits, f"{t.name} has no culprit to match against"


# --- whole-name matching ------------------------------------------------------------------------------------------


def test_mentions_matches_whole_names_only():
    assert dc.mentions("'nose' is unmaintained", "nose")
    assert dc.mentions("depends on Jinja2>=2", "jinja2")          # case-insensitive
    assert dc.mentions("`flask.ext` was removed", "flask.ext")
    assert not dc.mentions("a diagnose step", "nose")              # inside a longer word
    assert not dc.mentions("version sixteen", "six")
    assert not dc.mentions("see flask.extension", "flask.ext")


# --- the rule -----------------------------------------------------------------------------------------------------


def test_python2_is_named_by_a_syntax_error_finding():
    t = truth("webapp2")
    assert dc.names_cause(t, [finding("syntax-error", "Does not parse as Python 3")])
    assert not dc.names_cause(t, [finding("deprecated-api", "`collections.Mapping` was removed")])
    assert not dc.names_cause(t, [])


def test_dependency_cause_needs_the_dependency_named_in_the_message():
    t = truth("Flask-Triangle")                                    # culprit: jinja2
    assert dc.names_cause(t, [finding("incompatible-dependency", "jinja2 3.1 removed evalcontextfilter")])
    # a real but different problem does not count
    assert not dc.names_cause(t, [finding("deprecated-api", "`collections.Mapping` was removed in Python 3.10")])


def test_a_file_path_does_not_count_as_naming_the_cause():
    t = truth("bottle-sqlite")                                     # culprit: bottle
    f = finding("deprecated-api", "`inspect.getargspec` was removed in Python 3.11", location="bottle_sqlite.py:114")
    assert not dc.names_cause(t, [f])


def test_the_age_finding_never_counts():
    t = truth("Flask-MongoAlchemy")                                # culprit: mongoalchemy
    assert not dc.names_cause(t, [finding("abandoned-package", "MongoAlchemy has had no release since 2015")])
    assert dc.names_cause(t, [finding("pinned-dependency", "'MongoAlchemy' is hard-pinned to 0.19")])


def test_any_of_several_culprits_is_enough():
    t = truth("Pylons")                                            # culprits: webhelpers, weberror
    assert dc.names_cause(t, [finding("unmaintained-dependency", "'WebError' has had no release since 2014")])
    assert dc.names_cause(t, [finding("unmaintained-dependency", "'WebHelpers' has had no release since 2012")])


@given(st.lists(st.fixed_dictionaries({"kind": st.sampled_from(["deprecated-api", "pinned-dependency", "python-support-gap"]),
                                       "message": st.text(max_size=60)})))
def test_python2_is_named_exactly_when_a_syntax_error_is_present(findings):
    # no syntax-error kind is generated above, so the rule must never fire
    assert not dc.names_cause(truth("webapp2"), findings)
    assert dc.names_cause(truth("webapp2"), findings + [finding("syntax-error", "x")])


@given(st.lists(st.fixed_dictionaries({"kind": st.text(max_size=12), "message": st.text(max_size=60)})))
def test_nothing_is_named_from_no_findings_and_extra_findings_never_unname(findings):
    assert not any(dc.names_cause(t, []) for t in dc.TRUTH)
    for t in dc.TRUTH:
        if dc.names_cause(t, findings):
            assert dc.names_cause(t, findings + [finding("deprecated-api", "unrelated")])


# --- totals and the report ----------------------------------------------------------------------------------------


def fake_results(named_py2=True):
    """Every package gets a finding; Python 2 packages get a syntax error, the rest only the age finding."""
    out = {}
    for t in dc.TRUTH:
        fs = [finding("abandoned-package", "old")]
        if t.cause == "python2" and named_py2:
            fs.append(finding("syntax-error", "does not parse"))
        out[t.name] = {"findings": fs}
    return out


def test_score_counts_per_cause():
    s = dc.score(dc.TRUTH, fake_results())
    assert (s["named"], s["checked"]) == (8, 19)
    assert s["by_cause"]["python2"] == {"named": 8, "checked": 8}
    assert s["by_cause"]["dependency_removed"] == {"named": 0, "checked": 5}


def test_a_package_that_could_not_be_read_is_in_no_denominator():
    results = fake_results()
    results["webapp2"] = {"error": "ConnectError: no network"}
    del results["pydns"]                                           # missing entirely
    s = dc.score(dc.TRUTH, results)
    assert s["checked"] == 17 and s["named"] == 6
    assert set(s["unchecked"]) == {"webapp2", "pydns"}


def test_python2_surprises_lists_other_labelled_packages_that_also_have_python2_syntax():
    results = fake_results()
    results["minimongo"]["findings"].append(finding("syntax-error", "line 69"))
    assert dc.python2_surprises(dc.TRUTH, results) == ["minimongo"]


def test_render_states_the_totals_the_rule_and_the_caveats():
    page = dc.render(fake_results(), "2026-11-26")
    assert "It named the cause for 8 of 19 packages." in page
    assert "| Python 2 code | 8 of 8 |" in page
    assert "whole-word match" in page
    assert "not an independent" in page            # the caveat that the labels are the author's
    assert "after the autopsy output had been read" in page
    assert "did not predict that the install would fail" in page
    assert "Run on 2026-11-26." in page


def test_render_survives_missing_results():
    page = dc.render({})
    assert "not checked" in page and "0 of 0" in page
