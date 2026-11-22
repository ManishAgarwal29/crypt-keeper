"""Tests for the before/after verdict (task 11). Pure."""
import pytest
from hypothesis import given
from hypothesis import strategies as st

from models import ProofOfLife
from proof.verdict import judge


def pol(imported: bool, passed: int = 0, matched: bool = True, python: str | None = "3.12.1",
        skipped: bool = False, error: str | None = None) -> ProofOfLife:
    return ProofOfLife(
        imported=imported, error=None if imported else (error or "ImportError: boom"), tests_passed=passed,
        python_used=python, target_python_matched=matched, skipped=skipped,
    )


def test_dead_then_alive_is_a_resurrection_and_names_the_cause():
    verdict, reason = judge(pol(False, error="AttributeError: no MutableMapping"), pol(True, 6))
    assert verdict == "resurrected" and "AttributeError: no MutableMapping" in reason


def test_alive_both_times_with_more_passing_tests_is_an_improvement():
    verdict, reason = judge(pol(True, 324), pol(True, 411))
    assert verdict == "improved" and "87 more tests" in reason and "324 -> 411" in reason


@pytest.mark.parametrize("passed_before,passed_after", [(5, 5), (5, 3)])
def test_alive_both_times_without_more_passing_tests_is_already_alive(passed_before, passed_after):
    assert judge(pol(True, passed_before), pol(True, passed_after))[0] == "already_alive"


def test_alive_then_dead_is_a_regression():
    verdict, reason = judge(pol(True, 3), pol(False, error="SyntaxError: bad"))
    assert verdict == "regressed" and "SyntaxError: bad" in reason


def test_dead_both_times_is_still_dead():
    verdict, reason = judge(pol(False), pol(False, error="ModuleNotFoundError: flask._compat"))
    assert verdict == "still_dead" and "flask._compat" in reason


# --- honesty about the interpreter ---------------------------------------------------------------------------------


def test_success_on_the_wrong_python_is_never_a_resurrection():
    verdict, reason = judge(pol(False, matched=False, python="3.11.6"), pol(True, matched=False, python="3.11.6"))
    assert verdict == "unverified" and "3.11.6" in reason and "does not prove" in reason


def test_failure_on_the_wrong_python_is_still_reported_as_dead():
    assert judge(pol(False, matched=False), pol(False, matched=False))[0] == "still_dead"


def test_a_definitive_failure_without_an_interpreter_is_dead():
    after = ProofOfLife(imported=False, error="No importable Python package or module found in the source.")
    assert judge(None, after)[0] == "still_dead"


# --- missing or skipped checks ----------------------------------------------------------------------------------------


def test_no_proof_at_all_is_unverified():
    assert judge(None, None) == ("unverified", "Proof of life was not run.")


def test_skipped_proof_is_unverified():
    assert judge(pol(True), pol(False, skipped=True))[0] == "unverified"


def test_alive_without_a_baseline_is_unverified_but_dead_is_still_dead():
    assert judge(None, pol(True))[0] == "unverified"
    assert judge(None, pol(False))[0] == "still_dead"
    assert judge(pol(True, skipped=True), pol(True))[0] == "unverified"


# --- property-based -------------------------------------------------------------------------------------------------------

proofs = st.builds(
    pol, st.booleans(), st.integers(0, 50), st.booleans(), st.sampled_from([None, "3.11.6", "3.12.1"]),
    st.booleans(), st.one_of(st.none(), st.text(max_size=300)),
)


@given(st.one_of(st.none(), proofs), st.one_of(st.none(), proofs))
def test_judge_never_raises_and_always_explains(before, after):
    verdict, reason = judge(before, after)
    assert verdict in {"resurrected", "improved", "already_alive", "still_dead", "regressed", "unverified"}
    assert reason and len(reason) < 600


@given(proofs, proofs)
def test_a_resurrection_requires_dead_before_alive_after_on_the_target_python(before, after):
    if judge(before, after)[0] == "resurrected":
        assert not before.imported and not before.skipped
        assert after.imported and not after.skipped
        assert after.python_used is None or after.target_python_matched


# --- a truncated exhumation cannot prove the package dead ---------------------------------------------------


@pytest.mark.parametrize("before", [None, pol(True, 3), pol(False)])
def test_a_failure_on_truncated_source_is_unverified_never_dead_or_regressed(before):
    verdict, reason = judge(before, pol(False, error="ImportError: no module named x"), truncated=True)
    assert verdict == "unverified" and "incomplete" in reason and "ImportError" in reason


def test_a_failure_on_complete_source_is_still_dead_or_regressed():
    assert judge(pol(False), pol(False), truncated=False)[0] == "still_dead"
    assert judge(pol(True, 1), pol(False), truncated=False)[0] == "regressed"


def test_truncation_does_not_hide_a_success_that_was_actually_shown():
    assert judge(pol(False), pol(True, 4), truncated=True)[0] == "resurrected"


def test_truncation_never_upgrades_a_wrong_python_run():
    assert judge(pol(True), pol(True, matched=False), truncated=True)[0] == "unverified"
