"""The benchmark's selection rule and statistics. No network: PyPI payloads and results are made up here."""
import importlib.util
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("benchmark_script", ROOT / "scripts" / "benchmark.py")
bm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bm)


def payload(date="2019-05-01", sdist=True):
    return {
        "releases": {"1.0": [{"upload_time_iso_8601": f"{date}T10:00:00Z"}], "0.9": [{"upload_time": "2015-01-01T00:00:00"}]},
        "urls": [{"packagetype": "sdist" if sdist else "bdist_wheel"}],
    }


# --- the candidate list ---------------------------------------------------------------------------------------------


def test_the_shipped_list_parses_and_has_no_duplicates():
    rows = bm.read_candidates(ROOT / "benchmark" / "packages.txt")
    names = [n.lower() for _, n in rows]
    assert len(names) == len(set(names)), "a package listed twice would be counted twice"
    assert {g for g, _ in rows} == {"seen", "held-out"}
    assert sum(1 for g, _ in rows if g == "held-out") > bm.MAX_HELD_OUT, "the cap needs more candidates than it takes"


def test_comments_and_blank_lines_are_ignored(tmp_path):
    f = tmp_path / "p.txt"
    f.write_text("# header\n\nseen   a  # trailing\nheld-out b\n", encoding="utf-8")
    assert bm.read_candidates(f) == [("seen", "a"), ("held-out", "b")]


# --- the selection rule ---------------------------------------------------------------------------------------------


def test_the_latest_upload_across_all_releases_decides():
    assert bm.last_release(payload("2019-05-01")) == "2019-05-01"
    assert bm.last_release({"releases": {}}) is None


@pytest.mark.parametrize("p, expected", [
    (payload("2019-05-01"), None),
    (payload("2020-12-31"), None),
    (payload("2021-01-01"), "released 2021-01-01, not before 2021-01-01"),
    (payload("2024-01-01"), "released 2024-01-01, not before 2021-01-01"),
    (payload("2019-05-01", sdist=False), "latest release has no sdist"),
    (None, "not found on PyPI"),
    ({"releases": {}, "urls": []}, "no release files"),
])
def test_eligibility(p, expected):
    assert bm.eligibility(p) == expected


def test_held_out_packages_stop_at_the_cap_in_list_order():
    taken = 0
    kept = []
    for i in range(bm.MAX_HELD_OUT + 5):
        reason, taken = bm.decide("held-out", payload(), taken)
        kept.append(reason is None)
    assert kept == [True] * bm.MAX_HELD_OUT + [False] * 5 and taken == bm.MAX_HELD_OUT


def test_the_cap_does_not_apply_to_seen_packages_and_ineligible_ones_do_not_use_it_up():
    assert bm.decide("seen", payload(), bm.MAX_HELD_OUT) == (None, bm.MAX_HELD_OUT)
    reason, taken = bm.decide("held-out", payload("2024-01-01"), 3)
    assert reason and taken == 3


# --- statistics ---------------------------------------------------------------------------------------------------


def test_wilson_interval_known_values():
    lo, hi = bm.wilson(5, 10)
    assert 0.23 < lo < 0.24 and 0.76 < hi < 0.77
    assert bm.wilson(0, 0) == (0.0, 0.0)


@given(st.integers(0, 200).flatmap(lambda n: st.tuples(st.integers(0, n), st.just(n))))
def test_wilson_interval_contains_the_rate_and_stays_in_bounds(pair):
    k, n = pair
    lo, hi = bm.wilson(k, n)
    assert 0.0 <= lo <= hi <= 1.0
    if n:
        assert lo - 1e-9 <= k / n <= hi + 1e-9


def test_percentages_are_shown_with_their_denominator():
    assert bm.pct(3, 4).startswith("3/4 = 75%") and bm.pct(0, 0) == "n/a"


# --- summaries ------------------------------------------------------------------------------------------------------


