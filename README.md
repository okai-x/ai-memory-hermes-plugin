# ai-memory Hermes Memory Provider Plugin

Connects [Hermes Agent](https://github.com/NousResearch/hermes-agent) to [ai-memory](https://github.com/akitaonrails/ai-memory) as a first-class `MemoryProvider` plugin — automatic prefetch, explicit shared writes, optional lifecycle capture, and wiki search tools.

## Features

- **Background prefetch** — Hermes' profile-bound memory worker searches after each turn and injects the cached result before the next one
- **Lifecycle storage is opt-in** — turn capture and session finalization are off until `lifecycle_capture` is explicitly enabled
- **Personal memory stays personal** — built-in `MEMORY.md` / `USER.md` are not copied into the shared wiki
- **Hermes CLI integration** — `hermes memory setup` plus the plugin's own `hermes ai-memory status | config | config-set | link | update`
- **Scope that matches the other agents** — explicit config, then the checkout's `.ai-memory.toml`, then the Hermes profile
- **Safe in non-primary runs** — cron/subagent providers neither write nor consume the single-use handoff
- **3 tool schemas** — `ai_memory_search`, `ai_memory_write`, `ai_memory_status`
- **Multi-profile safe** — config and credentials bind during `initialize()` from the owning profile

## Quick Start

```bash
# Quick install via script (Linux/macOS)
bash <(curl -sL https://raw.githubusercontent.com/MrLuciano/ai-memory-hermes-plugin/main/scripts/install.sh)

# Enable and configure
hermes plugins enable ai-memory
hermes memory setup

# Verify
hermes memory status
```

Or download and run `scripts/install.sh` (Linux/macOS) or `scripts/install.ps1` (Windows) from the repository.

## Architecture

```
Hermes Agent → MemoryProvider ABC → AiMemoryProvider → AiMemoryClient → HTTP → ai-memory server
```

| Layer | File | Role |
|---|---|---|
| `__init__.py` | Entry point | `register()` — creates a fresh unbound provider for Hermes to initialize per profile |
| `provider.py` | AiMemoryProvider | Implements `MemoryProvider` ABC; lifecycle hooks, tool dispatch |
| `client.py` | AiMemoryClient | User API + stateless MCP client; search, write page, status, hooks, handoff |
| `config.py` | AiMemoryConfig | Profile JSON behavior + profile-scoped credential resolution |
| `cli.py` | CLI | `status`/`config`/`link` subcommands for `hermes memory` |
| `plugin.yaml` | Manifest | Metadata + `python_dependencies` (Hermes validates but never installs) |

## Lifecycle

| Hermes hook | ai-memory call | Threading |
|---|---|---|
| `is_available()` | Checks `server_url` is configured | Sync |
| `initialize()` | Resolves workspace/project, fetches the handoff once | Sync |
| `prefetch(query, session_id)` | Consumes `queue_prefetch`'s cache, else `GET /api/v1/search` | Sync |
| `queue_prefetch(query, session_id)` | `GET /api/v1/search` cached for the next turn | Hermes memory worker |
| `recall_status()` | Hit count of the last `prefetch`, for the recall indicator | Sync |
| `sync_turn(user, assistant)` | When `lifecycle_capture=true`, `POST /hook?event=user-prompt-submit` | Hermes memory worker |
| `on_session_end(messages)` | When `lifecycle_capture=true`, `POST /hook?event=session-end` (session id only) | Hermes memory worker at session boundaries |
| `on_memory_write(action, target, content)` | No remote write; personal files stay profile-local | Sync |
| `handle_tool_call(name, args)` | Routes to search/write/status | Sync |

## Configuration

### Profile behavior

Store `server_url`, `search_scope`, `lifecycle_capture`, and any fixed workspace/project override in
`$HERMES_HOME/ai-memory.json`. Behavior environment variables are deliberately
ignored so one multiplexed gateway cannot leak its launch profile's routing into
another member's assistant.

### Config File (`$HERMES_HOME/ai-memory.json`)

Written by `hermes memory setup` wizard. Only non-secret values are stored:

```json
{
  "server_url": "http://127.0.0.1:49374",
  "search_scope": "project",
  "lifecycle_capture": false
}
```

`workspace`/`project` overrides set here (except the legacy-generated pair described below) are explicit: they win over the nearest applicable `.ai-memory.toml` at or above the absolute, existing session directory supplied during initialization.

`project` is the default and restricts search tools and both recall paths to the resolved project. `global` remains an explicit opt-in. Omit fixed workspace/project overrides so `.ai-memory.toml` markers route each new session. A nested marker in `imperio_2026/apps/socorro_financeiro` takes precedence over the root marker.

When scope is unknown, project searches return no results and shared writes, lifecycle hooks and handoff consumption are blocked. Lifecycle hooks are also blocked unless `lifecycle_capture` is explicitly `true`. An explicitly configured default project remains supported. Resolved scope from a previous initialization is never a fallback for the next one.

`lifecycle_capture=false` prevents lifecycle records; it does not make recall local-only. Automatic recall still sends the current query to the configured ai-memory server. Disable the external provider for conversations whose queries must remain on the workstation.

**Changing folders during an existing session does not update this provider's scope.** Start a new provider/session initialized with the target directory; switching the session ID alone is insufficient. The plugin never infers the session directory from `os.getcwd()`. See [Scope Resolution](docs/reference.md#scope-resolution).

### Legacy scope defaults and explicit overrides

New installers/updaters omit fixed workspace/project values. In `hermes memory setup`, leave those prompts blank for directory routing; `search_scope` accepts only `global` or `project`. `hermes ai-memory config` also displays the read policy (not the session-specific TOML resolution).

For old files, a narrow compatibility rule applies **only when the file says `search_scope=project`**: the complete pair `workspace=hermes`, `project=hermes-default`, with no keys other than `server_url`, `workspace`, `project`, `search_scope`, `scope_origin`, is treated as automatic unless `scope_origin=explicit`. Resolution does not rewrite the file. Single-key overrides, custom values, additional configuration keys and initialization kwargs remain explicit.

The old format cannot prove whether an identical pair was installed automatically or typed manually. For an intentional fixed pair, set `hermes ai-memory config-set scope_origin explicit` **before enabling project mode**, or re-save the workspace/project with `config-set`. Saving a nonblank scope via setup/CLI records `scope_origin=explicit`; `automatic` is also accepted, but never demotes customized values. Blank scope inputs remove those file overrides. Invalid origin/policy values are rejected before saving.

For automatic folder routing, set `hermes ai-memory config-set search_scope project` and leave scope overrides unset. Each new provider must receive the intended absolute directory; scope does not change dynamically within an existing provider.

### Secrets & Security

`api_key` and `auth_token` are treated as **profile-scoped secrets**:
- They are **never written** to `ai-memory.json` by `save_config()` or `hermes memory setup`
- If they already exist in the JSON file, they are ignored on load and stripped on the next config save
- Put the member's key in that profile's `$HERMES_HOME/.env`:

```bash
AI_MEMORY_API_KEY=aim_member-key
```

The CLI shows where each secret is sourced from:

```bash
hermes ai-memory config
# → auth_token: (set via env: AI_MEMORY_AUTH_TOKEN)
# → api_key:    (not set)
```

Attempting to write a secret via `hermes ai-memory config-set` shows the env var to use instead:

```bash
hermes ai-memory config-set auth_token my-secret
# → NOT WRITTEN TO DISK: auth_token is a secret — use an environment variable:
#   export AI_MEMORY_AUTH_TOKEN='my-secret'
```

## Tools

| Tool | Description | Parameters |
|---|---|---|
| `ai_memory_search` | Search the wiki | `query` (req), `max_results` (opt, default 5) |
| `ai_memory_write` | Write a new wiki page | `path` (req), `body` (req), `tags` (opt) |
| `ai_memory_status` | Server health check | none |

## Development

```bash
uv sync
uv run ruff check .
uv run mypy .
uv run pytest --cov
```

### Project Structure

```
plugins/memory/ai-memory/
├── __init__.py       # Entry point — register()
├── plugin.yaml       # Hermes manifest
├── provider.py       # AiMemoryProvider
├── client.py         # AiMemoryClient (httpx)
├── config.py         # AiMemoryConfig + persistence
├── cli.py            # CLI subcommands
└── README.md         # In-plugin readme
tests/
├── test_config.py    # 12 tests
├── test_client.py    # 16 tests
├── test_provider.py  # 29 tests
├── test_entry.py     # 10 tests
└── test_cli.py       # 7 tests
```

## Requirements

- Python 3.10+
- Hermes Agent with plugin support
- ai-memory server (HTTP API at port 49374)
- `httpx >= 0.28`

## Deployment

### Install Scripts

| Platform | Script | Method |
|---|---|---|
| Linux/macOS | [`scripts/install.sh`](scripts/install.sh) | Bash — symlinks or copies plugin into `$HERMES_HOME`, writes initial config |
| Windows | [`scripts/install.ps1`](scripts/install.ps1) | PowerShell — creates NTFS junction or copies, writes initial config |

All scripts support **pre-flight checks**, **dry-run**, and **confirmation prompts**:

| Flag | Bash | PowerShell | Env var | Description |
|---|---|---|---|---|
| Dry run | `--dry-run` | `-DryRun` | — | Show what would happen without making changes |
| Skip prompts | `--yes` | `-Yes` | `FORCE=true` | Skip all confirmation prompts (for CI/automation) |

When piped (non-interactive), scripts detect the missing TTY and proceed with a warning. Set `FORCE=true` or pass `--yes`/`-Yes` to silence the warning.

Pre-flight checks verify: Hermes CLI availability, Hermes process status, ai-memory server reachability, plugin state, write permissions, and existing config.

### One-liner (Linux/macOS)

Requires `curl` and `tar`. The script downloads the plugin from GitHub when run via `curl`, then copies it into `$HERMES_HOME/plugins/ai-memory`.

```bash
bash <(curl -sL https://raw.githubusercontent.com/MrLuciano/ai-memory-hermes-plugin/main/scripts/install.sh)
```

Preview what the install would do (dry-run):

```bash
bash <(curl -sL .../install.sh) --dry-run
```

Skip confirmation prompts for automation:

```bash
FORCE=true bash <(curl -sL .../install.sh)
```

Set `AI_MEMORY_SERVER_URL` before running to override the default server address:

```bash
AI_MEMORY_SERVER_URL=http://10.0.0.42:49374 bash <(curl -sL ...)
```

### Windows (PowerShell)

Requires PowerShell 5.1+ with .NET (default on Windows 10/11 and Windows Server 2016+). The script downloads the plugin from GitHub when run via `iex`, then copies it into `$HERMES_HOME\plugins\ai-memory`.

```powershell
powershell -c "iex ((Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/MrLuciano/ai-memory-hermes-plugin/main/scripts/install.ps1').Content)"
```

With custom server URL:

```powershell
powershell -c "$env:ServerUrl='http://10.0.0.42:49374'; iex ((Invoke-WebRequest -Uri '...').Content)"
```

### Uninstall

Removes the plugin from `$HERMES_HOME/plugins/ai-memory` and runs `hermes plugins disable ai-memory` if the Hermes CLI is available. The `ai-memory.json` config file is kept unless you pass the removal flag.

Linux/macOS:

```bash
bash scripts/uninstall.sh
# Dry-run (preview only):
bash scripts/uninstall.sh --dry-run
# Skip prompt:
bash scripts/uninstall.sh --yes
# Also remove config:
REMOVE_CONFIG=true bash scripts/uninstall.sh
```

Windows (PowerShell):

```powershell
.\scripts\uninstall.ps1
# Dry-run (preview only):
.\scripts\uninstall.ps1 -DryRun
# Skip prompt:
.\scripts\uninstall.ps1 -Yes
# Also remove config:
.\scripts\uninstall.ps1 -RemoveConfig
```

### Update

Fetches the latest plugin files from GitHub, backs up the current install to `$HERMES_HOME/.ai-memory-backups/ai-memory.bak.<timestamp>`, replaces the plugin, and preserves `ai-memory.json`.

Linux/macOS:

```bash
bash scripts/update.sh
# Dry-run (preview only):
bash scripts/update.sh --dry-run
# Skip prompt:
bash scripts/update.sh --yes
# Or via one-liner:
bash <(curl -sL https://raw.githubusercontent.com/MrLuciano/ai-memory-hermes-plugin/main/scripts/update.sh)
```

Windows (PowerShell):

```powershell
.\scripts\update.ps1
# Dry-run (preview only):
.\scripts\update.ps1 -DryRun
# Skip prompt:
.\scripts\update.ps1 -Yes
```

Update from a local clone (preserves symlink/junction):

```bash
UPDATE_FROM_LOCAL=true bash scripts/update.sh
```

```powershell
$env:UPDATE_FROM_LOCAL="true"; .\scripts\update.ps1
```

The CLI also provides `hermes ai-memory update`, which downloads and replaces the plugin from GitHub.

### uv pip install (from repo)

If you have `uv` and are deploying from a local clone:

```bash
uv pip install --system -e "$PWD"  # installs httpx dependency
mkdir -p "$HERMES_HOME/plugins/ai-memory"
cp -r plugins/memory/ai-memory/* "$HERMES_HOME/plugins/ai-memory/"
```

More detailed deployment instructions in [docs/guide.md](docs/guide.md#deployment).

## References

- [Hermes Memory Provider Plugin docs](https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin)
- [ai-memory repository](https://github.com/akitaonrails/ai-memory)
- [docs/guide.md](docs/guide.md) — usage guide
- [docs/reference.md](docs/reference.md) — full API reference
- [docs/common-problems.md](docs/common-problems.md) — troubleshooting
