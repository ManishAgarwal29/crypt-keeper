"""Tests for revival assembly (task 8). Pure; no network."""
import ast
from datetime import date

from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st

from api.main import app
from coroner.autopsy import run_autopsy
from models import PackageRef, PackageSnapshot, Source
from necromancer.revive import revive, revived_snapshot
from tests.test_rewrite import NEUTRAL, POOL

NOW = date(2026, 1, 1)


def snap(source_map=None, dependencies=None, release="2015-01-01") -> PackageSnapshot:
    return PackageSnapshot(
        ref=PackageRef(source=Source.pypi, identifier="p"),
        last_release_date=release,
        dependencies=dependencies or [],
        source_map=source_map or {},
    )


def do_revive(s: PackageSnapshot, target: str = "3.12"):
    return revive(s, run_autopsy(s, target, NOW))


# --- diffs ---------------------------------------------------------------------------------


def test_diff_has_before_after_unified_and_line_counts():
    s = snap({"pkg/a.py": "from collections import Mapping, deque\nx = 1\n"})
    [d] = do_revive(s).diffs
    assert d.path == "pkg/a.py"
    assert d.before == s.source_map["pkg/a.py"]
    assert d.after == "from collections import deque\nfrom collections.abc import Mapping\nx = 1\n"
    assert d.unified.startswith("--- a/pkg/a.py\n+++ b/pkg/a.py\n")
    assert "-from collections import Mapping, deque" in d.unified
    assert "+from collections.abc import Mapping" in d.unified
    assert (d.lines_added, d.lines_removed) == (2, 1)


def test_only_changed_files_appear_in_diffs():
    s = snap({"a.py": "import collections\nx = collections.Mapping\n", "b.py": "x = 1\n", "c.py": "import imp\n"})
    assert [d.path for d in do_revive(s).diffs] == ["a.py"]


def test_diffs_and_log_are_ordered_by_path_regardless_of_input_order():
    files = {"z.py": "from collections import Mapping\n", "a.py": "from collections import Sequence\n"}
    forward = do_revive(snap(files))
    backward = do_revive(snap(dict(reversed(list(files.items())))))
    assert [d.path for d in forward.diffs] == ["a.py", "z.py"]
    assert forward == backward


def test_unchanged_snapshot_yields_an_empty_revival():
    result = do_revive(snap({"a.py": "x = 1\n"}, ["requests>=2.0"]))
    assert result.diffs == [] and result.applied == [] and result.manual_items == []
    assert result.dependencies == ["requests>=2.0"]


def test_diff_lines_for_a_line_starting_with_dashes_are_counted():
    s = snap({"a.py": "import sys\nx = sys.maxint\n# -- note\n"})
    [d] = do_revive(s).diffs
    assert (d.lines_added, d.lines_removed) == (1, 1)


# --- dependencies ------------------------------------------------------------------------------


def test_revived_dependencies_are_returned_in_order():
    result = do_revive(snap(dependencies=["numpy==1.16.0", "requests>=2", "django<2.0"]))
    assert result.dependencies == ["numpy>=1.26.0", "requests>=2", "django"]
    assert any("numpy" in a for a in result.applied)


def test_dependency_and_source_manual_items_are_combined():
    s = snap({"a.py": "import imp\n"}, ["pkg @ https://x.example/p.tar.gz"])
    result = do_revive(s)
    assert len(result.manual_items) == 2
    assert "direct URL" in result.manual_items[0] and "`imp`" in result.manual_items[1]


def test_target_python_from_the_report_is_respected():
    s = snap({"a.py": "import collections\nx = collections.Mapping\n"}, ["numpy==1.16.0"])
    result = do_revive(s, "3.9")
    assert result.diffs == [] and result.dependencies == ["numpy>=1.19.3"]


# --- revived_snapshot -----------------------------------------------------------------------------


def test_revived_snapshot_keeps_every_file_and_replaces_changed_ones():
    s = snap({"a.py": "from collections import Mapping\n", "b.py": "x = 1\n"}, ["numpy==1.16.0"])
    result = do_revive(s)
    revived = revived_snapshot(s, result)
    assert set(revived.source_map) == {"a.py", "b.py"}
    assert revived.source_map["a.py"] == "from collections.abc import Mapping\n"
    assert revived.source_map["b.py"] == "x = 1\n"
    assert revived.dependencies == ["numpy>=1.26.0"]


def test_revived_snapshot_does_not_modify_the_original():
    s = snap({"a.py": "from collections import Mapping\n"}, ["numpy==1.16.0"])
    revived_snapshot(s, do_revive(s))
    assert s.source_map == {"a.py": "from collections import Mapping\n"}
    assert s.dependencies == ["numpy==1.16.0"]


def test_revived_snapshot_with_a_bare_result_keeps_the_original_dependencies():
    from models import RevivalResult
    s = snap(dependencies=["numpy==1.16.0"])
    assert revived_snapshot(s, RevivalResult()).dependencies == ["numpy==1.16.0"]


def test_reviving_a_revived_snapshot_changes_nothing():
    s = snap(
        {"a.py": "import collections\nx = collections.Mapping\n", "b.py": "import imp\n"},
        ["numpy==1.16.0", "django<2"],
    )
    first = do_revive(s)
    again = do_revive(revived_snapshot(s, first))
    assert again.diffs == [] and again.applied == []
    assert again.dependencies == first.dependencies
    assert again.manual_items == [m for m in first.manual_items if "`imp`" in m]


# --- API ------------------------------------------------------------------------------------------------


def test_resurrect_endpoint_returns_dependencies_and_unified_diffs():
    client = TestClient(app)
    payload = snap({"a.py": "from collections import Mapping\n"}, ["numpy==1.16.0"]).model_dump(mode="json")
    r = client.post("/resurrect", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["dependencies"] == ["numpy>=1.26.0"]
    assert body["diffs"][0]["unified"].startswith("--- a/a.py")


# --- property-based ----------------------------------------------------------------------------------------

programs = st.lists(st.sampled_from(POOL + NEUTRAL + ["import imp\n"]), max_size=6).map("".join)
file_maps = st.dictionaries(st.from_regex(r"[a-z]{1,6}/[a-z]{1,6}\.py", fullmatch=True), programs, max_size=4)
dep_lists = st.lists(
    st.sampled_from(["numpy==1.16.0", "requests>=2", "django<2", "six", "pyyaml==3.12", "oops !!", "scipy~=1.2.0"]),
    max_size=6,
)


@given(file_maps, dep_lists)
def test_revival_never_drops_a_path_or_dependency_and_stays_valid(files, deps):
    s = snap(files, deps)
    result = do_revive(s)
    revived = revived_snapshot(s, result)
    assert set(revived.source_map) == set(files)
    assert len(revived.dependencies) == len(deps)
    assert {d.path for d in result.diffs} <= set(files)
    for d in result.diffs:
        assert d.before != d.after
        ast.parse(d.after)
    for content in revived.source_map.values():
        ast.parse(content)


@given(file_maps, dep_lists)
def test_revival_is_idempotent_and_deterministic(files, deps):
    s = snap(files, deps)
    first = do_revive(s)
    assert first == do_revive(s)
    again = do_revive(revived_snapshot(s, first))
    assert again.diffs == [] and again.applied == []
    assert again.dependencies == first.dependencies
