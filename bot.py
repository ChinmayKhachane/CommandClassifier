import discord
from discord import app_commands
import json
import logging
import os
import asyncio
import time
from pathlib import Path
from dotenv import load_dotenv
from classifier import IntentClassifier

load_dotenv()

# --- Config -----------------------------------------------------------

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.6"))

WATCHED_CHANNELS: list[str] = [
    ch.strip()
    for ch in os.getenv("WATCHED_CHANNELS", "").split(",")
    if ch.strip()
]

COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "10"))

# Discord user IDs that can manage commands globally. Comma-separated.
# Find your ID: enable Developer Mode in Discord, right-click yourself, Copy User ID.
ADMIN_IDS: set[int] = {
    int(uid.strip())
    for uid in os.getenv("ADMIN_IDS", "").split(",")
    if uid.strip()
}

# --- Logging -----------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot")

# --- Per-guild command storage ----------------------------------------

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

COMMANDS_PATH = Path(__file__).parent / "commands.json"

with open(COMMANDS_PATH) as f:
    DEFAULT_COMMANDS: list[dict] = json.load(f)["commands"]

# In-memory caches keyed by guild_id
_guild_commands: dict[int, list[dict]] = {}
_guild_classifiers: dict[int, IntentClassifier] = {}


def _guild_path(guild_id: int) -> Path:
    return DATA_DIR / f"{guild_id}.json"


def load_guild_commands(guild_id: int) -> list[dict]:
    path = _guild_path(guild_id)
    if path.exists():
        with open(path) as f:
            return json.load(f)["commands"]
    # No file yet — seed from the default template
    save_guild_commands(guild_id, DEFAULT_COMMANDS)
    logger.info(f"[guild={guild_id}] Created default commands file from template")
    return list(DEFAULT_COMMANDS)


def save_guild_commands(guild_id: int, commands: list[dict]):
    path = _guild_path(guild_id)
    with open(path, "w") as f:
        json.dump({"commands": commands}, f, indent=2)
    logger.info(f"[guild={guild_id}] Saved {len(commands)} commands to {path}")


def get_guild_commands(guild_id: int) -> list[dict]:
    if guild_id not in _guild_commands:
        _guild_commands[guild_id] = load_guild_commands(guild_id)
    return _guild_commands[guild_id]


def get_guild_classifier(guild_id: int) -> IntentClassifier:
    if guild_id not in _guild_classifiers:
        _guild_classifiers[guild_id] = IntentClassifier(
            commands=get_guild_commands(guild_id),
            confidence_threshold=CONFIDENCE_THRESHOLD,
        )
    return _guild_classifiers[guild_id]


def reload_guild_classifier(guild_id: int):
    commands = get_guild_commands(guild_id)
    if guild_id in _guild_classifiers:
        _guild_classifiers[guild_id].reload(commands)
    else:
        _guild_classifiers[guild_id] = IntentClassifier(
            commands=commands,
            confidence_threshold=CONFIDENCE_THRESHOLD,
        )


# --- Bot ---------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# per-channel cooldown tracker
_last_response_time: dict[int, float] = {}


def _on_cooldown(channel_id: int) -> bool:
    now = time.time()
    last = _last_response_time.get(channel_id, 0)
    if now - last < COOLDOWN_SECONDS:
        return True
    _last_response_time[channel_id] = now
    return False


def is_admin(interaction: discord.Interaction) -> bool:
    """Global admins OR users with the Manage Guild permission can manage commands."""
    if interaction.user.id in ADMIN_IDS:
        return True
    if isinstance(interaction.user, discord.Member):
        return interaction.user.guild_permissions.manage_guild
    return False


# --- Admin slash commands ----------------------------------------------


@tree.command(name="addcmd", description="Add a new auto-response command for this server")
@app_commands.describe(
    name="Short identifier (e.g. 'schedule')",
    description="When should this trigger? Describe the kind of messages that should match.",
    response="What the bot should reply with when matched.",
)
async def add_command(
    interaction: discord.Interaction, name: str, description: str, response: str
):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "You don't have permission to manage commands.", ephemeral=True
        )
        return

    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Commands can only be managed inside a server.", ephemeral=True
        )
        return

    commands = get_guild_commands(interaction.guild_id)
    for cmd in commands:
        if cmd["name"] == name:
            await interaction.response.send_message(
                f"Command `{name}` already exists. Use `/editcmd` to update it.",
                ephemeral=True,
            )
            return

    commands.append({"name": name, "description": description, "response": response})
    save_guild_commands(interaction.guild_id, commands)
    reload_guild_classifier(interaction.guild_id)

    await interaction.response.send_message(
        f"Added command `{name}`.\n"
        f"**Triggers when:** {description}\n"
        f"**Responds with:** {response}",
        ephemeral=True,
    )
    logger.info(f"[guild={interaction.guild_id}] {interaction.user} added command: {name}")


@tree.command(name="removecmd", description="Remove an auto-response command from this server")
@app_commands.describe(name="Name of the command to remove")
async def remove_command(interaction: discord.Interaction, name: str):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "You don't have permission to manage commands.", ephemeral=True
        )
        return

    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Commands can only be managed inside a server.", ephemeral=True
        )
        return

    commands = get_guild_commands(interaction.guild_id)
    for i, cmd in enumerate(commands):
        if cmd["name"] == name:
            commands.pop(i)
            save_guild_commands(interaction.guild_id, commands)
            reload_guild_classifier(interaction.guild_id)
            await interaction.response.send_message(
                f"Removed command `{name}`.", ephemeral=True
            )
            logger.info(f"[guild={interaction.guild_id}] {interaction.user} removed command: {name}")
            return

    await interaction.response.send_message(
        f"Command `{name}` not found.", ephemeral=True
    )


