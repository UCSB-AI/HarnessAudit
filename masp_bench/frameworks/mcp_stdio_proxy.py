"""Legacy stdio-to-SSE MCP proxy helper.

This module is an optional compatibility shim for clients that need a stdio MCP
server while the upstream MASP tools server is exposed over SSE.

    client ↔ stdio ↔ this proxy ↔ SSE ↔ MCP bank server

Codex no longer relies on this helper in the default MASP harness path; the
current Codex integration uses URL-based streamable-http MCP configuration
directly. This proxy is kept around as a fallback utility and for older setups.

Example usage in a client config.toml::

    [mcp_servers.masp_tools]
    command = "python"
    args = ["-m", "masp_bench.frameworks.mcp_stdio_proxy", "--url", "http://127.0.0.1:PORT/sse"]

The proxy connects to the SSE MCP server as a client and exposes the same
tools via stdio to the calling process.
"""

from __future__ import annotations

import argparse
import asyncio


async def _run_proxy(sse_url: str) -> None:
    from mcp.client.session import ClientSession
    from mcp.client.sse import sse_client
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    # 1. Connect to the upstream SSE MCP server
    async with sse_client(sse_url) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as client:
            await client.initialize()

            # 2. List tools from upstream
            upstream_tools = await client.list_tools()

            # 3. Create a stdio MCP server that mirrors those tools
            server = Server("masp_tools_proxy")

            @server.list_tools()
            async def list_tools():
                return upstream_tools.tools

            @server.call_tool()
            async def call_tool(name: str, arguments: dict | None = None):
                result = await client.call_tool(name, arguments or {})
                return result.content

            # 4. Run the stdio server
            async with stdio_server() as (read, write):
                await server.run(read, write, server.create_initialization_options())


def main() -> None:
    parser = argparse.ArgumentParser(description="stdio-to-SSE MCP proxy")
    parser.add_argument("--url", required=True, help="Upstream SSE MCP server URL")
    args = parser.parse_args()

    asyncio.run(_run_proxy(args.url))


if __name__ == "__main__":
    main()
