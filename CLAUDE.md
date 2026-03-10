# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
python bot.py                # global sync (up to 1 hour for slash commands to appear)
python bot.py --sync-guild   # instant sync to SYNC_GUILD_ID (for testing)
```

Requires a `.env` file with:
```
DISCORD_TOKEN=your_discord_bot_token
ANTHROPIC_API_KEY=sk-ant-...
CONFIDENCE_THRESHOLD=0.6            # optional, global default
COOLDOWN_SECONDS=10                 # optional, global default
SYNC_GUILD_ID=123456789             # optional, required for --sync-guild flag
DATA_DIR=/data                      # optional, defaults to project root; set for Railway
```

There is no `ADMIN_IDS` or `WATCHED_CHANNELS` env var — admin access is determined solely by Discord's Manage Server permission, and watched channels are configured per-server via `/config channels`.

## Deployment

Deployed on Railway as an always-on worker process (never serverless — bot needs a persistent WebSocket connection). A Railway Volume is mounted at `/data` and `DATA_DIR=/data` is set so `bot.db` persists across redeploys. The `Procfile` defines the process: `worker: python bot.py`.

## Architecture

Two files make up the entire bot:

**`bot.py`** — Discord bot, slash commands, message listener, per-guild storage, and per-guild config layer.

**`classifier.py`** — Wraps the Anthropic API. `IntentClassifier` builds a system prompt from the command list and sends each message to Claude Haiku, expecting a JSON response `{"command": "<name or none>", "confidence": 0.0–1.0}`. Call `reload(commands)` to hot-swap the command list without reinstantiating. `confidence_threshold` is a plain attribute and can be updated directly.

### Database

- All data is stored in `bot.db` (SQLite). Location controlled by `DATA_DIR` env var, defaulting to project root.
- `init_db()` must be called before `client.run()` — creates tables if they don't exist.
- Two tables: `commands(guild_id, name, description, response)` and `guild_config(guild_id, confidence_threshold, cooldown_seconds, watched_channels)`.
- `watched_channels` is stored as a JSON array string.
- Uses `sqlite3` from the standard library — no extra dependency.

### Per-guild storage pattern

- `commands.json` — read-only starter template seeded into the DB when a new guild is first seen.
- `config.json` — read-only config template; loaded as `DEFAULT_CONFIG` at startup. Used by `/config reset` to restore defaults.
- `load_guild_commands` seeds from `DEFAULT_COMMANDS` if no rows exist for that guild.
- `load_guild_config` merges DB values over `_default_config()` (env vars) so new keys always get defaults.
- In-memory caches (`_guild_commands`, `_guild_classifiers`, `_guild_config`) are populated lazily and kept in sync by `save_guild_commands` / `save_guild_config` + `reload_guild_classifier` after every mutation.
- `reload_guild_classifier` updates both the command list and `confidence_threshold` on the classifier.

### Message flow

`on_message` → filters (bot, DM, guild config watched_channels, length, prefix, guild config cooldown) → `get_guild_classifier(guild_id).classify(content)` → reply if confident match.

### Permissions

`is_admin(interaction)` returns `True` for any Discord member with the Manage Server (`manage_guild`) permission. There is no global `ADMIN_IDS` override. All slash commands that mutate state check this and are guild-only (reject DMs).

### Slash commands

| Command | Admin only | Description |
|---|---|---|
| `/addcmd` | yes | Add a command to this server |
| `/removecmd` | yes | Remove a command from this server |
| `/editcmd` | yes | Edit an existing command |
| `/listcmds` | no | List commands, paginated (5/page, Prev/Next buttons) |
| `/testcmd` | yes | Test classification without sending a reply |
| `/resetcmds` | yes | Reset commands to the default template |
| `/config view` | no | Show current per-server config |
| `/config threshold` | yes | Set confidence threshold (0.0–1.0) |
| `/config cooldown` | yes | Set cooldown in seconds (0–3600) |
| `/config channels` | yes | Set watched channels (blank = all) |
| `/config reset` | yes | Reset config to defaults |

### Slash command sync

- `on_ready` calls `tree.sync()` globally by default.
- If `--sync-guild` CLI flag is passed, uses `tree.copy_global_to(guild=...)` + `tree.sync(guild=...)` for instant guild-scoped sync, then clears global commands to prevent duplicates.
- `SYNC_GUILD_ID` must be set in `.env` for `--sync-guild` to work.

### Key constants / globals

- `PAGE_SIZE = 5` — commands shown per page in `/listcmds`
- `DEFAULT_COMMANDS` — loaded once from `commands.json` at startup
- `DB_PATH` — `Path` to `bot.db`; location set by `DATA_DIR` env var
- `_conn` — single `sqlite3.Connection`, opened by `init_db()`
- `DEFAULT_CONFIG` — loaded once from `config.json` at startup; used by `/config reset`
- `SYNC_GUILD_ID` — int or None, read from env
- `_sync_to_guild` — bool set by argparse before `client.run()`