@tree.command(name="editcmd", description="Edit an existing auto-response command on this server")
@app_commands.describe(
    name="Name of the command to edit",
    description="New trigger description (leave empty to keep current)",
    response="New response text (leave empty to keep current)",
)
async def edit_command(
    interaction: discord.Interaction,
    name: str,
    description: str = "",
    response: str = "",
):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "You don't have permission to manage commands.", ephemeral=True
        )
        return

    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Commands can only be managed inside a server.", ephemeral=True
        )
        return

    commands = get_guild_commands(interaction.guild_id)
    for cmd in commands:
        if cmd["name"] == name:
            if description:
                cmd["description"] = description
            if response:
                cmd["response"] = response
            save_guild_commands(interaction.guild_id, commands)
            reload_guild_classifier(interaction.guild_id)
            await interaction.response.send_message(
                f"Updated command `{name}`.\n"
                f"**Triggers when:** {cmd['description']}\n"
                f"**Responds with:** {cmd['response']}",
                ephemeral=True,
            )
            logger.info(f"[guild={interaction.guild_id}] {interaction.user} edited command: {name}")
            return

    await interaction.response.send_message(
        f"Command `{name}` not found.", ephemeral=True
    )


@tree.command(name="listcmds", description="List all auto-response commands for this server")
async def list_commands(interaction: discord.Interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Commands can only be listed inside a server.", ephemeral=True
        )
        return

    commands = get_guild_commands(interaction.guild_id)
    if not commands:
        await interaction.response.send_message(
            "No commands configured for this server yet. Use `/addcmd` to add one.",
            ephemeral=True,
        )
        return

    lines = []
    for cmd in commands:
        lines.append(
            f"**`{cmd['name']}`**\n"
            f"  Triggers: {cmd['description']}\n"
            f"  Response: {cmd['response']}"
        )

    text = "\n\n".join(lines)
    if len(text) > 1900:
        text = text[:1900] + "\n\n*...truncated*"

    await interaction.response.send_message(text, ephemeral=True)


@tree.command(name="testcmd", description="Test how a message would be classified on this server")
@app_commands.describe(message="The message to test classification on")
async def test_command(interaction: discord.Interaction, message: str):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "You don't have permission to use this.", ephemeral=True
        )
        return

    if interaction.guild_id is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    clf = get_guild_classifier(interaction.guild_id)
    loop = asyncio.get_event_loop()
    matched = await loop.run_in_executor(None, clf.classify, message)

    if matched:
        await interaction.followup.send(
            f"**Input:** {message}\n"
            f"**Matched:** `{matched['name']}` (confidence: {matched['confidence']:.2f})\n"
            f"**Would reply:** {matched['response']}",
            ephemeral=True,
        )
    else:
        await interaction.followup.send(
            f"**Input:** {message}\n"
            f"**Result:** No match (below threshold or no relevant command)",
            ephemeral=True,
        )


# --- Chat listener -----------------------------------------------------


@client.event
async def on_guild_join(guild: discord.Guild):
    get_guild_commands(guild.id)  # seeds the file if it doesn't exist
    logger.info(f"Joined guild {guild.name} (id={guild.id}), initialized command file")


@client.event
async def on_guild_remove(guild: discord.Guild):
    _guild_commands.pop(guild.id, None)
    _guild_classifiers.pop(guild.id, None)
    logger.info(f"Removed from guild {guild.name} (id={guild.id}), cleared from cache")


@client.event
async def on_ready():
    await tree.sync()
    for guild in client.guilds:
        get_guild_commands(guild.id)  # seeds the file if it doesn't exist
    logger.info(f"Logged in as {client.user} (id={client.user.id})")
    logger.info(f"Synced {len(tree.get_commands())} slash commands")
    if ADMIN_IDS:
        logger.info(f"Global admin user IDs: {ADMIN_IDS}")
    else:
        logger.warning("No ADMIN_IDS set — only users with Manage Server permission can manage commands!")
    if WATCHED_CHANNELS:
        logger.info(f"Watching channels: {WATCHED_CHANNELS}")
    else:
        logger.info("Watching ALL channels")


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user or message.author.bot:
        return
    if message.guild is None:
        return  # ignore DMs
    if WATCHED_CHANNELS and message.channel.name not in WATCHED_CHANNELS:
        return
    if len(message.content.strip()) < 5:
        return
    if message.content.strip().startswith(("!", "/")):
        return
    if _on_cooldown(message.channel.id):
        return

    clf = get_guild_classifier(message.guild.id)
    loop = asyncio.get_event_loop()
    matched = await loop.run_in_executor(None, clf.classify, message.content)

    if matched:
        logger.info(
            f"[guild={message.guild.id}] #{message.channel.name} | {message.author}: {message.content!r} "
            f"→ !{matched['name']} (conf={matched['confidence']:.2f})"
        )
        await message.reply(matched["response"], mention_author=False)


# --- Entrypoint --------------------------------------------------------

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN not set. Add it to your .env file.")
    if not ANTHROPIC_API_KEY:
        raise SystemExit("ANTHROPIC_API_KEY not set. Add it to your .env file.")
    if not ADMIN_IDS:
        logger.warning(
            "ADMIN_IDS is empty. Users with Manage Server permission can still manage commands."
        )

    client.run(DISCORD_TOKEN)
