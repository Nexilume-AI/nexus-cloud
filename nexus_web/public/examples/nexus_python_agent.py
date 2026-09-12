"""Upload this file in Agent → Runtime → Upload Python. No requirements needed.

Nexus starts `server` automatically; do not put initialization in __main__.
Do not hard-code credentials. Read configured secrets inside tool functions.
"""
from nexus_agent.fastmcp import CurrentNexusMCP, NexusMCPServer

server = NexusMCPServer("My first Nexus Agent")


@server.tool(chat=True)
async def hello(message: str, nexus=CurrentNexusMCP()) -> dict:
    """Respond to a message and demonstrate a private invocation trace."""
    with nexus.trace.step("prepare-response"):
        reply = f"Hello! Your Agent received: {message}"
    return {"reply": reply}
