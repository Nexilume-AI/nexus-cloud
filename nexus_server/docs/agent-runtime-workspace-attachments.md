# Agent Runtime Workspace Attachments

Agent Docker runtimes stay sandboxed by default. They do not mount a user's local or remote filesystem. When a user explicitly attaches a Remote Workspace during deploy, Nexus gives the runtime a scoped API token for file operations under one authorized root.

## Web Flow

1. Open `Remote Workspace`.
2. Create an SSH workspace connection for the target machine.
3. Open `Agents`.
4. Click `Deploy` on the Agent.
5. In `Workspace attachment`, select the SSH workspace.
6. Set `Authorized root`, for example `~/project` or `D:/work/customer-a`.
7. Choose `Read only` or `Read/write`.
8. Deploy the Agent runtime.

After deploy, the Agent container reads and writes through the Nexus workspace API. The SSH private key or password is never injected into the Agent container.

## Deploy-Time Attachment

`POST /api/v1/agents/{agent_id}/runtime/deployments/` accepts:

```json
{
  "env": "prod",
  "workspace_connection_id": "00000000-0000-0000-0000-000000000000",
  "workspace_root": "~/project",
  "workspace_access_mode": "read_only"
}
```

`workspace_access_mode` is `read_only` or `read_write`.

When attached, the container receives:

```text
NEXUS_WORKSPACE_CONNECTION_ID
NEXUS_WORKSPACE_ROOT
NEXUS_WORKSPACE_ACCESS_MODE
NEXUS_WORKSPACE_API_URL
NEXUS_WORKSPACE_TOKEN
```

## Agent Runtime Rules

The Agent must follow these rules:

- Treat `NEXUS_WORKSPACE_API_URL` as the only file-system entry point for attached SSH workspaces.
- Do not assume the SSH directory is mounted inside the container.
- Use only relative paths such as `README.md` or `src/app.py`.
- Do not send absolute paths such as `/home/user/project/file.txt`, `C:\work\file.txt`, or `D:/work/file.txt`.
- Do not use `..` path traversal. Nexus rejects paths that escape `NEXUS_WORKSPACE_ROOT`.
- Respect `NEXUS_WORKSPACE_ACCESS_MODE`.
- Use read endpoints only when mode is `read_only`.
- Use write endpoints only when mode is `read_write`.
- Run command endpoints only when mode is `read_write`.
- Include `Authorization: Bearer ${NEXUS_WORKSPACE_TOKEN}` on every workspace API request.
- Emit Display events separately through AG-UI if the user should see changed files in Display.

Nexus enforces these constraints server-side. The Agent should still implement them client-side so errors are clear and predictable.

Command execution has a broader trust boundary than file read/write. Nexus starts the command in the requested directory under `NEXUS_WORKSPACE_ROOT`, but the remote shell still runs with the configured SSH user's OS permissions. A command can reference absolute paths if the SSH account can access them. Only attach `read_write` workspaces to Agents that are trusted to run commands on that SSH target.

## Runtime Workspace API

Use `Authorization: Bearer ${NEXUS_WORKSPACE_TOKEN}` for all calls.

List files:

```text
GET ${NEXUS_WORKSPACE_API_URL}files/?path=.
```

Read a file:

```text
GET ${NEXUS_WORKSPACE_API_URL}files/read/?path=README.md
```

Write a file:

```text
POST ${NEXUS_WORKSPACE_API_URL}files/write/
Content-Type: application/json

{"path":"notes/todo.txt","content":"..."}
```

Run a command:

```text
POST ${NEXUS_WORKSPACE_API_URL}commands/run/
Content-Type: application/json

{"cwd":"src","command":"npm test -- --runInBand","timeout_seconds":60}
```

The response includes:

```json
{
  "cwd": "~/project/src",
  "command": "npm test -- --runInBand",
  "exit_code": 0,
  "stdout": "...",
  "stderr": "",
  "timed_out": false,
  "timeout_seconds": 60
}
```

The `path` parameter is always relative to `NEXUS_WORKSPACE_ROOT`. Absolute paths and `..` traversal are rejected. Writes require `workspace_access_mode=read_write`.

For commands, `cwd` is also relative to `NEXUS_WORKSPACE_ROOT`; absolute `cwd` values and `..` traversal are rejected. Commands require `workspace_access_mode=read_write`.

## Minimal Agent Example

This is the expected shape inside an Agent container:

