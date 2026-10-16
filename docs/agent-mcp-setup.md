# Connect an Agent to your development tools

Open an Agent's access page and select your MCP client. Nexus loads the connection
configuration automatically and remembers your client choice, not credentials.

| Client | Configuration file | Credential reference |
| --- | --- | --- |
| Claude Code | `.mcp.json` | `${NEXUS_API_KEY}` |
| Codex | `~/.codex/config.toml` | `bearer_token_env_var` |
| Cursor | `.cursor/mcp.json` | `${env:NEXUS_API_KEY}` |
| VS Code / GitHub Copilot | `.vscode/mcp.json` | Masked input prompt |

Use the Personal Tool Setup credential authorized for the Agent. The access page
does not issue, recover, rotate, or revoke credentials. Computer Tool Setup remains
the managed-credential workflow for this distribution; no enterprise key service
is required or included.

**Copy configuration** copies exactly the preview. Merge the server entry into
your existing file without replacing other servers. For environment references,
set the variable in the environment that launches the client and restart it; a
project `.env` file is not automatically loaded. VS Code prompts for the key.
Keep credentials out of Git and do not share them in screenshots or logs.

Configuration generation does not prove that a client is connected. Reload the
chosen client and use its MCP tool-discovery view to verify the connection.
