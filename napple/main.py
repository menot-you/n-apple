"""The MCP transport `napple mcp serve` runs, on the official MCP Python SDK.

The SDK owns framing, the handshake and the protocol version on stdio. A tool
failure is an error result that carries the error's class and message, so the
caller can read it and correct the call.
"""

import json
from typing import Any

import anyio
import mcp_types as types
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server

from .server import TOOLS, Tools

SDK_TOOLS = [types.Tool.model_validate(tool) for tool in TOOLS]

INSTRUCTIONS = (
    "nAPPLE: the App Store Connect API for the NOTT Apple apps. Use these tools "
    "when the work reads or changes an Apple asset: use napple_build_list for the "
    "next build number, napple_build_upload to upload an IPA or PKG, "
    "napple_build_show for its processing state, and napple_signing_show to "
    "check a bundle's certificates and profiles. Do not use fastlane, altool or "
    "an ad hoc API call. Ask the owner before each write tool call."
)


def build(tools: Tools) -> Server[Any]:
    """The SDK server answering for `tools`."""

    async def list_tools(
        ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=SDK_TOOLS)

    async def call_tool(
        ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        try:
            result = await anyio.to_thread.run_sync(
                tools.call, params.name, params.arguments or {}
            )
        # The protocol boundary: every failure reaches the caller as an error result.
        except Exception as error:  # noqa: BLE001 - nothing may escape as silence
            text = f"{type(error).__name__}: {error}"
            return types.CallToolResult(
                content=[types.TextContent(text=text)], is_error=True
            )
        return types.CallToolResult(
            content=[types.TextContent(text=json.dumps(result, ensure_ascii=False))],
            structured_content=result,
        )

    return Server(
        "napple",
        version="0.1.0",
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def serve_stdio(tools: Tools) -> None:
    """Serve one session on stdio until its peer stops talking."""
    server = build(tools)

    async def run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(run)
