"""Tests for the MCP tool functions (task 9, requirement 6.3). No network."""
import json
import os
import sys

import pytest

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


# --- the server itself, over stdio, the way Kiro talks to it ---------------------------------------------------


def test_server_starts_and_serves_both_tools_over_stdio():
    """Catches SDK breakage (the mcp 2.x FastMCP rename) that the plain tool-function tests cannot."""
    pytest.importorskip("mcp")
    import asyncio
    from pathlib import Path

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    script = Path(__file__).resolve().parents[2] / "mcp-server" / "server.py"

    async def talk() -> tuple[list[str], dict]:
        params = StdioServerParameters(command=sys.executable, args=[str(script)])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = [t.name for t in (await session.list_tools()).tools]
                bad = await session.call_tool("get_pypi_metadata", {"name": "../etc/passwd"})  # rejected before any network call
                return names, json.loads(bad.content[0].text)

    names, error = asyncio.run(asyncio.wait_for(talk(), timeout=60))
    assert sorted(names) == ["get_pypi_metadata", "get_repo_tree"]
    assert error["status"] == 422 and "valid PyPI package name" in error["error"]
