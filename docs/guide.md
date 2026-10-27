# Usage Guide

## Installation

### 1. Start ai-memory server

```bash
# Install ai-memory (see https://github.com/akitaonrails/ai-memory)
ai-memory serve

# Default: http://127.0.0.1:49374
```

### 2. Install the plugin

```bash
# Copy into your Hermes profile
cp -r plugins/memory/ai-memory/* "$HERMES_HOME/plugins/ai-memory/"

# Or symlink for development
python -m plugins.memory.ai_memory.cli link
```

**Quick one-liner (Linux/macOS):**

```bash
bash <(curl -sL https://raw.githubusercontent.com/MrLuciano/ai-memory-hermes-plugin/main/scripts/install.sh)
```

**Using uv pip (pre-installs httpx):**

```bash
uv pip install --system -e /path/to/ai-memory-hermes-plugin
cp -r plugins/memory/ai-memory/* "$HERMES_HOME/plugins/ai-memory/"
```

### 3. Enable in Hermes

```bash
hermes plugins enable ai-memory
```

### 4. Run setup wizard

```bash
hermes memory setup
```

Prompts for server URL, API key (optional), and workspace/project.

### 5. Verify

```bash
hermes ai-memory status
# → ai-memory server: reachable
#   Version:      2.4.1
#   Pages:        42
#   Sessions:     7
#   Observations: 318
```

## Configuration

### Server URL

If ai-memory runs on a different host or port, save it in the active profile:

```bash
hermes ai-memory config-set server_url http://10.0.0.42:49374
```

Or use the `hermes memory setup` wizard.

### Authentication

Bearer auth is profile-scoped. Put the member's key in `$HERMES_HOME/.env`
(secrets are never written to `ai-memory.json`):

```bash
AI_MEMORY_API_KEY=aim_member-key
```

Verify which source is active:

```bash
hermes ai-memory config
# → auth_token: (set via env: AI_MEMORY_AUTH_TOKEN)
# → api_key:    (not set)
```

**Note:** Attempting `hermes ai-memory config-set auth_token value` will show the env var to use instead — secrets are never persisted to `ai-memory.json`.

### Project Isolation

The provider writes to and reads from the resolved project by default. `initialize()` resolves the scope in this order:

1. Explicit kwargs — `ai_memory_workspace`, `project`
2. Explicit config — `workspace` / `project` in `$HERMES_HOME/ai-memory.json`
3. The nearest `.ai-memory.toml` at or above the session's working directory
4. What Hermes reports — `agent_workspace`, then `agent_identity` as `hermes-{profile}` (defaults: `hermes` / `hermes-default`)

