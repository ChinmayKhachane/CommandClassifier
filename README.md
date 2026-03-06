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

Edit `commands.json` — this is the **template** that every new server starts with when the bot joins. It does not affect servers that already have their own commands file.

Each command has:

- **name**: identifier for logging
- **description**: tells the AI *when* this command should match (this is the most important field — be descriptive)
- **response**: what the bot sends when matched

### 4. Run

```bash
python bot.py
```

## Per-Server Commands

Each server gets its own command set stored in `data/{guild_id}.json`. When the bot joins a server for the first time, it automatically creates that server's file by copying `commands.json` as a starting point.

From that point on, each server's commands are fully independent — changes in one server don't affect any other.

All slash commands operate on the server they are used in.

## Managing Commands

Once the bot is running, admins can manage commands live via Discord slash commands — no need to edit files manually or restart the bot. Changes take effect immediately.

> **Who can manage commands?**
> - Any Discord user ID listed in `ADMIN_IDS` in `.env`
> - Any server member with the **Manage Server** permission
>
> To find your user ID: enable Developer Mode in Discord settings, then right-click your username and select **Copy User ID**.

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

Displays every command configured for this server with its trigger description and response. Only visible to you (ephemeral).

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

## Tuning

| Env Variable | Default | Description |
|---|---|---|
| `CONFIDENCE_THRESHOLD` | `0.6` | Minimum confidence (0-1) to trigger a response. Raise to reduce false positives. |
| `COOLDOWN_SECONDS` | `10` | Per-channel cooldown between responses. Prevents spam. |
| `WATCHED_CHANNELS` | *(empty = all)* | Comma-separated channel names to monitor. |

### Tips

- **Too many false positives?** Raise `CONFIDENCE_THRESHOLD` to 0.7 or 0.8.
- **Missing obvious matches?** Lower the threshold or improve the command `description`.
- **Bot responding to everything?** Make descriptions more specific about *when* to match, not just what the topic is.
- The `description` field is what the LLM reads to decide matches — invest time writing clear, specific descriptions.

## Cost

Claude Haiku is used for classification. Each chat message costs roughly **$0.00003** to classify (a ~20 token input, ~15 token output). At 10,000 messages/day that's about **$0.30/day**.

## File Structure

```
commands.json        ← default template copied to new servers
data/
  123456789.json     ← server-specific commands (auto-created)
  987654321.json
  ...
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
