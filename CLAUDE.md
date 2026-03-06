# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
python bot.py
```

Requires a `.env` file with:
```
DISCORD_TOKEN=your_discord_bot_token
ANTHROPIC_API_KEY=sk-ant-...
ADMIN_IDS=123456789,987654321       # optional, comma-separated Discord user IDs
CONFIDENCE_THRESHOLD=0.6            # optional
COOLDOWN_SECONDS=10                 # optional
WATCHED_CHANNELS=general,chat       # optional, empty = all channels
```

## Architecture

Two files make up the entire bot:

**`bot.py`** — Discord bot, slash commands, message listener, and per-guild storage layer.

**`classifier.py`** — Wraps the Anthropic API. `IntentClassifier` builds a system prompt from the command list and sends each message to Claude Haiku, expecting a JSON response `{"command": "<name or none>", "confidence": 0.0–1.0}`. Call `reload(commands)` to hot-swap the command list without reinstantiating.

### Per-guild command storage

- `commands.json` — read-only starter template. Copied to `data/{guild_id}.json` when the bot first joins a server.
- `data/{guild_id}.json` — each server's live command set, same JSON schema as `commands.json`.
- On startup, `on_ready` seeds files for all current guilds. `on_guild_join` seeds the file for newly joined guilds.
- In-memory caches (`_guild_commands`, `_guild_classifiers`) are populated lazily and kept in sync by `save_guild_commands` + `reload_guild_classifier` after every mutation.

### Message flow

`on_message` → filters (bot, DM, channel, length, prefix, cooldown) → `get_guild_classifier(guild_id).classify(content)` → reply if confident match.

### Permissions

`is_admin(interaction)` returns `True` for user IDs in `ADMIN_IDS` **or** any Discord member with the Manage Server (`manage_guild`) permission. All slash commands that mutate state check this and are guild-only (reject DMs).
