"""The Undertaker: static triage, change detection, the report, and the script's orchestration. No network."""
import importlib.util
import json
from datetime import date
from types import SimpleNamespace
from pathlib import Path

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from models import AutopsyReport, Finding, Liveness, RevivalResult
from necromancer.triage import (
    ATTENTION,
    TriageEntry,
    compare,
    needs_attention,
    render_report,
    triage,
    unreadable,
)
from tests.test_exhumer import SDIST_URL, make_tar, pypi_client, pypi_payload

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("undertake_script", ROOT / "undertaker" / "undertake.py")
undertake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(undertake)

TODAY = date(2026, 11, 20)


def finding(kind: str, message: str = "m") -> Finding:
    return Finding(kind=kind, severity="high", message=message)


def report(*kinds: str) -> AutopsyReport:
    return AutopsyReport(liveness=Liveness.dead, target_python="3.12", findings=[finding(k) for k in kinds])


def revival(manual: int = 0, files: int = 1) -> RevivalResult:
    from models import FileDiff

    return RevivalResult(diffs=[FileDiff(path=f"f{i}.py", before="a", after="b") for i in range(files)],
                         manual_items=[f"x{i}" for i in range(manual)])


def entry(status="revivable", last="2019-01-01", error=None) -> TriageEntry:
    return TriageEntry(status=status, last_release=last, error=error, checked_on="2026-11-19")


# --- triage -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("before, manual, after_kinds, expected", [
    (("deprecated-api",), 0, (), "revivable"),
    (("deprecated-api", "deprecated-api"), 0, ("abandoned-package",), "revivable"),
    (("deprecated-api",), 2, (), "needs_human"),
    (("syntax-error",), 1, (), "needs_human"),
    (("deprecated-api",), 0, ("incompatible-dependency",), "blocked"),
    ((), 0, ("incompatible-dependency",), "blocked"),
    (("deprecated-api",), 1, ("incompatible-dependency",), "needs_human"),
    ((), 0, (), "alive"),
    (("abandoned-package", "pinned-dependency"), 0, ("abandoned-package",), "alive"),
])
def test_status(before, manual, after_kinds, expected):
    entry_ = triage(report(*before), revival(manual), report(*after_kinds), "2019-01-01", "2026-11-20")
    assert entry_.status == expected


def test_triage_records_what_it_saw():
    e = triage(report("deprecated-api", "deprecated-api", "abandoned-package"), revival(1, files=3),
               report("yanked-dependency"), "2018-02-03", "2026-11-20")
    assert (e.code_findings, e.files_changed, e.manual_items, e.last_release, e.checked_on) == (2, 3, 1, "2018-02-03", "2026-11-20")
    assert e.blockers == ["m"]


def test_an_old_age_alone_does_not_make_a_package_dead_here():
    """Age is not breakage: this triage is about whether the code runs on the target, not about maintenance."""
    assert triage(report("abandoned-package"), revival(0, files=0), report(), "2010-01-01", "2026-11-20").status == "alive"


# --- change detection ---------------------------------------------------------------------------------------------------


def changes(prev: dict, cur: dict):
    return [(o.package, o.change) for o in compare(prev, cur)]


def test_nothing_changed_gives_nothing():
    e = {"a": entry()}
    assert compare(e, {"a": entry()}) == []


def test_a_package_that_becomes_revivable_is_flagged():
    assert changes({"a": entry("needs_human")}, {"a": entry("revivable")}) == [("a", "newly_revivable")]


def test_a_package_that_stops_being_revivable_is_flagged():
    assert changes({"a": entry("revivable")}, {"a": entry("blocked")}) == [("a", "no_longer_revivable")]


def test_a_new_release_is_flagged_even_if_the_status_is_the_same():
    obs = compare({"a": entry("needs_human", "2019-01-01")}, {"a": entry("needs_human", "2026-11-01")})
    assert [(o.package, o.change) for o in obs] == [("a", "new_release")] and "upstream" in obs[0].detail


def test_other_status_moves_are_reported_but_do_not_need_attention():
    obs = compare({"a": entry("alive")}, {"a": entry("blocked")})
    assert [o.change for o in obs] == ["status_changed"] and needs_attention(obs) == []


def test_a_package_seen_for_the_first_time_is_new_and_does_not_need_attention():
    obs = compare({}, {"a": entry("revivable")})
    assert [o.change for o in obs] == ["new"] and needs_attention(obs) == []


def test_a_fetch_failure_keeps_the_last_status_and_is_not_a_status_change():
    prev = {"a": entry("revivable")}
    cur = {"a": unreadable(prev["a"], "PyPI answered with status 503.", "2026-11-20")}
    assert cur["a"].status == "revivable" and cur["a"].last_release == "2019-01-01"
    assert changes(prev, cur) == [("a", "unreadable")]
    assert changes(cur, cur) == [], "the same failure two nights running is not news twice"
    assert changes(cur, {"a": entry("revivable")}) == [("a", "readable_again")]


def test_unreadable_without_history_is_not_called_revivable():
    assert unreadable(None, "x", "2026-11-20").status == "needs_human"


def test_both_a_new_release_and_a_new_status_are_reported():
    kinds = [c for _, c in changes({"a": entry("needs_human", "2019-01-01")}, {"a": entry("revivable", "2026-11-01")})]
    assert set(kinds) == {"new_release", "newly_revivable"}


@given(st.dictionaries(st.sampled_from(list("abcde")), st.sampled_from(["alive", "revivable", "needs_human", "blocked"]).map(entry)))
def test_comparing_a_state_with_itself_is_always_quiet(state):
    assert compare(state, state) == []


