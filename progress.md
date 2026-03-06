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
- `on_guild_remove` evicts the guild's entries from both `_guild_commands` and `_guild_classifiers`
- The `data/{guild_id}.json` file is intentionally kept on disk so commands are restored if the bot is re-added

### Permission system
- `is_admin()` now accepts the full `interaction` object
- Grants access to: global `ADMIN_IDS` from `.env` **OR** any Discord member with the Manage Server permission
- Server owners/admins can manage their own bot without needing to be in `ADMIN_IDS`

### Documentation
- `README.md` updated to reflect per-server architecture, new file structure, and updated permission docs
- `CLAUDE.md` created for future AI assistant context

---

## Current file structure

```
bot.py           — bot, slash commands, per-guild storage layer
classifier.py    — IntentClassifier wrapping Anthropic API
commands.json    — starter template copied to new servers (not live data)
data/
  {guild_id}.json  — per-server live command sets (auto-created)
CLAUDE.md        — AI assistant guidance
README.md        — user-facing documentation
requirements.txt
```

---

## Known limitations / potential next steps

- `WATCHED_CHANNELS` is global — there's no per-server channel filtering
- No way to reset a server's commands back to the default template via slash command
- No pagination for `/listcmds` beyond the 1900-char truncation
- `CONFIDENCE_THRESHOLD` and `COOLDOWN_SECONDS` are global — not configurable per server
