# CLAUDE.md — nAPPLE

Repository contract and contributor guide for `nAPPLE`, the MCP server for the App Store Connect API.
Read this file before changing the key configuration, the HTTP client, the build upload, or documentation.

## What this repository builds

`nAPPLE` gives MCP tools that read and change App Store Connect assets with a team API key.

| Part | Path | Responsibility |
| --- | --- | --- |
| MCP and CLI doors | [server.py](napple/server.py), [main.py](napple/main.py), [cli.py](napple/cli.py) | MCP tool surface on stdio and the `napple <noun> <verb>` CLI |
| Key configuration and JWT | [config.py](napple/config.py), [auth.py](napple/auth.py) | Key file checks and ES256 JWTs |
| HTTP client | [client.py](napple/client.py) | Requests, bounded GET retry, and pagination inside the API root |
| Build upload | [upload.py](napple/upload.py) | Upload root check, upload, commit, and poll |
| Errors | [errors.py](napple/errors.py) | One typed error for each failure |

## Local checks and commands

Run these commands from the repository root with the pinned Python version and `uv.lock`:

| Need | Command |
| --- | --- |
| Sync workspace dependencies | `uv sync --group dev` |
| Lint and format check | `uv run ruff check .` and `uv run ruff format --check .` |
| Full unit suite | `uv run --group dev pytest tests -q` |
| Lockfile consistency | `uv lock --check` |

The tests use a local HTTP server. They never reach Apple.
Never put a real key in a test fixture.

## Code boundaries

1. **Owner approval for writes:** each write tool changes a real Apple asset. The caller asks the owner before each write call.
2. **Key custody:** the key file must have mode 600. The key and the JWT never enter a tool result or a log.
3. **Bounded network:** the client never retries POST or PATCH, and pagination never leaves the API root.
4. **Upload root:** with `--upload-root`, the build upload reads only a regular file inside that root.
5. **Typed failures:** each failure raises one typed error. Do not encode a failure as an empty value.

## Contribution

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/SETUP.md](docs/SETUP.md) before a change.
Write the regression test first for any behavior change. Observe the test fail (`RED`) before you edit product code.
Run the checks in [docs/QUALITY.md](docs/QUALITY.md) before a completion claim.
