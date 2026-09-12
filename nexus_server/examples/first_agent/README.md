# Nexus Server first Agent

This deterministic FastMCP Agent is the runnable example used by the Nexus Server
getting-started guide. It exposes `run_agent` over Streamable HTTP at
`0.0.0.0:8000/mcp` and does not call an external model provider.

```bash
docker build -t nexus-first-agent:local .
docker save -o nexus-first-agent.tar nexus-first-agent:local
```

Test `tools/list` and `tools/call` directly before uploading:

```bash
docker run --rm -d --name nexus-first-agent-smoke -p 18000:8000 nexus-first-agent:local
python smoke.py --url http://127.0.0.1:18000/mcp
docker stop nexus-first-agent-smoke
```

Register `nexus-first-agent:local` when Nexus Server uses the same Docker daemon,
or upload `nexus-first-agent.tar` from **Agents → Runtime & deployment**.

The container contract always defaults to port `8000`. For a direct host-only
smoke test when that port is occupied, set `NEXUS_AGENT_PORT` and pass the same
port to `smoke.py`; do not change the port in the image uploaded to Nexus.
