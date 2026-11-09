"""Property-based tests for the Necromancer (tasks 6 & 7 starter).

These encode invariants that must hold for any input, catching edge cases unit tests
miss. Expand as the transforms grow.
"""
from hypothesis import given
from hypothesis import strategies as st

from necromancer.revive import bump_dependencies, rewrite_deprecated

# A dependency line like "name==1.2.3"
dep_names = st.from_regex(r"[a-zA-Z][a-zA-Z0-9_-]{0,15}", fullmatch=True)
versions = st.from_regex(r"[0-9]{1,3}(\.[0-9]{1,3}){0,2}", fullmatch=True)
pinned_deps = st.builds(lambda n, v: f"{n}=={v}", dep_names, versions)


@given(st.lists(pinned_deps, max_size=10))
def test_bump_never_leaves_hard_pin(deps):
    new_deps, _applied, _manual = bump_dependencies(deps, "3.12")
    # Property: no output dependency keeps an exact '==' pin.
    assert all("==" not in d for d in new_deps)


@given(st.lists(pinned_deps, max_size=10))
def test_bump_preserves_count(deps):
    new_deps, _applied, _manual = bump_dependencies(deps, "3.12")
    # Property: revival never drops a dependency.
    assert len(new_deps) == len(deps)


@given(st.text(max_size=200))
def test_rewrite_is_idempotent(source):
    once, _ = rewrite_deprecated(source)
    twice, _ = rewrite_deprecated(once)
    # Property: applying the rewrite twice equals applying it once.
    assert once == twice