def ran(name, group, before, after, verdict, error=None, seconds=10.0):
    return {"name": name, "group": group, "status": "ran", "last_release": "2019-01-01", "seconds": seconds, "verdict": verdict,
            "reason": "r", "liveness": "dead", "findings": 1, "files_changed": 1, "manual_items": 0, "truncated": False,
            "before_imported": before, "after_imported": after, "before_tests": [0, 0, 0], "after_tests": [1, 0, 0],
            "test_source": "smoke", "after_error": error}


ROWS = [
    ran("a", "held-out", False, True, "resurrected"),
    ran("b", "held-out", False, False, "still_dead", "SyntaxError: print"),
    ran("c", "held-out", True, True, "already_alive"),
    ran("d", "held-out", False, False, "still_dead", "ImportError: No module named 'x'"),
    ran("s", "seen", False, True, "resurrected"),
    {"name": "e", "group": "held-out", "status": "error", "error": "timeout", "seconds": 1.0},
    {"name": "f", "group": "held-out", "status": "excluded", "reason": "released 2024"},
]


def test_summary_counts_only_the_requested_group():
    held = bm.summarize(ROWS, "held-out")
    assert (held["ran"], held["dead_before"], held["resurrected"], held["alive_before"], held["alive_after"]) == (4, 3, 1, 1, 2)
    assert len(held["errors"]) == 1
    assert bm.summarize(ROWS, "seen")["resurrected"] == 1
    assert bm.summarize(ROWS)["ran"] == 5


def test_an_already_alive_package_is_not_counted_as_a_resurrection():
    assert bm.summarize([ran("c", "held-out", True, True, "already_alive")])["resurrected"] == 0


@pytest.mark.parametrize("error, cause", [
    ("(could not install: foo)", "a dependency could not be installed (wheel-only)"),
    ("SyntaxError: Missing parentheses", "Python 2 syntax (not converted)"),
    ("ModuleNotFoundError: No module named 'imp'", "a module is missing or was removed from Python"),
    ("ImportError: cannot import name 'x'", "a name was removed from a dependency or the standard library"),
    ("AttributeError: no attribute y", "an attribute was removed or changed"),
    ("something unexpected", "other"),
    (None, "other"),
])
def test_failure_causes(error, cause):
    assert bm.failure_cause({"after_error": error, "reason": ""}) == cause


def test_the_report_states_the_rate_the_caveats_and_every_package():
    report = bm.render_report(ROWS)
    assert "1/3 = 33%" in report, "the rate among dead packages, with its denominator"
    assert "not a random sample" in report and "Many old packages still work" in report
    for name in ("| a |", "| b |", "| c |", "| d |", "| s |"):
        assert name in report
    assert "e (held-out): timeout" in report and "f (held-out): released 2024" in report


def placeholder(name, group="held-out"):
    row = ran(name, group, False, False, "unverified", "No importable Python package or module was found")
    row["before_skipped"] = row["after_skipped"] = True
    return row


def test_a_placeholder_is_in_no_rate_and_is_counted_apart():
    rows = ROWS + [placeholder("shim")]
    held = bm.summarize(rows, "held-out")
    assert held["nothing_to_run"] == 1
    assert (held["ran"], held["dead_before"], held["resurrected"]) == (4, 3, 1), "the placeholder must not count as dead"
    assert held["verdicts"]["unverified"] == 1, "but it still appears among the verdicts"


def test_result_files_from_before_the_field_existed_still_summarise():
    assert "before_skipped" not in ROWS[0] and bm.summarize(ROWS, "held-out")["nothing_to_run"] == 0


def test_the_report_says_how_many_placeholders_were_left_out_and_shows_them_in_the_table():
    text = bm.render_report(ROWS + [placeholder("shim")])
    assert "1 more had no Python source of their own" in text and "| shim |" in text and "nothing to run" in text


