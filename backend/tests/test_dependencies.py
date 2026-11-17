"""Tests for Coroner dependency analysis (task 3). No network."""
from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from coroner.autopsy import analyze_dependencies
from models import DependencyInfo

NOW = date(2026, 1, 1)


def kinds(findings):
    return [f.kind for f in findings]


def test_clean_dependency_has_no_findings():
    assert analyze_dependencies(["requests>=2.0"], NOW) == []


def test_exact_pin_is_flagged():
    findings = analyze_dependencies(["requests==2.0.0"], NOW)
    assert kinds(findings) == ["pinned-dependency"]


def test_wildcard_pin_is_not_an_exact_pin():
    assert analyze_dependencies(["requests==2.*"], NOW) == []


def test_upper_bound_is_flagged():
    assert "upper-bounded-dependency" in kinds(analyze_dependencies(["django<2.0"], NOW))


def test_unparseable_dependency_is_reported_not_raised():
    findings = analyze_dependencies(["not a valid !! requirement"], NOW)
    assert kinds(findings) == ["unparseable-dependency"]


def test_yanked_pinned_version_is_high_severity():
    info = {"requests": DependencyInfo(yanked_versions=["2.0.0"])}
    findings = analyze_dependencies(["requests==2.0.0"], NOW, info)
    yanked = [f for f in findings if f.kind == "yanked-dependency"]
    assert len(yanked) == 1 and yanked[0].severity == "high"


def test_yanked_match_is_semantic_not_textual():
    info = {"requests": DependencyInfo(yanked_versions=["2.0"])}
    assert "yanked-dependency" in kinds(analyze_dependencies(["requests==2.0.0"], NOW, info))


def test_unpinned_dependency_is_not_marked_yanked():
    info = {"requests": DependencyInfo(yanked_versions=["2.0.0"])}
    assert "yanked-dependency" not in kinds(analyze_dependencies(["requests>=2.0"], NOW, info))


def test_incompatible_python_is_high_severity():
    info = {"oldlib": DependencyInfo(supports_target_python=False)}
    findings = analyze_dependencies(["oldlib>=1.0"], NOW, info)
    assert [(f.kind, f.severity) for f in findings] == [("incompatible-dependency", "high")]


def test_unknown_python_support_is_not_flagged():
    info = {"oldlib": DependencyInfo(supports_target_python=None)}
    assert analyze_dependencies(["oldlib>=1.0"], NOW, info) == []


def test_unmaintained_dependency_is_flagged():
    info = {"oldlib": DependencyInfo(last_release_date="2018-05-01")}
    assert "unmaintained-dependency" in kinds(analyze_dependencies(["oldlib"], NOW, info))


def test_recently_released_dependency_is_not_unmaintained():
    info = {"newlib": DependencyInfo(last_release_date="2025-11-01")}
    assert analyze_dependencies(["newlib"], NOW, info) == []


def test_info_lookup_normalizes_names():
    info = {"My_Lib": DependencyInfo(supports_target_python=False)}
    assert "incompatible-dependency" in kinds(analyze_dependencies(["my-lib>=1"], NOW, info))


# --- property-based -------------------------------------------------------------


@given(st.lists(st.text(max_size=40), max_size=10))
def test_analyzer_never_raises_on_arbitrary_strings(deps):
    findings = analyze_dependencies(deps, NOW)
    assert all(f.kind and f.message for f in findings)


@given(st.lists(st.from_regex(r"[a-z][a-z0-9_-]{0,10}==[0-9]{1,2}(\.[0-9]{1,2}){0,2}", fullmatch=True), max_size=8))
def test_every_exact_pin_is_reported(deps):
    pinned = [f for f in analyze_dependencies(deps, NOW) if f.kind == "pinned-dependency"]
    assert len(pinned) == len(deps)
