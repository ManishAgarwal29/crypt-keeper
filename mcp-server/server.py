"""Crypt Keeper MCP server: exhumation tools for PyPI and GitHub.

Exposes two tools so Kiro (and the app) share one access layer:
  - get_pypi_metadata(name)
  - get_repo_tree(url)

This is a skeleton using the `mcp` package stdio server. Task 9 implements the real
fetching. Register it in .kiro/settings/mcp.json to use inside Kiro.
"""
from __future__ import annotations

import json
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # keep the skeleton importable before deps are installed
    FastMCP = None  # type: ignore[assignment]


def _build_server() -> Any:
    server = FastMCP("crypt-keeper")

    @server.tool()
    def get_pypi_metadata(name: str) -> str:
        """Fetch PyPI metadata for a package by name. Returns JSON (task 9)."""
        # TODO(task 9): GET https://pypi.org/pypi/{name}/json and return trimmed metadata.
        return json.dumps({"name": name, "status": "not-implemented"})

    @server.tool()
    def get_repo_tree(url: str) -> str:
        """Fetch a repository's file tree by URL. Returns JSON map (task 9)."""
        # TODO(task 9): fetch repo contents; return {path: content}.
        return json.dumps({"url": url, "files": {}, "status": "not-implemented"})

    return server


if __name__ == "__main__":
    if FastMCP is None:
        raise SystemExit("Install the 'mcp' package: pip install mcp")
    _build_server().run()
