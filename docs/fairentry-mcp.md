# FairEntry MCP Integration

FairEntry exposes a small MCP server so ChatGPT, Codex, Claude, and Claude Code
can query the same data the web app uses.

## What It Can Read

- `web/data/board.json`
- `web/data/backtest.json`

## What It Can Write

- `data/mcp_state.json` for local dummy portfolio positions and stock notes.
- This file is ignored by git.
- Refresh commands are returned as instructions only; MCP tools do not execute
  shell commands.

## Local MCP

Run the stdio server:

```powershell
python -m fairentry.mcp.stdio_server
```

### Claude Desktop

Add this to your Claude Desktop config, adjusting the absolute path if needed:

```json
{
  "mcpServers": {
    "fairentry": {
      "command": "python",
      "args": ["-m", "fairentry.mcp.stdio_server"],
      "cwd": "C:\\Users\\sbpk5\\Documents\\Pill\\ai_ground\\FairEntry"
    }
  }
}
```

### Claude Code

From the repo:

```powershell
claude mcp add fairentry -- python -m fairentry.mcp.stdio_server
claude mcp list
```

If you already configured it in Claude Desktop, Claude Code can import Desktop
MCP servers on supported setups:

```powershell
claude mcp add-from-claude-desktop
```

### Codex

Add a project or global MCP server entry:

```toml
[mcp_servers.fairentry]
command = "python"
args = ["-m", "fairentry.mcp.stdio_server"]
cwd = "C:\\Users\\sbpk5\\Documents\\Pill\\ai_ground\\FairEntry"
```

## Remote MCP

Run locally over HTTP:

```powershell
$env:FAIRENTRY_MCP_TOKEN="change-me"
$env:FAIRENTRY_MCP_HOST="0.0.0.0"
$env:FAIRENTRY_MCP_PORT="8789"
python -m fairentry.mcp.http_server
```

Endpoint:

```text
POST http://localhost:8789/mcp
Authorization: Bearer change-me
```

Deploy this behind HTTPS before connecting it to ChatGPT or Claude web/API.

## Tool List

- `get_board_summary`
- `get_stock`
- `find_stocks`
- `compare_stocks`
- `explain_score`
- `get_backtest_summary`
- `ask_fairentry`
- `add_portfolio_position`
- `list_portfolio`
- `close_portfolio_position`
- `save_stock_note`
- `list_stock_notes`
- `get_refresh_instructions`

## Example Questions

- "Which Buy stocks have weak price demand?"
- "Compare ATAT, RELY, and MU."
- "Explain why ATAT is a Buy."
- "Find cheap stocks with upside above 30%."
- "Add 100 shares of ATAT to my dummy portfolio."
- "Save a note on RELY: watch remittance volume next quarter."

## Safety Notes

- This is research tooling, not financial advice.
- The remote server should use `FAIRENTRY_MCP_TOKEN`.
- Do not expose private local files through new tools unless you intend to.
- Keep trading/execution actions out of this MCP unless you add explicit auth,
  audit logs, and confirmation gates.