def test_the_row_records_whether_each_run_was_skipped():
    data = {"verdict": "unverified", "verdict_reason": "r", "autopsy": {"liveness": "dead", "findings": []},
            "revival": {"diffs": [], "manual_items": []}, "package": {"truncated": False},
            "proof_before": {"imported": False, "skipped": True}, "proof_after": {"imported": False, "skipped": True}}
    row = bm.row_from_result("held-out", "shim", "2015-01-01", data, 1.0)
    assert row["before_skipped"] is True and row["after_skipped"] is True


def test_the_history_has_one_row_per_run_with_its_own_denominator():
    runs = [("first", ROWS), ("second", [ran("a", "held-out", False, False, "still_dead"), ran("b", "held-out", True, True, "already_alive")])]
    text = bm.render_history(runs)
    assert len([line for line in text.splitlines() if line.startswith("| ") and "---" not in line]) == 3, "a header and two runs"
    assert "| first | 5 | 4 | 2 |" in text and "| second | 2 | 1 | 0 |" in text and "0/1 = 0%" in text


def test_the_history_command_reads_label_and_path(tmp_path, capsys):
    path = tmp_path / "r.jsonl"
    path.write_text("\n".join(__import__("json").dumps(r) for r in ROWS), encoding="utf-8")
    import argparse

    bm.cmd_history(argparse.Namespace(runs=[f"mine={path}"]))
    assert "| mine | 5 |" in capsys.readouterr().out


def test_the_comparison_lists_only_packages_whose_outcome_changed():
    before = [ran("a", "held-out", False, False, "still_dead"), ran("b", "held-out", True, True, "already_alive"),
              ran("only-before", "held-out", True, True, "already_alive")]
    after = [ran("a", "held-out", False, True, "resurrected"), ran("b", "held-out", True, True, "already_alive"),
             ran("only-after", "held-out", True, True, "already_alive")]
    text = bm.render_comparison(before, after, "First run", "after the fixes")
    assert "2 packages ran in both. 1 changed outcome." in text
    assert "| a | still_dead | resurrected |" in text and "| b |" not in text and "only-" not in text


def test_a_comparison_with_no_change_says_so():
    rows = [ran("a", "held-out", True, True, "already_alive")]
    assert "No package changed outcome." in bm.render_comparison(rows, rows, "x", "y")


def test_the_comparison_is_case_insensitive_about_names():
    a, b = ran("Flask-Mail", "held-out", False, False, "still_dead"), ran("flask-mail", "held-out", False, True, "resurrected")
    assert "1 changed outcome" in bm.render_comparison([a], [b], "x", "y")


def test_each_later_candidate_list_is_valid_and_shares_nothing_with_the_earlier_ones():
    lists = ["packages.txt", "packages-fresh.txt", "packages-third.txt"]
    seen: set[str] = set()
    for i, filename in enumerate(lists):
        rows = bm.read_candidates(ROOT / "benchmark" / filename)
        names = [n.lower() for _, n in rows]
        assert len(names) == len(set(names)), f"{filename} lists a package twice"
        assert not (set(names) & seen), f"{filename} repeats a package from an earlier list, so it would not be fresh"
        if i:
            assert {g for g, _ in rows} == {"held-out"}
        assert sum(1 for g, _ in rows if g == "held-out") > bm.MAX_HELD_OUT
        seen |= set(names)


def test_the_development_section_is_left_out_when_there_were_no_development_packages():
    without = [r for r in ROWS if r["group"] != "seen"]
    assert "Packages used while developing" not in bm.render_report(without)
    assert "Packages used while developing Crypt Keeper (1 ran)" in bm.render_report(ROWS)


def test_the_report_names_the_file_it_was_made_from():
    assert "from `benchmark/mine.jsonl`" in bm.render_report(ROWS, "benchmark/mine.jsonl")


def test_the_report_keeps_the_development_packages_out_of_the_headline():
    report = bm.render_report(ROWS)
    headline = report.split("## Verdicts")[0]
    assert "4 ran" in headline, "only the held-out packages are in the headline"
