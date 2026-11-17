"""Tests for autopsy report assembly (task 5). Pure; no network."""
from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from coroner.autopsy import classify_liveness, run_autopsy
from models import (
    AutopsyReport, DependencyInfo, Liveness, PackageRef, PackageSnapshot, Source,
)

NOW = date(2026, 1, 1)


def snap(**kw) -> PackageSnapshot:
    return PackageSnapshot(ref=PackageRef(source=Source.pypi, identifier="pkg"), **kw)


def kinds(report: AutopsyReport) -> list[str]:
    return [f.kind for f in report.findings]


# --- liveness uses declared Python support (requirement 2.1) -------------------------


def test_recent_release_with_old_python_support_is_dormant():
    assert classify_liveness(date(2025, 12, 1), NOW, ["3.6", "3.7"], "3.12") == Liveness.dormant


def test_recent_release_with_current_python_support_is_alive():
    assert classify_liveness(date(2025, 12, 1), NOW, ["3.10", "3.11", "3.12"], "3.12") == Liveness.alive


def test_python2_only_package_is_dormant_even_if_recent():
    assert classify_liveness(date(2025, 12, 1), NOW, ["2.7"], "3.12") == Liveness.dormant


def test_lag_below_threshold_is_still_alive():
    assert classify_liveness(date(2025, 12, 1), NOW, ["3.10"], "3.12") == Liveness.alive


def test_unknown_python_support_never_demotes():
    assert classify_liveness(date(2025, 12, 1), NOW, [], "3.12") == Liveness.alive


def test_python_support_is_ignored_without_a_target():
    assert classify_liveness(date(2025, 12, 1), NOW, ["3.6"]) == Liveness.alive


def test_python_support_never_makes_package_deader_than_age():
    assert classify_liveness(date(2024, 6, 1), NOW, ["3.6"], "3.12") == Liveness.dormant


# --- causes of death are findings (requirement 2.4) ---------------------------------------


def test_dead_package_reports_abandonment_even_with_no_other_problems():
    report = run_autopsy(snap(last_release_date="2018-01-01", declared_pythons=["3.12"]), "3.12", NOW)
    assert report.liveness == Liveness.dead
    [finding] = report.findings
    assert finding.kind == "abandoned-package" and finding.severity == "high"
    assert "2018-01-01" in finding.message


def test_missing_release_date_is_reported():
    report = run_autopsy(snap(), "3.12", NOW)
    assert report.findings[0].kind == "abandoned-package"
    assert "No release date" in report.findings[0].message


def test_dormant_package_is_medium_severity():
    report = run_autopsy(snap(last_release_date="2024-06-01"), "3.12", NOW)
    assert [(f.kind, f.severity) for f in report.findings] == [("abandoned-package", "medium")]


def test_healthy_package_has_empty_report():
    report = run_autopsy(snap(last_release_date="2025-12-01", declared_pythons=["3.12"]), "3.12", NOW)
    assert report.liveness == Liveness.alive and report.findings == []


def test_python_support_gap_is_its_own_cause_of_death():
    report = run_autopsy(snap(last_release_date="2025-12-01", declared_pythons=["3.6"]), "3.12", NOW)
    assert report.liveness == Liveness.dormant
    assert kinds(report) == ["python-support-gap"]
    assert "3.6" in report.findings[0].message and "3.12" in report.findings[0].message


def test_python2_only_support_gap_message():
    report = run_autopsy(snap(last_release_date="2025-12-01", declared_pythons=["2.7"]), "3.12", NOW)
    assert "2.7" in report.findings[0].message


def test_report_combines_all_sources_of_findings():
    report = run_autopsy(
        snap(
            last_release_date="2018-01-01",
            declared_pythons=["3.6"],
            dependencies=["oldlib==1.0"],
            dependency_info={"oldlib": DependencyInfo(supports_target_python=False)},
            source_map={"m.py": "import imp"},
        ),
        "3.12", NOW,
    )
    assert set(kinds(report)) >= {
        "abandoned-package", "python-support-gap", "pinned-dependency",
        "incompatible-dependency", "deprecated-api",
    }


# --- ordering --------------------------------------------------------------------------


def test_findings_are_ordered_most_severe_first():
    report = run_autopsy(
        snap(last_release_date="2018-01-01", dependencies=["a==1", "b<2"], source_map={"m.py": "import imp"}),
        "3.12", NOW,
    )
    ranks = [{"high": 0, "medium": 1, "low": 2}[f.severity] for f in report.findings]
    assert ranks == sorted(ranks) and ranks[0] == 0 and ranks[-1] == 2


def test_report_is_deterministic():
    s = snap(last_release_date="2018-01-01", dependencies=["a==1", "b==2"], source_map={"x.py": "import imp", "a.py": "import imp"})
    assert run_autopsy(s, "3.12", NOW) == run_autopsy(s, "3.12", NOW)


@pytest.mark.parametrize("target", ["3.12", "3", "nonsense", ""])
def test_odd_targets_do_not_raise(target):
    run_autopsy(snap(last_release_date="2025-12-01", declared_pythons=["3.6"]), target, NOW)


# --- property-based ----------------------------------------------------------------------


snapshots = st.builds(
    snap,
    last_release_date=st.one_of(st.none(), st.dates(date(1995, 1, 1), date(2026, 1, 1)).map(str), st.text(max_size=12)),
    declared_pythons=st.lists(st.from_regex(r"[0-9]{1,2}\.[0-9]{1,2}", fullmatch=True), max_size=5),
    dependencies=st.lists(st.text(max_size=20), max_size=5),
    source_map=st.dictionaries(st.text(min_size=1, max_size=10), st.text(max_size=80), max_size=3),
)


@given(snapshots, st.sampled_from(["3.8", "3.12", "3.13", "bad"]))
def test_autopsy_never_raises_and_is_sorted(snapshot, target):
    report = run_autopsy(snapshot, target, NOW)
    ranks = [{"high": 0, "medium": 1, "low": 2}[f.severity] for f in report.findings]
    assert ranks == sorted(ranks)
    assert all(f.message for f in report.findings)


@given(snapshots)
def test_non_alive_package_always_explains_itself(snapshot):
    report = run_autopsy(snapshot, "3.12", NOW)
    if report.liveness != Liveness.alive:
        assert report.findings, "a dormant or dead package must list at least one cause"
