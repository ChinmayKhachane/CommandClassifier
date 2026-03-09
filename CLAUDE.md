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
ADMIN_IDS=123456789,987654321       # optional, comma-separated Discord user IDs
CONFIDENCE_THRESHOLD=0.6            # optional, global default
COOLDOWN_SECONDS=10                 # optional, global default
WATCHED_CHANNELS=general,chat       # optional, global default, empty = all channels
SYNC_GUILD_ID=123456789             # optional, required for --sync-guild flag
```

## Architecture

Two files make up the entire bot:

**`bot.py`** — Discord bot, slash commands, message listener, per-guild storage, and per-guild config layer.

**`classifier.py`** — Wraps the Anthropic API. `IntentClassifier` builds a system prompt from the command list and sends each message to Claude Haiku, expecting a JSON response `{"command": "<name or none>", "confidence": 0.0–1.0}`. Call `reload(commands)` to hot-swap the command list without reinstantiating. `confidence_threshold` is a plain attribute and can be updated directly.

### Per-guild command storage

- `commands.json` — read-only starter template. Copied to `data/{guild_id}.json` when the bot first joins a server.
- `data/{guild_id}.json` — each server's live command set, same JSON schema as `commands.json`.
- On startup, `on_ready` seeds files for all current guilds. `on_guild_join` seeds the file for newly joined guilds.
- In-memory caches (`_guild_commands`, `_guild_classifiers`) are populated lazily and kept in sync by `save_guild_commands` + `reload_guild_classifier` after every mutation.
- All file writes are atomic: written to `.tmp` then `os.replace()`.

### Per-guild config storage

- `data/{guild_id}_config.json` — per-server overrides for `confidence_threshold`, `cooldown_seconds`, and `watched_channels`.
- Loaded lazily into `_guild_config` cache. Falls back to global env var defaults if no file exists.
- `load_guild_config` always merges stored values over `_default_config()` so new keys get defaults automatically.
- `reload_guild_classifier` updates both the command list and `confidence_threshold` on the classifier.

### Message flow

`on_message` → filters (bot, DM, guild config watched_channels, length, prefix, guild config cooldown) → `get_guild_classifier(guild_id).classify(content)` → reply if confident match.

### Permissions

`is_admin(interaction)` returns `True` for user IDs in `ADMIN_IDS` **or** any Discord member with the Manage Server (`manage_guild`) permission. All slash commands that mutate state check this and are guild-only (reject DMs).

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
| `/config reset` | yes | Reset config to global defaults |

### Slash command sync

- `on_ready` calls `tree.sync()` globally by default.
- If `--sync-guild` CLI flag is passed, uses `tree.copy_global_to(guild=...)` + `tree.sync(guild=...)` for instant guild-scoped sync.
- `SYNC_GUILD_ID` must be set in `.env` for `--sync-guild` to work.

### Key constants / globals

- `PAGE_SIZE = 5` — commands shown per page in `/listcmds`
- `DEFAULT_COMMANDS` — loaded once from `commands.json` at startup
- `SYNC_GUILD_ID` — int or None, read from env
- `_sync_to_guild` — bool set by argparse before `client.run()`
