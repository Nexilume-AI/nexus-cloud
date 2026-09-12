# Computer Terminal Constraints

Computer is a remote SSH terminal gateway. It does not install, start, proxy, or embed any remote IDE.

For development and production startup instructions, see the [Nexus Server README](../README.md).

## Scope

- Computer targets are SSH connections.
- Terminal sessions are short-lived interactive shells over WebSocket.
- Linux and macOS targets use the target user's default shell unless a shell is requested.
- Windows OpenSSH targets use PowerShell when detected or requested.
- Tool setup features read and update remote Codex CLI configuration over the selected SSH terminal session.
- Tool setup features allow users to download the generated config files and, where needed, environment files for manual review or transfer.
- Terminal session creation automatically probes for `codex` and `claude` commands on the SSH target.
- Tool detection is best-effort and stores only command presence, path, version, runner, timestamp, and non-secret error text.
- Tool setup shell mode is inferred from the selected SSH target facts and terminal session shell; users do not manually choose Bash vs PowerShell.
- API configuration must come from a selected Provider Runtime credentials export.
- MCP configuration must come from the canonical selected Agent MCP export and therefore carries the current caller Workspace/Project scope. Computer must not reconstruct the endpoint or tenant headers itself.
- Codex configuration must be generated as `$CODEX_HOME/nexus.config.toml` and launched with `codex --profile nexus`.
- Codex provider profiles must use the Nexus OpenAI-compatible base URL, `wire_api = "responses"`, and `experimental_bearer_token` from the selected Provider Runtime credentials export.
- Codex API and MCP operations must be independent: applying API provider settings must preserve existing MCP servers, and MCP add/replace/remove/clear operations must preserve API provider settings.
- Generated Codex commands must not overwrite the user's base `~/.codex/config.toml`.
- Generated Claude Code configuration commands must create a backup under the target user's home directory before overwriting Claude Code config files.
- Downloaded config and environment files are generated client-side from the current export result and are not persisted by Computer.
- Generated rollback commands must restore the latest matching backup when available.

## Security

- SSH private keys and passwords are encrypted server-side and are never returned to the browser.
- Terminal WebSocket connections require the same authenticated user context as the REST API.
- Terminal access requires tenant admin permission.
- Terminal output is streamed to the browser and is not persisted by default.
- API keys entered in the UI are used only to compose commands and are not sent to REST APIs or stored.
- Provider runtime API keys are fetched only through the existing credentials export flow and are not persisted by Computer.
- Audit logs record target/session lifecycle events, not terminal transcript contents.

## Runtime

- Computer terminal WebSockets require the ASGI application in `config.asgi:application`.
- Start the backend with `python -m uvicorn config.asgi:application --host 0.0.0.0 --port 8000`.
- Do not use `python manage.py runserver` for Computer terminal operation. In this project it handles `/ws/workspace-terminals/...` as ordinary HTTP instead of upgrading the connection.
- A successful WebSocket handshake returns `101 Switching Protocols`. An HTTP 200 response for `/ws/workspace-terminals/...` indicates that the ASGI handler was bypassed.
- `NEXUS_WORKSPACE_SSH_RUNNER=fake` is for local UI development.
- `NEXUS_WORKSPACE_SSH_RUNNER=paramiko` is required for real SSH terminal access.
- Password SSH is supported by the Paramiko runner and does not require `sshpass`.
- Windows targets do not require `sh`, `tar`, `curl`, `wget`, `npm`, or Node.js.

## Request path

```text
Nexus Console
    |
    | ws:// or wss:// /ws/workspace-terminals/{session_id}/
    v
Vite development proxy or production reverse proxy
    |
    | WebSocket upgrade
    v
Uvicorn -> config.asgi:application -> WorkspaceASGIProxy
    |
    | authenticated terminal channel
    v
SSH target
```

The REST API and Computer WebSocket gateway share the same port. The WebSocket route is intercepted by `WorkspaceASGIProxy`; all other requests continue to Django.

## API Surface

- `GET /api/v1/workspace-connections/`
- `POST /api/v1/workspace-connections/`
- `POST /api/v1/workspace-connections/{connection_id}/test/`
- `POST /api/v1/workspace-terminal-sessions/`
- `POST /api/v1/workspace-terminal-sessions/{session_id}/close/`
- `POST /api/v1/workspace-terminal-sessions/{session_id}/tool-status/`
- `GET /api/v1/workspace-terminal-sessions/{session_id}/tool-config/?tool=codex`
- `POST /api/v1/workspace-terminal-sessions/{session_id}/tool-config/apply/`
- `POST /api/v1/workspace-terminal-sessions/{session_id}/tool-config/rollback/`
- `WS /ws/workspace-terminals/{session_id}/?token=...&tenant_id=...`

## Removed Surface

The following concepts are intentionally removed:

- runtime install plans
- remote IDE proxying
- remote IDE password generation
- managed install paths
- remote/local tunnel ports
- WebSocket proxying for an upstream IDE