```python
from __future__ import annotations

import json
import os
from urllib.parse import quote
from urllib.request import Request, urlopen


WORKSPACE_API_URL = os.environ.get("NEXUS_WORKSPACE_API_URL", "").rstrip("/") + "/"
WORKSPACE_TOKEN = os.environ.get("NEXUS_WORKSPACE_TOKEN", "")
WORKSPACE_MODE = os.environ.get("NEXUS_WORKSPACE_ACCESS_MODE", "read_only")


def workspace_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {WORKSPACE_TOKEN}",
        "Accept": "application/json",
    }


def read_workspace_file(path: str) -> str:
    url = WORKSPACE_API_URL + "files/read/?path=" + quote(path)
    request = Request(url, headers=workspace_headers(), method="GET")
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload["data"]["content"]


def write_workspace_file(path: str, content: str) -> None:
    if WORKSPACE_MODE != "read_write":
        raise RuntimeError("Workspace is not attached with read_write access.")
    body = json.dumps({"path": path, "content": content}).encode("utf-8")
    headers = workspace_headers()
    headers["Content-Type"] = "application/json"
    request = Request(WORKSPACE_API_URL + "files/write/", data=body, headers=headers, method="POST")
    with urlopen(request, timeout=30) as response:
        response.read()


def run_workspace_command(command: str, cwd: str = ".", timeout_seconds: int = 30) -> dict:
    if WORKSPACE_MODE != "read_write":
        raise RuntimeError("Workspace is not attached with read_write access.")
    body = json.dumps({"command": command, "cwd": cwd, "timeout_seconds": timeout_seconds}).encode("utf-8")
    headers = workspace_headers()
    headers["Content-Type"] = "application/json"
    request = Request(WORKSPACE_API_URL + "commands/run/", data=body, headers=headers, method="POST")
    with urlopen(request, timeout=timeout_seconds + 5) as response:
        return json.loads(response.read().decode("utf-8"))["data"]


original = read_workspace_file("agent-input.md")
updated = original + "\nAgent updated this file.\n"
write_workspace_file("agent-output.md", updated)
test_result = run_workspace_command("python -m pytest", cwd=".", timeout_seconds=60)
if test_result["exit_code"] != 0:
    raise RuntimeError(test_result["stderr"] or test_result["stdout"])
```

The example paths are relative to `NEXUS_WORKSPACE_ROOT`. If the deploy form uses `workspace_root=~/project`, then `agent-input.md` resolves to `~/project/agent-input.md` on the SSH target.

## Display Events

Writing a file through the workspace API changes the SSH target, but it does not automatically show the file in Display. If the user should see the file under `All Files in This Task`, the Agent must also emit an AG-UI custom file event:

```json
{
  "type": "CUSTOM",
  "name": "nexus.file.updated",
  "value": {
    "name": "agent-output.md",
    "kind": "markdown",
    "description": "Updated through attached SSH workspace",
    "content": "..."
  }
}
```

Send Display events to `NEXUS_AGUI_EVENTS_URL` with `X-Nexus-AGUI-Token: ${NEXUS_AGUI_TOKEN}`.

## Local SSH E2E Test

The opt-in test `tests.test_agent_workspace_local_ssh_e2e` verifies the full flow against a local SSH server:

1. Create an Agent and runtime image.
2. Register an SSH Remote Workspace pointing at the local computer.
3. Deploy the Agent with `workspace_access_mode=read_write`.
4. Read a local file through the runtime workspace API.
5. Run a command from the attached workspace root through `commands/run/`.
6. Emit a `nexus.file.updated` Display event.
7. Write the updated content back to the local computer.
8. Verify the Display page includes the file event and the local output file was written.

It is skipped by default. Enable it with:

```text
NEXUS_LOCAL_SSH_E2E=1
NEXUS_LOCAL_SSH_HOST=127.0.0.1
NEXUS_LOCAL_SSH_PORT=22
NEXUS_LOCAL_SSH_USER=<local ssh user>
NEXUS_LOCAL_SSH_PRIVATE_KEY_PATH=<path to private key>
```

Or use `NEXUS_LOCAL_SSH_PASSWORD` for password auth. Use `NEXUS_LOCAL_SSH_E2E_ROOT` when the remote SSH path differs from the local temp path you want the test to use.

Run:

```text
python manage.py test tests.test_agent_workspace_local_ssh_e2e
```
