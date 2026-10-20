# nAPPLE runbook

Read and change App Store Connect assets through a bounded MCP server.
Each write tool changes a real Apple asset. Ask the owner before each write call.

## Setup

Read [installation](INSTALLATION.md).
Run from the public repository root:

```bash
uv sync --group dev
uv run napple --help
uv run napple mcp serve
```

Register the absolute `.venv/bin/napple` executable with arguments `mcp serve` in the MCP client.
Restart the client. Compare `tools/list` with the [reference](REFERENCE.md).

## Key configuration

Put the team API key in a file with mode 600. Keep it outside the repository.
Set `NOTT_NAPPLE_KEY_ID`, `NOTT_NAPPLE_ISSUER_ID` and `NOTT_NAPPLE_KEY_PATH` in the process environment.
Alternatively, put one `AuthKey_<key id>.p8` file and one `issuer_id` file in `~/.nott/apple/`.
Do not put key values in commands or reports.
Read [privacy](PRIVACY.md) before you connect a team key.

## First useful result

Call `napple_account_show`. It reads the key, the team and any pending agreement.
A read tool changes nothing in Apple.

## Failures

A `ConfigError` names the missing or unsafe configuration.
An `AgreementError` names a pending agreement. Accept it on the App Store Connect site.
A `RateLimitError` follows three retries of a GET. Wait, then retry the call.
See [troubleshooting](TROUBLESHOOTING.md).

## Backup and restoration

nAPPLE keeps no local store. The team API key file is the only local state.
Keep a private copy of the key file. Apple does not supply the private key again.
To restore, put the key file back with mode 600 and repeat the first useful result.
