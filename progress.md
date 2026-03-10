# Project Progress

## What this is

A Discord bot that passively listens to chat and auto-responds when a message matches a configured command — no `!trigger` syntax needed. Uses Claude Haiku to classify intent.

---

## What we built / changed

### Started with (original state)
- Single global `commands.json` shared across all servers
- One global `commands_data` list and one global `IntentClassifier` instance
- Admin permission was `ADMIN_IDS` only (env var, hardcoded user IDs)
- No handling of DMs, guild joins, or guild removals

### Per-server commands (major refactor)
- Replaced the single global state with per-guild dicts keyed by `guild_id`:
  - `_guild_commands: dict[int, list[dict]]`
  - `_guild_classifiers: dict[int, IntentClassifier]`
- Each server's commands are stored in `data/{guild_id}.json`
- `commands.json` is now a read-only starter template — no longer the live data store
- All slash commands (`/addcmd`, `/removecmd`, `/editcmd`, `/listcmds`, `/testcmd`) now operate on the calling server's data only
- DMs are explicitly ignored in `on_message`

### Default command seeding
- On first join, `data/{guild_id}.json` is automatically created by copying `commands.json`
- `on_ready` seeds files for all guilds the bot is already in at startup
- `on_guild_join` seeds the file for newly joined guilds while the bot is running
- If a server was previously joined, the orphaned JSON file is reused as-is (their customizations come back)

### Guild remove / cache cleanup
- `on_guild_remove` evicts the guild's entries from `_guild_commands`, `_guild_classifiers`, and `_guild_config`
- The `data/{guild_id}.json` file is intentionally kept on disk so commands are restored if the bot is re-added

### Permission system
- `is_admin()` now accepts the full `interaction` object
- Grants access to: global `ADMIN_IDS` from `.env` **OR** any Discord member with the Manage Server permission
- Server owners/admins can manage their own bot without needing to be in `ADMIN_IDS`

### Code quality fixes
- Atomic file writes in `save_guild_commands` and `save_guild_config` — writes `.tmp` then `os.replace()` to prevent corruption
- Background `_cleanup_cooldown_cache()` task runs every hour to evict stale entries from `_last_response_time`
- Replaced deprecated `asyncio.get_event_loop()` with `asyncio.get_running_loop()` in `on_message` and `/testcmd`

### `/resetcmds` command
- Resets a server's entire command set back to the default template from `commands.json`
- Admin-only, guild-only
- Updates in-memory cache, disk file, and reloads classifier immediately

### `/listcmds` pagination
- Commands are now shown 5 per page with Prev/Next buttons (discord.ui.View)
- Buttons are disabled at boundaries; view times out after 120 seconds
- If there is only one page, no buttons are shown

### Per-server configuration
- Each server can override global defaults for three settings:
  - `confidence_threshold` — minimum classifier confidence to respond
  - `cooldown_seconds` — per-channel response cooldown
  - `watched_channels` — list of channel names to monitor (empty = all)
- Stored in `data/{guild_id}_config.json`; loaded lazily and cached in `_guild_config`
- `load_guild_config` merges stored values over `_default_config()` so new keys always get a default (forward-compatible)
- `_on_cooldown` now takes `cooldown_seconds` as a parameter instead of using the global
- `on_message` pulls both `watched_channels` and `cooldown_seconds` from guild config
- `get_guild_classifier` and `reload_guild_classifier` use per-guild `confidence_threshold`

### `/config` command group
- `/config view` — shows current threshold, cooldown, watched channels (any user)
- `/config threshold <0.0–1.0>` — sets confidence threshold; reloads classifier (admin)
- `/config cooldown <0–3600>` — sets per-channel cooldown in seconds (admin)
- `/config channels [names]` — comma-separated channel names; blank = all (admin)
- `/config reset` — restores all settings to global env var defaults (admin)

### Slash command sync with `--sync-guild` flag
- `python bot.py --sync-guild` — syncs commands to the test guild in `SYNC_GUILD_ID` (instant)
- `python bot.py` — syncs globally (up to 1 hour propagation)
- `SYNC_GUILD_ID` is read from `.env`; error logged if flag used without it set
- Uses `tree.copy_global_to(guild=...)` before guild sync so all commands appear in the test server

---

### SQLite migration

- Replaced all JSON file storage (`data/{guild_id}.json`, `data/{guild_id}_config.json`) with a single SQLite database (`bot.db`)
- `init_db()` creates the DB and tables on startup if they don't exist; called before `client.run()`
- Two tables: `commands(guild_id, name, description, response)` and `guild_config(guild_id, confidence_threshold, cooldown_seconds, watched_channels)`
- `watched_channels` stored as a JSON array string
- `save_guild_commands` does a DELETE + bulk INSERT within a transaction for simplicity
- `save_guild_config` uses INSERT OR REPLACE
- In-memory caches unchanged — SQLite is only hit on cache miss or mutation
- Uses `sqlite3` from the standard library — no new dependency

---

## Current file structure

```
bot.py          — bot, slash commands, per-guild storage + config layer
classifier.py   — IntentClassifier wrapping Anthropic API
commands.json   — default command template seeded into DB for new guilds
config.json     — default config template used by /config reset
bot.db          — SQLite database (auto-created on first run, gitignored)
CLAUDE.md       — AI assistant guidance
README.md       — user-facing documentation
progress.md     — this file
requirements.txt
```

---

## Known limitations / potential next steps

- No pagination for `/listcmds` beyond 5 per page (page size hardcoded as `PAGE_SIZE = 5`)
- No `/exportcmds` or `/importcmds` for bulk command management
- No per-server logging channel (all logs go to stdout only)
- `SYNC_GUILD_ID` is a single test guild — no multi-guild test sync support
- `bot.db` should be added to `.gitignore` to avoid committing live guild data