For legacy-generated `hermes` / `hermes-default` pairs, see the deterministic [compatibility rule and explicit opt-out](../README.md#legacy-scope-defaults-and-explicit-overrides). New installers leave scope unset; leave setup scope prompts blank to use markers.

For a single profile with folder-based memory, keep the default `search_scope=project` and omit fixed workspace/project overrides. The nearest applicable marker above the absolute, existing session directory wins, including markers within monorepo applications.

With no project marker and no configured project, writes and handoff consumption are blocked. Explicit default-project configuration is still supported. Project-mode recall with unknown scope returns no results rather than falling back to global search.

Folder changes inside an existing session do **not** re-resolve this provider's scope. Start a new provider/session initialized with the target folder; `/new` or `/reset` that only switches the ID does not change folder scope. No process-cwd fallback is used.

## Lifecycle Behavior

### Session Start (`initialize`)

When a new conversation starts, the provider resolves the scope (above) and fetches the previous session's handoff once, so `system_prompt_block()` can surface it. Non-primary contexts (`agent_context` other than `primary`) skip that fetch — accepting a handoff is single-use.

### Before Each Turn (`prefetch`)

The agent calls `prefetch(query, session_id=...)` with the user's message. The provider:

1. Consumes the cached hits `queue_prefetch` stored for that `(session_id, query)`, if any
2. Otherwise searches the wiki with the member API at `GET /api/v1/search?q=<query>&limit=3`
3. Returns one block per hit, labelled `title (path): snippet`

Hermes runs `queue_prefetch(query, session_id=...)` on its serialized, profile-bound memory worker after a turn, which makes the next turn's `prefetch` a cache read.

### After Each Turn (`sync_turn`)

With `lifecycle_capture=true`, after each user+assistant exchange:

1. Hermes schedules the call on its profile-bound memory worker
2. The provider sends `POST /hook?event=user-prompt-submit` with `{"session_id", "prompt"}` — the field ai-memory reads — plus the session's `cwd`
3. Errors are logged without breaking the conversation

### Session End (`on_session_end`)

With `lifecycle_capture=true`, when the conversation ends:

1. Hermes serializes the boundary after pending turn writes
2. The provider sends `POST /hook?event=session-end` with the session id only — the server reads no body field on this event
3. Errors are logged without breaking session shutdown

If a close is ever lost (server down at that moment), `ai-memory finalize-session --agent hermes` posts the same event afterwards.

### Personal memory (`on_memory_write`)

Hermes keeps built-in `MEMORY.md` and `USER.md` inside each member's profile.
The provider does not mirror those files to the shared ai-memory project. Team
knowledge is written explicitly with `ai_memory_write`; completed conversations
stay local unless `lifecycle_capture` is explicitly enabled.

## Using Tools

### `ai_memory_search`

```json
{
  "name": "ai_memory_search",
  "arguments": {
    "query": "project architecture decisions",
    "max_results": 10
  }
}
```

### `ai_memory_write`

```json
{
  "name": "ai_memory_write",
  "arguments": {
    "path": "decisions/auth-flow.md",
    "body": "# Auth Flow\n\nWe use JWT with refresh tokens...",
    "tags": ["auth", "security"]
  }
}
```

### `ai_memory_status`

```json
{
  "name": "ai_memory_status",
  "arguments": {}
}
```

## Development

### Setup

```bash
uv sync
```

### Running Tests

```bash
# Full suite with coverage
uv run pytest --cov

# Single file
uv run pytest tests/test_provider.py -v

# Watch mode (with pytest-watch)
uv run ptw -- --cov
```

### Linting and Typing

```bash
uv run ruff check .
uv run mypy .
```

### Adding a New Tool

1. Add schema to `get_tool_schemas()` in `provider.py`
2. Add handler case in `handle_tool_call()`
3. Add private `_tool_method()` in provider
4. Add client method in `client.py` if needed
5. Write tests in `tests/test_provider.py`

## Integration Patterns

### Multiple Hermes Profiles

Each profile gets its own ai-memory project:

```bash
# Profile "work"
hermes --profile work
# → project: hermes-work

# Profile "personal"
hermes --profile personal
# → project: hermes-personal
```

### Disable Auto-Capture

Lifecycle capture is disabled by default. Keep this in `$HERMES_HOME/ai-memory.json` for curated team memory:

```json
{
  "lifecycle_capture": false
}
```

This disables lifecycle persistence, not remote recall. The provider still sends the current query to the configured ai-memory search endpoint. Use `hermes memory off` before a conversation whose query text must remain on the workstation.

Enable it only for a project whose members have agreed to share conversation prompts:

```bash
hermes ai-memory config-set lifecycle_capture true
```

### Custom Handoff Agent

```python
client.fetch_handoff(agent="codex", workspace="hermes", project="my-project")
```

## Deployment

### Install Scripts

Two ready-to-run install scripts are included in the repository:

- `scripts/install.sh` — Linux/macOS (Bash)
- `scripts/install.ps1` — Windows (PowerShell)

Both scripts:
1. Resolve `$HERMES_HOME` (default: `~/.hermes` or `%USERPROFILE%\.hermes`)
2. Run **pre-flight checks** (Hermes CLI, ai-memory server, plugin state, write permissions)
3. Prompt for **confirmation** before making changes
4. Symlink/junction or copy the plugin directory
5. Write an initial `ai-memory.json` config if none exists

#### Flags

| Flag | Bash | PowerShell | Env var | Description |
|---|---|---|---|---|
| Dry run | `--dry-run` | `-DryRun` | — | Show what would happen without making changes |
| Skip prompts | `--yes` | `-Yes` | `FORCE=true` | Skip confirmation (for CI/automation) |

When piped (non-interactive), scripts detect the missing TTY, print a warning, and proceed. Set `FORCE=true` or pass `--yes`/`-Yes` to silence the warning.

### Linux/macOS One-liner

Requires `curl` and `tar`. When run via `curl`, the script downloads the plugin from GitHub and copies it into `$HERMES_HOME/plugins/ai-memory`.

```bash
bash <(curl -sL https://raw.githubusercontent.com/MrLuciano/ai-memory-hermes-plugin/main/scripts/install.sh)
```

Override the server URL:

```bash
AI_MEMORY_SERVER_URL=http://10.0.0.42:49374 bash <(curl -sL ...)
```

### Windows One-liner

Requires PowerShell 5.1+ with .NET (default on Windows 10/11 and Windows Server 2016+). When run via `iex`, the script downloads the plugin from GitHub and copies it into `$HERMES_HOME\plugins\ai-memory`.

```powershell
powershell -c "iex ((Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/MrLuciano/ai-memory-hermes-plugin/main/scripts/install.ps1').Content)"
```

### Uninstall

Two uninstall scripts are included:

- `scripts/uninstall.sh` — Linux/macOS
- `scripts/uninstall.ps1` — Windows

Both remove `$HERMES_HOME/plugins/ai-memory` (symlink, junction, or copied directory) and run `hermes plugins disable ai-memory` if the Hermes CLI is available. The `ai-memory.json` config file is kept unless you request removal.

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
# Dry-run:
.\scripts\uninstall.ps1 -DryRun
# Skip prompt:
.\scripts\uninstall.ps1 -Yes
# Also remove config:
.\scripts\uninstall.ps1 -RemoveConfig
```

### Update

Two update scripts are included:

- `scripts/update.sh` — Linux/macOS
- `scripts/update.ps1` — Windows

Both default to downloading the latest plugin from the GitHub `main` branch, back up the existing install to `$HERMES_HOME/.ai-memory-backups/ai-memory.bak.<timestamp>`, replace the plugin files, and preserve `ai-memory.json`. Backups are kept and listed after each run.

Linux/macOS:

```bash
bash scripts/update.sh
# Dry-run (preview only):
bash scripts/update.sh --dry-run
# Skip prompt:
bash scripts/update.sh --yes
```

Override the source tarball:

```bash
REPO_TARBALL_URL=https://example.com/ai-memory.tar.gz bash scripts/update.sh
```

Update from a local clone (preserves a symlink install):

```bash
UPDATE_FROM_LOCAL=true bash scripts/update.sh
```

Windows (PowerShell):

```powershell
.\scripts\update.ps1
# Dry-run:
.\scripts\update.ps1 -DryRun
# Skip prompt:
.\scripts\update.ps1 -Yes
```

```powershell
$env:UPDATE_FROM_LOCAL="true"; .\scripts\update.ps1
```

The `hermes ai-memory update` command performs the same GitHub-based update from inside Hermes.

### uv pip install (from local clone)

If you have the repository cloned and want to install `httpx` into your Hermes environment:

```bash
# Install the project dependency
uv pip install --system -e /path/to/ai-memory-hermes-plugin

# Copy the plugin files
mkdir -p "$HERMES_HOME/plugins/ai-memory"
cp -r /path/to/ai-memory-hermes-plugin/plugins/memory/ai-memory/* "$HERMES_HOME/plugins/ai-memory/"
```

Without the `uv pip install` step, Hermes will still attempt to install `httpx` automatically from `plugin.yaml`'s `pip_dependencies` section — but pre-installing it is faster for repeated deployments.

### Manual Install

```bash
# From the project root
mkdir -p "$HERMES_HOME/plugins/ai-memory"
cp -r plugins/memory/ai-memory/* "$HERMES_HOME/plugins/ai-memory/"
```
