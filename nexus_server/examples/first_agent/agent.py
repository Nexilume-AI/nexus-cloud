from __future__ import annotations

import os
from typing import Literal

from mcp.server.fastmcp import FastMCP


mcp = FastMCP(
    "nexus-first-agent",
    host="0.0.0.0",
    port=int(os.environ.get("NEXUS_AGENT_PORT", "8000")),
    streamable_http_path="/mcp",
)


@mcp.tool()
def run_agent(
    task: str,
    context: str = "",
    mode: Literal["explain", "code", "verify"] = "explain",
    attachments: list[dict] | None = None,
) -> str:
    """Return a deterministic response that proves the Nexus runtime path works."""
    attachment_count = len(attachments or [])
    return (
        f"mode={mode}\n"
        f"task={task}\n"
        f"context={context}\n"
        f"attachments={attachment_count}"
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
