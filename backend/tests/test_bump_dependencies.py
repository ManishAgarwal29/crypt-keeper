"""Tests for Necromancer dependency bumping (task 6). Pure; no network."""
import pytest
from hypothesis import given
from hypothesis import strategies as st
from packaging.requirements import Requirement
from packaging.version import Version

from necromancer.floors import PYTHON_FLOORS, floor_for
from necromancer.revive import bump_dependencies


def bump(dep: str, target: str = "3.12"):
    (new,), applied, manual = bump_dependencies([dep], target)
    return new, applied, manual


# --- unpinning ---------------------------------------------------------------------------


def test_exact_pin_becomes_lower_bound():
    new, applied, manual = bump("requests==2.0.0")
    assert new == "requests>=2.0.0" and manual == []
    assert applied == ["Unpinned requests: ==2.0.0 -> >=2.0.0"]


def test_local_version_label_is_dropped_from_lower_bound():
    assert bump("torch==2.1.0+cpu")[0] == "torch>=2.1.0"


def test_extras_and_markers_survive_unpinning():
    new, _, _ = bump('requests[security]==2.0; python_version >= "3.8"')
    req = Requirement(new)
    assert req.extras == {"security"}
    assert str(req.marker) == 'python_version >= "3.8"'
    assert str(req.specifier) == ">=2.0"


def test_wildcard_pin_is_left_alone():
    assert bump("django==3.*") == ("django==3.*", [], [])


def test_other_operators_are_left_alone():
    for dep in ("requests>=2.0", "requests!=2.1", "requests~=2.0", "requests"):
        assert bump(dep) == (dep, [], []), dep


def test_requirement_needing_no_change_is_returned_verbatim():
    assert bump("requests  >=  2.0") == ("requests  >=  2.0", [], [])


# --- upper bounds --------------------------------------------------------------------------


def test_upper_bound_is_removed_and_reported():
    new, applied, _ = bump("django>=1.8,<2.0")
    assert new == "django>=1.8"
    assert any("Removed upper bound <2.0" in a for a in applied)


def test_inclusive_upper_bound_is_removed():
    assert bump("django<=2.0")[0] == "django"


def test_pin_with_upper_bound_keeps_only_a_lower_bound():
    assert bump("flask>=0.10,<1.0,!=0.11")[0] == "flask!=0.11,>=0.10"


# --- floors for compiled packages ------------------------------------------------------------


def test_pinned_numpy_is_unpinned_then_raised_to_floor():
    new, applied, _ = bump("numpy==1.16.0")
    assert new == "numpy>=1.26.0"
    assert len(applied) == 2 and "Raised numpy floor from 1.16.0 to 1.26.0" in applied[1]


def test_floor_depends_on_target_python():
    assert bump("numpy>=1.16", "3.11")[0] == "numpy>=1.23.2"
    assert bump("numpy>=1.16", "3.13")[0] == "numpy>=2.1.0"


def test_no_floor_for_targets_older_than_the_table():
    assert bump("numpy>=1.16", "3.8") == ("numpy>=1.16", [], [])


def test_newer_target_uses_newest_known_floor():
    assert bump("numpy>=1.16", "3.99")[0] == "numpy>=2.1.0"


def test_floor_already_satisfied_is_untouched():
    assert bump("numpy>=1.30") == ("numpy>=1.30", [], [])


def test_unconstrained_package_gets_no_floor():
    assert bump("numpy") == ("numpy", [], [])


def test_floor_lookup_normalizes_names():
    assert bump("PyYAML>=3.0")[0] == "PyYAML>=6.0.1"
    assert bump("Scikit_Learn>=0.1")[0] == "Scikit_Learn>=1.3.1"


def test_strict_lower_bound_is_replaced_by_floor():
    assert bump("numpy>1.16")[0] == "numpy>=1.26.0"


def test_floor_keeps_extras_and_markers():
    new, _, _ = bump('numpy[extra]>=1.16; python_version >= "3.9"')
    req = Requirement(new)
    assert str(req.specifier) == ">=1.26.0" and req.extras == {"extra"} and req.marker is not None


def test_wildcard_pin_that_cannot_reach_floor_is_manual():
    new, applied, manual = bump("numpy==1.16.*")
    assert new == "numpy==1.16.*" and applied == []
    assert len(manual) == 1 and "1.26.0" in manual[0]


def test_compatible_release_cap_that_cannot_reach_floor_is_manual():
    _, _, manual = bump("numpy~=1.16.0")
    assert len(manual) == 1


def test_compatible_release_cap_that_reaches_floor_is_fine():
    assert bump("numpy~=1.16") == ("numpy~=1.16", [], [])


# --- things that need a human ------------------------------------------------------------------


