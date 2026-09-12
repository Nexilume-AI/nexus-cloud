from __future__ import annotations

import argparse
import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main(url: str) -> None:
    async with streamable_http_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            if "run_agent" not in names:
                raise RuntimeError(f"run_agent is missing; tools={names}")
            result = await session.call_tool(
                "run_agent",
                {"task": "first call", "context": "source=smoke", "mode": "verify"},
            )
            if result.isError:
                raise RuntimeError(f"run_agent failed: {result.content}")
            print(f"tools={names}")
            print(f"result={result.content}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:18000/mcp")
    args = parser.parse_args()
    asyncio.run(main(args.url))
