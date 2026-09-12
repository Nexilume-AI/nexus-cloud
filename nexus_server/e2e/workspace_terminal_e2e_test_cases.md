# Computer Terminal E2E Test Cases

## Server prerequisite

Run the backend through the ASGI application:

```bash
python -m uvicorn config.asgi:application --host 0.0.0.0 --port 8000
```

Do not use `python manage.py runserver` for these cases. A successful WebSocket handshake returns `101 Switching Protocols`; an HTTP 200 response from `/ws/workspace-terminals/...` means the ASGI WebSocket handler was bypassed.

## Fake runner

Environment:

```bash
NEXUS_WORKSPACE_SSH_RUNNER=fake
```

Cases:

- Create SSH target.
- Test SSH target.
- Create terminal session.
- Connect to `/ws/workspace-terminals/{session_id}/`.
- Send input frame: `{"type":"input","data":"echo hello\n"}`.
- Receive output frame with terminal text.
- Close terminal session.

## Real SSH runner

Environment:

```bash
NEXUS_WORKSPACE_SSH_RUNNER=paramiko
```

Cases:

- Create private-key Linux/macOS SSH target and open terminal.
- Create password Linux/macOS SSH target and open terminal.
- Create password Windows OpenSSH target and open PowerShell terminal.
- Verify Windows target registration does not require `sh`, `tar`, `curl`, `wget`, `npm`, or Node.js.

## Security checks

- WebSocket with neither a valid authenticated browser session nor a valid bearer token is rejected.
- WebSocket with an authenticated same-origin browser session is accepted.
- WebSocket with a valid bearer token remains compatible.
- WebSocket with an untrusted browser Origin is rejected.
- WebSocket with mismatched `tenant_id` is rejected.
- REST responses never include SSH private key, password, or encrypted secret fields.
- Terminal transcript output is not persisted to audit logs.
