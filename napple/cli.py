"""napple: `napple <noun> <verb>`. `mcp serve` is the MCP server, on stdio.

`COMMANDS` is the only list of commands. Every handler is a `cmd_<noun>_<verb>`
bound in it once. A spelling that is not in the table is an unknown command, and
a bare `napple` is a usage error rather than a server.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final, NamedTuple

from . import config
from .client import Client
from .main import serve_stdio
from .server import Tools


def _flags(noun: str, verb: str) -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog=f"napple {noun} {verb}", description=COMMANDS[(noun, verb)].summary
    )


def _client() -> Client:
    return Client(config.load())


def cmd_mcp_serve(args: list[str]) -> int:
    parser = _flags("mcp", "serve")
    parser.add_argument(
        "--upload-root",
        type=Path,
        help="read napple_build_upload files only inside this directory",
    )
    parsed = parser.parse_args(args)
    serve_stdio(Tools(_client, upload_root=parsed.upload_root))
    return 0


# --- the table ---------------------------------------------------------------------


class Command(NamedTuple):
    synopsis: str
    summary: str
    handler: Callable[[list[str]], int]


COMMANDS: Final[dict[tuple[str, str], Command]] = {
    ("mcp", "serve"): Command(
        "[--upload-root PATH]",
        "serve the App Store Connect tools on stdio",
        cmd_mcp_serve,
    ),
}

USAGE: Final = (
    "usage: napple <noun> <verb> [arguments]   (napple --help lists every command)"
)


def render_help() -> str:
    """The help, generated from COMMANDS in table order."""
    lines = [USAGE, ""]
    for (noun, verb), command in COMMANDS.items():
        head = f"napple {noun} {verb} {command.synopsis}".rstrip()
        lines.append(f"  {head}\n      {command.summary}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    match args:
        case ["--help" | "-h" | "help"]:
            print(render_help())
            return 0
        case [noun, verb, *rest] if (noun, verb) in COMMANDS:
            return COMMANDS[(noun, verb)].handler(rest)
        case []:
            sys.stderr.write(f"napple: {USAGE}\n")
            return 2
        case _:
            sys.stderr.write(f"unknown command: napple {' '.join(args[:2])}\n{USAGE}\n")
            return 2


if __name__ == "__main__":
    sys.exit(main())
