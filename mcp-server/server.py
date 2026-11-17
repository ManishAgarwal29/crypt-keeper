"""Crypt Keeper MCP server: exhumation tools for PyPI and GitHub.

Exposes two tools so Kiro and the app share one access layer:
  - get_pypi_metadata(name)
  - get_repo_tree(url)

The fetching logic lives in backend/api/exhumer.py and is reused here. Both tools return
JSON strings; bad input or upstream failures come back as {"error": ..., "status": ...}
instead of raising. Register the server in .kiro/settings/mcp.json (see README).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from api.exhumer import ExhumeError, exhume_github, fetch_pypi_metadata  # noqa: E402

try:  # mcp 2.x renamed FastMCP to MCPServer; the decorator and run() API are the same
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:
    try:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as Server
    except ImportError:  # keep the module importable (and its tool functions testable) without mcp
        Server = None  # type: ignore[assignment,misc]


def _client() -> httpx.Client:
    return httpx.Client(timeout=20, follow_redirects=True)


def _error(exc: ExhumeError) -> str:
    return json.dumps({"error": exc.message, "status": exc.status})


def pypi_metadata_json(name: str) -> str:
    """Trimmed PyPI metadata for `name` as JSON, or a structured error."""
    try:
        with _client() as client:
            return json.dumps(asdict(fetch_pypi_metadata(name, client)))
    except ExhumeError as exc:
        return _error(exc)


def repo_tree_json(url: str, target_python: str = "3.12") -> str:
    """A GitHub repo's Python files as {"files": {path: content}}, or a structured error."""
    try:
        with _client() as client:
            snapshot = exhume_github(url, target_python, client, os.environ.get("GITHUB_TOKEN"))
        return json.dumps({"url": snapshot.ref.identifier, "files": snapshot.source_map})
    except ExhumeError as exc:
        return _error(exc)


def _build_server() -> Any:
    server = Server("crypt-keeper")

    @server.tool()
    def get_pypi_metadata(name: str) -> str:
        """Fetch PyPI metadata for a package by name. Returns JSON."""
        return pypi_metadata_json(name)

    @server.tool()
    def get_repo_tree(url: str) -> str:
        """Fetch the Python files of a GitHub repository by URL. Returns JSON."""
        return repo_tree_json(url)

    return server


if __name__ == "__main__":
    if Server is None:
        raise SystemExit("No supported MCP SDK found (needs mcp 1.x or 2.x): pip install mcp")
    _build_server().run()
