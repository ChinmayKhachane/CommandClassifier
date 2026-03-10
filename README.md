# Discord Intent Bot

A Discord bot that reads chat messages and automatically responds with the most relevant command — no `!command` syntax needed. Uses Claude Haiku to classify message intent in real-time.

## How it works

```
User says: "hey what days do you go live?"
Bot matches: schedule (confidence: 0.88)
Bot replies: "The stream schedule is Monday, Wednesday, and Friday at 7 PM EST!"
```

The bot passively listens to chat. When someone says something that matches a configured command, it replies. If nothing matches confidently enough, it stays quiet.

Each server the bot is in gets its own independent set of commands.

## Setup

### 1. Create a Discord Bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **New Application** → give it a name → **Create**
3. Go to **Bot** tab → **Reset Token** → copy the token
4. Under **Privileged Gateway Intents**, enable **Message Content Intent**
5. Go to **OAuth2 → URL Generator**, select `bot` scope with `Send Messages` + `Read Message History` permissions
6. Open the generated URL to invite the bot to your server

### 2. Install & Configure

```bash
pip install -r requirements.txt
```

Create a `.env` file with your tokens:
```
DISCORD_TOKEN=your_discord_bot_token
ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Customize the Default Template

Edit `commands.json` — this is the **template** that every new server starts with when the bot joins. It does not affect servers that already have their own commands.

Each command has:

- **name**: identifier for logging
- **description**: tells the AI *when* this command should match (this is the most important field — be descriptive)
- **response**: what the bot sends when matched

### 4. Run

```bash
python bot.py
```

## Deployment (Railway)

The bot is designed to run as an always-on worker process. Do not enable serverless — the bot requires a persistent WebSocket connection to Discord.

### Required environment variables

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Your Discord bot token |
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `DATA_DIR` | Path to persistent storage directory (e.g. `/data`) |
| `CONFIDENCE_THRESHOLD` | Optional, default `0.6` |
| `COOLDOWN_SECONDS` | Optional, default `10` |
| `SYNC_GUILD_ID` | Optional, only needed for `--sync-guild` local testing |

### Persistent volume

`bot.db` must survive redeploys. On Railway: add a Volume and mount it at `/data`, then set `DATA_DIR=/data`. Without this the database resets on every deploy.

## Per-Server Commands

Each server gets its own command set stored in the database. When the bot joins a server for the first time, it automatically seeds that server's commands from `commands.json`.

From that point on, each server's commands are fully independent — changes in one server don't affect any other.

All slash commands operate on the server they are used in.

## Managing Commands

Once the bot is running, admins can manage commands live via Discord slash commands — no need to edit files manually or restart the bot. Changes take effect immediately.

> **Who can manage commands?**
> Any server member with the **Manage Server** permission.

---

### `/addcmd` — Add a command

```
/addcmd name:schedule description:"When someone asks about stream times" response:"We stream Mon, Wed, Fri at 7 PM EST!"
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Short identifier used in logs (e.g. `schedule`) |
| `description` | yes | Describes *when* this should trigger — the AI reads this to match messages |
| `response` | yes | What the bot replies with when matched |

---

### `/listcmds` — List all commands

```
/listcmds
```

Displays every command configured for this server with its trigger description and response. Paginated (5 per page) with Prev/Next buttons if there are more than 5 commands. Only visible to you (ephemeral).

---

### `/resetcmds` — Reset to default template

```
/resetcmds
```

Replaces this server's entire command set with the default template from `commands.json`. Useful to start fresh. Irreversible without manual backup.

---

### `/editcmd` — Edit an existing command

```
/editcmd name:schedule description:"When someone asks what days streams happen"
/editcmd name:schedule response:"Streams are Tuesday and Thursday at 8 PM EST!"
/editcmd name:schedule description:"New trigger" response:"New response"
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Name of the command to edit |
| `description` | no | Leave empty to keep the current value |
| `response` | no | Leave empty to keep the current value |

---

### `/removecmd` — Remove a command

```
/removecmd name:schedule
```

Permanently deletes the command from this server's command set.

---

### `/testcmd` — Test classification

```
/testcmd message:"hey what days do you stream?"
```

Runs a message through this server's classifier and shows which command matched, the confidence score, and what the bot would reply — without actually sending a response to chat. Useful for tuning descriptions.

---

All slash commands are **ephemeral** (only you can see the responses).

## Per-Server Configuration

Each server can configure its own confidence threshold, cooldown, and watched channels using the `/config` command group. Settings are saved to the database and take effect immediately. By default the bot watches all channels — use `/config channels` to restrict it.

### `/config view` — Show current settings

```
/config view
```

### `/config threshold` — Confidence threshold

```
/config threshold value:0.75
```

Sets how confident the classifier must be before responding. Range: `0.0–1.0`. Higher = stricter.

### `/config cooldown` — Response cooldown

```
/config cooldown value:30
```

Seconds the bot waits between responses in the same channel. `0` disables the cooldown. Range: `0–3600`.

### `/config channels` — Watched channels

```
/config channels channels:general,chat,support
```

Comma-separated list of channel names to monitor. Leave blank to watch all channels.

```
/config channels
```

### `/config reset` — Reset config to defaults

```
/config reset
```

Restores this server's configuration to the defaults (`confidence_threshold: 0.6`, `cooldown: 10s`, all channels).

---

All slash commands are **ephemeral** (only you can see the responses).

## Tuning

| Setting | Default | How to change |
|---|---|---|
| Confidence threshold | `0.6` | `/config threshold` or `CONFIDENCE_THRESHOLD` env var |
| Cooldown | `10s` | `/config cooldown` or `COOLDOWN_SECONDS` env var |
| Watched channels | all | `/config channels` per server |

### Tips

- **Too many false positives?** Raise the threshold to 0.7 or 0.8 via `/config threshold`.
- **Missing obvious matches?** Lower the threshold or improve the command `description`.
- **Bot responding to everything?** Make descriptions more specific about *when* to match, not just what the topic is.
- The `description` field is what the LLM reads to decide matches — invest time writing clear, specific descriptions.

## Cost

Claude Haiku is used for classification. Each chat message costs roughly **$0.00003** to classify (a ~20 token input, ~15 token output). At 10,000 messages/day that's about **$0.30/day**.

## File Structure

```
bot.py          ← bot, slash commands, per-guild storage + config layer
classifier.py   ← IntentClassifier wrapping Anthropic API
commands.json   ← default command template seeded into new servers
config.json     ← default config template used by /config reset
Procfile        ← Railway process definition
bot.db          ← SQLite database (auto-created on first run, gitignored)
```

## Architecture

```
Discord Chat Message
        │
        ▼
   Filters (bot?, DM?, cooldown?, length?, channel?)
        │
        ▼
   Per-server classifier (Claude Haiku)
        │
        ├─ match found (confidence ≥ threshold) → reply with command response
        └─ no match → do nothing
```
