"""Tests for the MCP tool functions (task 9, requirement 6.3). No network."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "mcp-server"))

import server  # noqa: E402


def test_invalid_package_name_returns_structured_error():
    result = json.loads(server.pypi_metadata_json("../etc/passwd"))
    assert result["status"] == 422 and "valid PyPI package name" in result["error"]


def test_empty_package_name_returns_structured_error():
    assert json.loads(server.pypi_metadata_json(""))["status"] == 422


def test_invalid_repo_url_returns_structured_error():
    result = json.loads(server.repo_tree_json("https://gitlab.com/o/r"))
    assert result["status"] == 422 and "GitHub" in result["error"]