def test_unparseable_dependency_goes_to_manual_unchanged():
    new, applied, manual = bump("not a valid !! requirement")
    assert new == "not a valid !! requirement" and applied == [] and len(manual) == 1


def test_direct_url_dependency_goes_to_manual_unchanged():
    dep = "pkg @ https://example.com/pkg-1.0.tar.gz"
    new, applied, manual = bump(dep)
    assert new == dep and applied == [] and "direct URL" in manual[0]


def test_arbitrary_equality_goes_to_manual():
    new, _, manual = bump("pkg===1.0")
    assert new == "pkg===1.0" and len(manual) == 1


def test_output_is_one_to_one_and_ordered():
    deps = ["a==1", "b>=2", "oops !!", "c<3"]
    new, _, _ = bump_dependencies(deps, "3.12")
    assert new == ["a>=1", "b>=2", "oops !!", "c"]


# --- floors table --------------------------------------------------------------------------------


def test_floor_table_is_well_formed_and_monotonic():
    for name, by_minor in PYTHON_FLOORS.items():
        assert name == name.lower()
        minors = sorted(by_minor)
        versions = [Version(by_minor[m]) for m in minors]
        assert versions == sorted(versions), f"{name} floors must not decrease with newer Python"


@pytest.mark.parametrize("target,expected", [("3.12", "1.26.0"), ("3.12.4", "1.26.0"), ("3.8", None), ("2.7", None), ("x", None), ("", None)])
def test_floor_for(target, expected):
    assert floor_for("numpy", target) == expected


def test_floor_for_unknown_package():
    assert floor_for("requests", "3.12") is None


# --- property-based ------------------------------------------------------------------------------

names = st.from_regex(r"[a-z]([a-z0-9_-]{0,9}[a-z0-9])?", fullmatch=True)
versions = st.from_regex(r"[0-9]{1,3}(\.[0-9]{1,3}){0,2}", fullmatch=True)
known = st.sampled_from(sorted(PYTHON_FLOORS))
ops = st.sampled_from(["==", ">=", ">", "<", "<=", "!=", "~=", "==X.*"])


@st.composite
def requirement_strings(draw):
    name = draw(st.one_of(names, known))
    extras = draw(st.sampled_from(["", "[extra]"]))
    parts = []
    for _ in range(draw(st.integers(0, 3))):
        op, v = draw(ops), draw(versions)
        parts.append(f"=={v}.*" if op == "==X.*" else (f"~={v}.0" if op == "~=" else f"{op}{v}"))
    marker = draw(st.sampled_from(["", '; python_version >= "3.8"']))
    return f"{name}{extras}{','.join(parts)}{marker}"


targets = st.sampled_from(["3.8", "3.9", "3.11", "3.12", "3.13", "bad"])


@given(st.lists(requirement_strings(), max_size=8), targets)
def test_bump_is_idempotent(deps, target):
    once, _, _ = bump_dependencies(deps, target)
    twice, _, _ = bump_dependencies(once, target)
    assert twice == once


@given(st.lists(requirement_strings(), max_size=8), targets)
def test_bump_output_is_valid_and_one_to_one(deps, target):
    new, _, _ = bump_dependencies(deps, target)
    assert len(new) == len(deps)
    for out in new:
        Requirement(out)


@given(st.lists(requirement_strings(), max_size=8), targets)
def test_bump_leaves_no_exact_pin_or_upper_bound(deps, target):
    new, _, _ = bump_dependencies(deps, target)
    for out in new:
        for s in Requirement(out).specifier:
            assert s.operator not in ("<", "<=")
            assert not (s.operator == "==" and not s.version.endswith(".*"))


@given(names, versions, targets)
def test_unpinning_never_lowers_the_version(name, v, target):
    (out,), _, _ = bump_dependencies([f"{name}=={v}"], target)
    [spec] = Requirement(out).specifier
    assert spec.operator == ">=" and Version(spec.version) >= Version(v)


@given(known, versions, st.sampled_from(["3.9", "3.10", "3.11", "3.12", "3.13"]))
def test_no_lower_bound_is_left_below_the_floor(name, v, target):
    (out,), _, _ = bump_dependencies([f"{name}>={v}"], target)
    floor = Version(floor_for(name, target))
    [spec] = Requirement(out).specifier
    assert Version(spec.version) >= floor


@given(requirement_strings(), targets)
def test_changes_are_always_reported(dep, target):
    (out,), applied, _ = bump_dependencies([dep], target)
    assert (out != dep) == bool(applied)


@given(st.lists(st.text(max_size=40), max_size=8), targets)
def test_bump_never_raises_on_arbitrary_strings(deps, target):
    new, _, _ = bump_dependencies(deps, target)
    assert len(new) == len(deps)
