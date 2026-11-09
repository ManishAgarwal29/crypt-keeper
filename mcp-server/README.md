# Crypt Keeper MCP Server

Exposes two exhumation tools to Kiro and the app:
- `get_pypi_metadata(name)` — PyPI metadata for a package.
- `get_repo_tree(url)` — a repository's file tree.

## Install

```powershell
pip install mcp httpx
```

## Register in Kiro

Add this to your MCP config (`.kiro/settings/mcp.json` at the workspace root, or the
user-level `~/.kiro/settings/mcp.json`). You can edit it from Kiro's command palette:
search **"MCP"** and open the config.

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

## Run standalone

```powershell
python mcp-server/server.py
```
