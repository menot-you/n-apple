"""The stdio transport of `napple mcp serve`, run as a real subprocess.

The server starts with no App Store Connect variables, so the tool list must
work without a key, and a call must fail as an error result that names the gap.
"""

from __future__ import annotations

import os
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from napple import cli
from napple.server import TOOLS, Tools

MEMBER = Path(__file__).resolve().parents[1]


def test_stdio_lists_the_ten_tools_and_reports_a_missing_key(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("NOTT_NAPPLE_")}
    # An empty HOME, so the server never reaches the live key directory.
    env["HOME"] = str(tmp_path)
    params = StdioServerParameters(
        command="uv",
        args=["run", "napple", "mcp", "serve"],
        cwd=MEMBER,
        env=env,
    )

    async def run() -> tuple[str | None, list[str], str, bool]:
        with anyio.fail_after(60):
            return await session_run()

    async def session_run() -> tuple[str | None, list[str], str, bool]:
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            started = await session.initialize()
            listed = await session.list_tools()
            result = await session.call_tool("napple_account_show", {})
            text = result.content[0].text  # type: ignore[union-attr]
            names = [tool.name for tool in listed.tools]
            return started.instructions, names, text, result.is_error

    instructions, names, text, is_error = anyio.run(run)
    # The plugin law does not name nAPPLE, so its trigger is these instructions.
    assert instructions is not None
    assert "napple_build_upload" in instructions
    assert "Ask the owner" in instructions
    assert names == [tool["name"] for tool in TOOLS]
    assert len(names) == 10
    assert is_error
    assert text.startswith("ConfigError: not set: NOTT_NAPPLE_KEY_ID")


def test_mcp_serve_takes_the_upload_root_into_the_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    served: list[Tools] = []
    monkeypatch.setattr(cli, "serve_stdio", served.append)

    assert cli.main(["mcp", "serve", "--upload-root", str(tmp_path)]) == 0
    assert cli.main(["mcp", "serve"]) == 0

    assert [tools.upload_root for tools in served] == [tmp_path, None]
