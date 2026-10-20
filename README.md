# nAPPLE

nAPPLE is a Python MCP server for the App Store Connect API. It serves the NOTT apps.
It runs on SERVER (Linux). The command `napple mcp serve` serves it on stdio.

## Tools

Read tools change nothing in Apple. Write tools create or upload a real Apple asset.

| Tool | Effect | Purpose |
| --- | --- | --- |
| `napple_account_show` | Read | Shows the key, the team and any pending agreement. The agreement check is fast. |
| `napple_app_list` | Read | Lists the team's apps. |
| `napple_build_list` | Read | Lists builds. Also gives the next build number. |
| `napple_build_show` | Read | Shows one build. |
| `napple_signing_show` | Read | Shows the App ID, capabilities, profiles and certificates of one bundle. |
| `napple_group_list` | Read | Lists the team's groups. |
| `napple_build_upload` | Write | Uploads a build through the buildUploads API. It uploads, commits and polls until a terminal state. |
| `napple_bundle_create` | Write | Creates a bundle ID. |
| `napple_certificate_create` | Write | Creates a certificate from a CSR only. The private key stays where it signs. |
| `napple_profile_create` | Write | Creates a provisioning profile. |

The agent asks the owner before each write call.

## Configuration

The server reads three environment variables:

- `NOTT_NAPPLE_KEY_ID`: the key ID of the team API key.
- `NOTT_NAPPLE_ISSUER_ID`: the issuer ID of the team.
- `NOTT_NAPPLE_KEY_PATH`: the path to the team API key `.p8` file in `~/.nott/apple/`.

When the environment has none of the three, the server reads `~/.nott/apple/`.
That directory holds one `AuthKey_<key id>.p8` file and an `issuer_id` file.
If the environment has only some of the three, each tool call fails with a
`ConfigError` that names the missing variables.

## Registration

The Claude Code user scope on SERVER registers napple, with no variable.
The launcher directory is the directory where `tools/mcp.sh install napple` puts the MCP launchers:

```bash
claude mcp add --scope user napple -- <launcher directory>/napple
```

Claude Code sessions that the owner opens on SERVER get the ten tools. napple is
not in `plugin/.mcp.json`.

### In NOTT.app nodes

A Claude worker node on the SDK route also gets napple. The host
(`harness/nott_harness/acp/node.py`, `Host.servers` and `Host.napple`) adds it
when all of these are true:

- The node is not Guara, a recipe node or an audit parent, and the host has a
  base plugin root. An orchestrator node is a worker and gets napple.
- `~/.nott/apple/` is a directory.
- The launcher `napple` is in the same directory as the nspawn launcher, and
  it is the installer's MCP entry.
- The node workspace is not `~/.nott`, a parent of it, or a directory in it.

Codex, qwen, AGY and forked Claude nodes do not get napple. A napple failure
(the launcher, the profile capture or the 4 MiB profile limit) removes napple
only, and the node starts. When the launcher exists but fails, the host writes
`napple is not available in this node <node id>: <reason>` to its journal.

napple runs on the host behind its own producer port, outside the node cage.
Its environment is only `PATH`, `HOME` and `LANG`, and its command is
`napple mcp serve --upload-root <node workspace>`. The agent's own tools cannot
read `~/.nott/apple/`. Each napple call is a permission request to the owner.

With `--upload-root`, `napple_build_upload` opens the path once. It continues
only when `/proc/self/fd` shows a regular file inside the root, and it reads
every byte from that descriptor. It refuses a symbolic link out of the root, a
path outside the root, a directory and a FIFO. A release without the flag stops
at its argument parser, so the node starts without napple.

The key file must have mode `600`. The server refuses a key file that other users can read.
The key and the JWT never enter a tool result or a log.

## Not covered

nAPPLE does not cover these tasks:

- Creating an app record. The API has GET only on `/v1/apps`. Use the App Store Connect site.
- Xcode Cloud.
- Sales.
- Reviews.
- App Store submission. This is the owner's act.
- Screenshots.

## Behavior

- The server retries a GET that returns HTTP 429 at most 3 times. It honors `Retry-After`.
- The server never retries POST or PATCH.
- Pagination never follows a link outside the API root.
- Each failure raises one typed error: `ConfigError`, `AuthError`, `AgreementError`,
  `NotFoundError`, `ConflictError`, `RateLimitError`, `ProtocolError` or `UploadError`.

## Install and rollback

```bash
bash tools/mcp.sh install napple
bash tools/mcp.sh rollback napple
```

## Tests

Run the tests from `mcps/napple`:

```bash
TMPDIR=/var/tmp uv run --group dev pytest tests -q
```

The tests use a local HTTP server. They never reach Apple.

## Where the build comes from

The M5 signs the iOS IPA with `apps/apple/Scripts/ios_archive.py`.
xtool on SERVER compiles only the NottKit library today. It cannot produce a distribution IPA yet.

## Documentation

| Need | Page |
| --- | --- |
| Contributor development setup | [Setup](docs/SETUP.md) |
| Checks and security review | [Quality](docs/QUALITY.md) |
| Documentation index and a first contribution | [Docs](docs/README.md) |
| Setup, operation, and restoration | [Runbook](docs/RUNBOOK.md) |
| Services and trust boundaries | [Architecture](docs/ARCHITECTURE.md) |
| Complete MCP and CLI inventory | [Reference](docs/REFERENCE.md) |
| Installation | [Installation](docs/INSTALLATION.md) |
| Processing choices | [Privacy](docs/PRIVACY.md) |
| Existing data | [Upgrade](docs/UPGRADE.md) |
| Failure diagnosis | [Troubleshooting](docs/TROUBLESHOOTING.md) |
| Direction | [Roadmap](docs/ROADMAP.md) |
| Changes | [Changelog](CHANGELOG.md) |

Read [contributing](CONTRIBUTING.md), [conduct](CODE_OF_CONDUCT.md), and [support](SUPPORT.md).
