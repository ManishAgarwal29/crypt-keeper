"""Explaining a failure. The error texts are the real ones the sandbox reported in the benchmark runs."""
import pytest
from hypothesis import given
from hypothesis import strategies as st

from models import ProofOfLife
from proof.diagnosis import diagnose


def dead(error: str) -> ProofOfLife:
    return ProofOfLife(imported=False, error=error)


SITE = "<sandbox>/venv/lib/python3.12/site-packages"


@pytest.mark.parametrize("error, paths, category, says", [
    ("SyntaxError: multiple exception types must be parenthesized (webapp2.py, line 571)", [], "python2-syntax", "2to3"),
    ("ModuleNotFoundError: No module named 'imp'", [], "removed-from-python", "imp"),
    ("ModuleNotFoundError: No module named 'distutils'", [], "removed-from-python", "setuptools"),
    ("ModuleNotFoundError: No module named 'Queue'", [], "removed-from-python", "queue"),
    ("ModuleNotFoundError: No module named 'cursor'", ["mongokit/__init__.py", "mongokit/cursor.py"], "python2-relative-import", "from . import cursor"),
    ("ModuleNotFoundError: No module named 'Workbook'", ["pyExcelerator/__init__.py", "pyExcelerator/Workbook.py"], "python2-relative-import", "pyExcelerator/Workbook.py"),
    ("ModuleNotFoundError: No module named 'nose'", ["noseconfig.py"], "undeclared-dependency", "nose"),
    ("ModuleNotFoundError: No module named 'six'", ["ipcalc.py"], "undeclared-dependency", "six"),
    ("ModuleNotFoundError: No module named 'mimerender' (could not install: mimerender>=0.5.2)", [], "dependency-not-installable", "mimerender>=0.5.2"),
    ("ModuleNotFoundError: No module named 'webhelpers' (could not install: WebHelpers (>=0.6.4), WebError (>=0.10.3))", [], "dependency-not-installable", "WebHelpers"),
    (f"ImportError: cannot import name 'evalcontextfilter' from 'jinja2' ({SITE}/jinja2/__init__.py)", [], "dependency-removed-a-name", "jinja2"),
    (f"ImportError: cannot import name 'parse_rule' from 'werkzeug.routing' ({SITE}/werkzeug/routing/__init__.py)", [], "dependency-removed-a-name", "werkzeug"),
    ("ImportError: cannot import name 'getargspec' from 'inspect' (/usr/local/lib/python3.12/inspect.py)", [], "removed-from-python", "inspect.getargspec"),
    ("AttributeError: module 'collections' has no attribute 'MutableMapping'", [], "attribute-removed", "collections.MutableMapping"),
    ("DistributionNotFound: The 'fixture' distribution was not found and is required by the application", [], "looks-itself-up", "pip install"),
    ("Import check timed out after 60s.", [], "timed-out", "too long"),
    ("Import check was stopped: it went over the output limit (10 MB).", [], "resource-limit", "writes"),
    ("TypeError: unsupported operand type(s) for +: 'dict_keys' and 'list'", [], "other", "traceback"),
])
def test_each_real_failure_gets_its_cause_and_a_next_step(error, paths, category, says):
    hint = diagnose(dead(error), paths)
    assert hint.category == category and says in hint.next_step, hint
    assert hint.summary in error or error.startswith(hint.summary[:-3])


@pytest.mark.parametrize("error", [
    f"ImportError: cannot import name 'soft_unicode' from 'markupsafe' ({SITE}/markupsafe/__init__.py)",
    "AttributeError: module 'pymongo' has no attribute 'Connection'",
])
def test_pinning_is_only_suggested_with_the_condition_that_an_older_release_installs(error):
    """The rule cannot know whether an old release installs on this Python (no old MarkupSafe does on 3.12), so the
    advice leads with changing the code and keeps the condition on pinning."""
    step = diagnose(dead(error), []).next_step
    assert "if one installs on this Python" in step
    lowered = step.lower()
    assert lowered.index("change the code") < lowered.index("pin"), "changing the code is the advice that always applies"
    assert "<X" not in step, "no placeholder version"


def test_a_windows_path_to_site_packages_is_recognised_too():
    error = r"ImportError: cannot import name 'DSSKey' from 'paramiko' (C:\x\venv\Lib\site-packages\paramiko\__init__.py)"
    assert diagnose(dead(error), []).category == "dependency-removed-a-name"


def test_a_top_level_module_of_the_package_is_not_called_a_relative_import():
    hint = diagnose(dead("ModuleNotFoundError: No module named 'helper'"), ["helper.py", "pkg/__init__.py"])
    assert hint.category == "undeclared-dependency"


def test_the_packages_own_top_level_package_is_never_called_a_relative_import():
    """`pkg/__init__.py` looks like a sibling directory, but `import pkg` of a top-level package is absolute."""
    hint = diagnose(dead("ModuleNotFoundError: No module named 'pkg'"), ["pkg/__init__.py", "pkg/sub.py"])
    assert hint.category != "python2-relative-import"


def test_a_resurrected_package_in_the_api_has_no_hint(monkeypatch):
    from fastapi.testclient import TestClient

    from api import main
    from tests.test_api import pypi_client, pypi_routes

    monkeypatch.setattr(main, "_http_client", lambda: pypi_client(pypi_routes()))
    monkeypatch.setattr(main, "run_proof_of_life", lambda s, t: ProofOfLife(
        imported="collections.abc" in "".join(s.source_map.values()), error=None if "collections.abc" in "".join(s.source_map.values()) else "AttributeError: x",
        python_used="3.12.0", target_python_matched=True))
    body = TestClient(main.app).post("/revive", json={"ref": {"source": "pypi", "identifier": "deadpkg"}}).json()
    assert body["verdict"] == "resurrected" and body["failure_hint"] is None


def test_a_package_that_imported_has_no_hint():
    assert diagnose(ProofOfLife(imported=True), []) is None and diagnose(None, []) is None


def test_a_skip_because_of_the_curated_list_has_no_hint():
    assert diagnose(ProofOfLife(imported=False, skipped=True, error="Proof of life on this server is limited to a curated list"), []) is None


def test_a_placeholder_gets_a_hint_about_what_it_installs():
    proof = ProofOfLife(imported=False, skipped=True, error="No importable ... so there is nothing to run. It looks like a placeholder")
    assert diagnose(proof, ["setup.py"]).category == "nothing-to-run"


def test_a_long_error_is_shortened_in_the_summary():
    hint = diagnose(dead("ModuleNotFoundError: No module named 'x' " + "y" * 500), [])
    assert len(hint.summary) <= 160 and hint.summary.endswith("...")


def test_an_empty_error_still_says_something():
    assert diagnose(dead(""), []).summary


@given(st.text(max_size=300), st.lists(st.text(max_size=20), max_size=5))
def test_any_error_text_gives_one_hint_and_never_raises(error, paths):
    hint = diagnose(dead(error), paths)
    assert hint is not None and hint.category and hint.next_step
