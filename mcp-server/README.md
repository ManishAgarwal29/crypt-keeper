# Crypt Keeper MCP Server

Exposes two exhumation tools to Kiro. They reuse `backend/api/exhumer.py`, so Kiro and the app see
the same data.

- `get_pypi_metadata(name)`: last release date, declared Pythons, dependencies, yanked versions.
- `get_repo_tree(url)`: a GitHub repository's Python files.

Both return JSON. Bad input or an upstream failure comes back as `{"error": ..., "status": ...}`
instead of an exception.

## Install

```powershell
pip install mcp httpx packaging pydantic
```

Works with `mcp` 1.x (`FastMCP`) and 2.x (`MCPServer`). A test in `backend/tests/test_mcp_tools.py`
starts the server over stdio and calls it, so an SDK change shows up in the test run.

## Register it in Kiro

In Kiro, open the command palette, search for **MCP**, and open the workspace MCP config
(`.kiro/settings/mcp.json`). Add:

```json
{
  "mcpServers": {
    "crypt-keeper": {
      "command": "python",
      "args": ["mcp-server/server.py"],
      "env": { "FASTMCP_LOG_LEVEL": "ERROR" },
      "disabled": false,
      "autoApprove": ["get_pypi_metadata", "get_repo_tree"]
    }
  }
}
```

Then ask Kiro, for example: "Use crypt-keeper to look up attrdict's last release and declared
Python versions."

## Run it by hand

```powershell
python mcp-server/server.py
```

It speaks MCP over stdio, so it waits for a client.
