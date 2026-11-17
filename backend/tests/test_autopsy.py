"""Unit tests for the Coroner (task 2 starter)."""
from datetime import date

from coroner.autopsy import (
    classify_liveness,
    detect_deprecated_apis,
    run_autopsy,
)
from models import Liveness, PackageSnapshot, PackageRef, Source


def test_classify_liveness_alive():
    now = date(2026, 1, 1)
    assert classify_liveness(date(2025, 12, 1), now, ["3.11"]) == Liveness.alive


def test_classify_liveness_dormant():
    now = date(2026, 1, 1)
    assert classify_liveness(date(2024, 6, 1), now, ["3.9"]) == Liveness.dormant


def test_classify_liveness_dead_by_age():
    now = date(2026, 1, 1)
    assert classify_liveness(date(2020, 1, 1), now, ["3.6"]) == Liveness.dead


def test_classify_liveness_dead_when_unknown():
    now = date(2026, 1, 1)
    assert classify_liveness(None, now, []) == Liveness.dead


def test_detect_deprecated_apis_finds_known():
    findings = detect_deprecated_apis(
        {"pkg/mod.py": "import collections\nx = collections.Mapping"}, "3.12"
    )
    assert any(f.kind == "deprecated-api" for f in findings)


def test_run_autopsy_composes_report():
    snapshot = PackageSnapshot(
        ref=PackageRef(source=Source.pypi, identifier="deadpkg"),
        last_release_date="2019-01-01",
        declared_pythons=["3.6"],
        dependencies=["requests==2.0.0"],
        source_map={"m.py": "import collections\nx = collections.Mapping"},
    )
    report = run_autopsy(snapshot, "3.12", date(2026, 1, 1))
    assert report.liveness == Liveness.dead
    assert len(report.findings) >= 2
