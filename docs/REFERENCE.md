# nAPPLE reference

The source declarations own argument types, bounds, defaults, and refusal rules.
Use the installed executable's `--help` and MCP `tools/list` for its effective version.
A declared operation can refuse a missing prerequisite.

## CLI commands

Source: [cli.py](../napple/cli.py), `COMMANDS`.
All commands use the executable from the public repository's root workspace.

| Command | Arguments |
| --- | --- |
| `napple mcp serve` | `[--upload-root PATH]` |

## MCP tools

Source: [server.py](../napple/server.py).

| Tool | Effect |
| --- | --- |
| `napple_account_show` | Read |
| `napple_app_list` | Read |
| `napple_build_list` | Read |
| `napple_build_show` | Read |
| `napple_signing_show` | Read |
| `napple_group_list` | Read |
| `napple_build_upload` | Write |
| `napple_bundle_create` | Write |
| `napple_certificate_create` | Write |
| `napple_profile_create` | Write |

## Configuration

Source: [config.py](../napple/config.py).

| Name | Value |
| --- | --- |
| `NOTT_NAPPLE_KEY_ID` | The key ID of the team API key. |
| `NOTT_NAPPLE_ISSUER_ID` | The issuer ID of the team. |
| `NOTT_NAPPLE_KEY_PATH` | The path to the team API key `.p8` file. |

When the environment has none of the three names, the server reads `~/.nott/apple/`.
A partial set of names is a `ConfigError` that names the missing names.

## Errors

Source: [errors.py](../napple/errors.py).
Each failure raises one typed error: `ConfigError`, `AuthError`, `AgreementError`,
`NotFoundError`, `ConflictError`, `RateLimitError`, `ProtocolError` or `UploadError`.