def test_every_attention_kind_is_one_the_report_knows():
    assert ATTENTION == {"newly_revivable", "new_release", "no_longer_revivable"}


# --- the report -------------------------------------------------------------------------------------------------------------


def test_the_report_lists_what_needs_attention_first_and_says_it_is_a_prediction():
    cur = {"a": entry("revivable"), "b": entry("blocked")}
    text = render_report(cur, compare({"a": entry("blocked"), "b": entry("blocked")}, cur), "2026-11-20", "3.12")
    assert text.index("Needs your attention") < text.index("Every package")
    assert "**a**" in text and "prediction, not proof" in text and "nothing was run" in text


def test_a_quiet_night_says_so():
    assert "Nothing changed that needs a decision." in render_report({"a": entry()}, [], "2026-11-20", "3.12")


def test_an_unreachable_package_is_shown_with_its_error():
    cur = {"a": unreadable(entry(), "PyPI answered with status 503.", "2026-11-20")}
    assert "not reachable: PyPI answered with status 503." in render_report(cur, [], "2026-11-20", "3.12")


# --- the script ---------------------------------------------------------------------------------------------------------------


DEAD = b"import collections\nx = collections.Mapping\n"
FIXABLE_ROUTES = {
    "https://pypi.org/pypi/deadpkg/json": httpx.Response(200, json=pypi_payload()),
    SDIST_URL: httpx.Response(200, content=make_tar({"deadpkg-1.0/deadpkg/__init__.py": DEAD})),
}


def test_a_fixable_package_is_revivable_and_a_missing_one_is_reported_not_fatal():
    client = pypi_client(FIXABLE_ROUTES)
    current, obs = undertake.undertake(["deadpkg", "nosuchpkg"], {}, "3.12", client, TODAY)
    assert current["deadpkg"].status == "revivable" and current["deadpkg"].files_changed == 1
    assert current["nosuchpkg"].error and "nosuchpkg" in current["nosuchpkg"].error
    assert {o.package for o in obs} == {"deadpkg", "nosuchpkg"}


def test_a_crash_on_one_package_does_not_stop_the_others(monkeypatch):
    real = undertake.triage_package

    def flaky(name, *a):
        if name == "boom":
            raise RuntimeError("kaboom")
        return real(name, *a)

    monkeypatch.setattr(undertake, "triage_package", flaky)
    current, _ = undertake.undertake(["boom", "deadpkg"], {}, "3.12", pypi_client(FIXABLE_ROUTES), TODAY)
    assert "RuntimeError: kaboom" in current["boom"].error and current["deadpkg"].status == "revivable"


def test_state_round_trips_and_a_bad_file_is_refused(tmp_path):
    state = {"a": entry(), "b": entry("blocked")}
    path = tmp_path / "s.json"
    undertake.write_state(path, state)
    assert undertake.read_state(path) == state
    assert path.read_text(encoding="utf-8") == path.read_text(encoding="utf-8") and json.loads(path.read_text())["version"] == 1
    path.write_text('{"version": 99, "packages": {}}', encoding="utf-8")
    with pytest.raises(ValueError):
        undertake.read_state(path)
    assert undertake.read_state(tmp_path / "missing.json") == {}


def test_the_state_file_is_stable_so_nightly_diffs_are_small(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    undertake.write_state(a, {"z": entry(), "a": entry("blocked")})
    undertake.write_state(b, {"a": entry("blocked"), "z": entry()})
    assert a.read_bytes() == b.read_bytes()


def test_the_watchlist_ignores_comments_blanks_and_duplicates(tmp_path):
    f = tmp_path / "w.txt"
    f.write_text("# header\nattrdict\n\nhyper  # old\nAttrDict\n", encoding="utf-8")
    assert undertake.read_watchlist(f) == ["attrdict", "hyper"]


def test_the_shipped_watchlist_is_valid():
    names = undertake.read_watchlist(ROOT / "undertaker" / "watchlist.txt")
    assert len(names) >= 10 and all(n.replace("-", "").replace("_", "").replace(".", "").isalnum() for n in names)


def test_the_script_runs_end_to_end_and_the_second_night_notices_a_new_release(tmp_path, monkeypatch):
    state, rep, wl = tmp_path / "state.json", tmp_path / "REPORT.md", tmp_path / "w.txt"
    wl.write_text("deadpkg\n", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["undertake", "--watchlist", str(wl), "--state", str(state), "--report", str(rep)])
    first = pypi_client(FIXABLE_ROUTES)
    monkeypatch.setattr(undertake, "httpx", SimpleNamespace(Client=lambda **_: first))
    assert undertake.main() == 0 and state.exists() and "deadpkg" in rep.read_text(encoding="utf-8")
    newer = {**FIXABLE_ROUTES, "https://pypi.org/pypi/deadpkg/json": httpx.Response(200, json=pypi_payload(uploaded="2026-11-01T00:00:00.000000Z"))}
    second = pypi_client(newer)
    monkeypatch.setattr(undertake, "httpx", SimpleNamespace(Client=lambda **_: second))
    assert undertake.main() == 0
    assert "new release appeared" in rep.read_text(encoding="utf-8")


def test_a_corrupt_state_file_stops_the_run_without_touching_it(tmp_path, monkeypatch):
    state, wl = tmp_path / "state.json", tmp_path / "w.txt"
    state.write_text("not json", encoding="utf-8")
    wl.write_text("deadpkg\n", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["undertake", "--watchlist", str(wl), "--state", str(state), "--report", str(tmp_path / "r.md")])
    assert undertake.main() == 2 and state.read_text(encoding="utf-8") == "not json"
