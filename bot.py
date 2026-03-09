import argparse
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

_sync_guild_id_str = os.getenv("SYNC_GUILD_ID", "").strip()
SYNC_GUILD_ID: int | None = int(_sync_guild_id_str) if _sync_guild_id_str else None

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
_guild_config: dict[int, dict] = {}


def _default_config() -> dict:
    return {
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "cooldown_seconds": COOLDOWN_SECONDS,
        "watched_channels": list(WATCHED_CHANNELS),
    }


def _config_path(guild_id: int) -> Path:
    return DATA_DIR / f"{guild_id}_config.json"


def load_guild_config(guild_id: int) -> dict:
    path = _config_path(guild_id)
    cfg = _default_config()
    if path.exists():
        with open(path) as f:
            cfg.update(json.load(f))
    return cfg


def save_guild_config(guild_id: int, config: dict):
    path = _config_path(guild_id)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2)
    os.replace(tmp, path)
    logger.info(f"[guild={guild_id}] Saved config to {path}")


def get_guild_config(guild_id: int) -> dict:
    if guild_id not in _guild_config:
        _guild_config[guild_id] = load_guild_config(guild_id)
    return _guild_config[guild_id]


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
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump({"commands": commands}, f, indent=2)
    os.replace(tmp, path)
    logger.info(f"[guild={guild_id}] Saved {len(commands)} commands to {path}")


def get_guild_commands(guild_id: int) -> list[dict]:
    if guild_id not in _guild_commands:
        _guild_commands[guild_id] = load_guild_commands(guild_id)
    return _guild_commands[guild_id]


def get_guild_classifier(guild_id: int) -> IntentClassifier:
    if guild_id not in _guild_classifiers:
        config = get_guild_config(guild_id)
        _guild_classifiers[guild_id] = IntentClassifier(
            commands=get_guild_commands(guild_id),
            confidence_threshold=config["confidence_threshold"],
        )
    return _guild_classifiers[guild_id]


def reload_guild_classifier(guild_id: int):
    commands = get_guild_commands(guild_id)
    config = get_guild_config(guild_id)
    if guild_id in _guild_classifiers:
        clf = _guild_classifiers[guild_id]
        clf.reload(commands)
        clf.confidence_threshold = config["confidence_threshold"]
    else:
        _guild_classifiers[guild_id] = IntentClassifier(
            commands=commands,
            confidence_threshold=config["confidence_threshold"],
        )


# --- Bot ---------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# per-channel cooldown tracker
_last_response_time: dict[int, float] = {}


async def _cleanup_cooldown_cache():
    while True:
        await asyncio.sleep(3600)
        cutoff = time.time() - (COOLDOWN_SECONDS + 5)
        stale = [ch for ch, t in _last_response_time.items() if t < cutoff]
        for ch in stale:
            del _last_response_time[ch]
        if stale:
            logger.info(f"Cleaned up {len(stale)} stale cooldown entries")


def _on_cooldown(channel_id: int, cooldown_seconds: int) -> bool:
    now = time.time()
    last = _last_response_time.get(channel_id, 0)
    if now - last < cooldown_seconds:
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


# --- Pagination helper -------------------------------------------------

PAGE_SIZE = 5  # commands per page


def _build_page(commands: list[dict], page: int, total_pages: int) -> str:
    start = page * PAGE_SIZE
    lines = []
    for cmd in commands[start : start + PAGE_SIZE]:
        lines.append(
            f"**`{cmd['name']}`**\n"
            f"  Triggers: {cmd['description']}\n"
            f"  Response: {cmd['response']}"
        )
    header = f"Commands (page {page + 1}/{total_pages}):\n\n"
    return header + "\n\n".join(lines)


class CmdsView(discord.ui.View):
    def __init__(self, commands: list[dict]):
        super().__init__(timeout=120)
        self.commands = commands
        self.page = 0
        self.total_pages = max(1, -(-len(commands) // PAGE_SIZE))  # ceiling div
        self._update_buttons()

    def _update_buttons(self):
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= self.total_pages - 1

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._update_buttons()
        await interaction.response.edit_message(
            content=_build_page(self.commands, self.page, self.total_pages), view=self
        )

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._update_buttons()
        await interaction.response.edit_message(
            content=_build_page(self.commands, self.page, self.total_pages), view=self
        )

    async def on_timeout(self):
        # Disable buttons after timeout; best-effort (message reference may be gone)
        for item in self.children:
            item.disabled = True


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


@tree.command(name="resetcmds", description="Reset this server's commands back to the default template")
async def reset_commands(interaction: discord.Interaction):
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

    _guild_commands[interaction.guild_id] = list(DEFAULT_COMMANDS)
    save_guild_commands(interaction.guild_id, _guild_commands[interaction.guild_id])
    reload_guild_classifier(interaction.guild_id)

    await interaction.response.send_message(
        f"Commands reset to the default template ({len(DEFAULT_COMMANDS)} commands restored).",
        ephemeral=True,
    )
    logger.info(f"[guild={interaction.guild_id}] {interaction.user} reset commands to default template")


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

    view = CmdsView(commands)
    await interaction.response.send_message(
        _build_page(commands, 0, view.total_pages),
        view=view if view.total_pages > 1 else None,
        ephemeral=True,
    )


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
    loop = asyncio.get_running_loop()
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


# --- Config slash commands ---------------------------------------------

config_group = app_commands.Group(name="config", description="View or change per-server bot settings")
tree.add_command(config_group)


@config_group.command(name="view", description="Show the current configuration for this server")
async def config_view(interaction: discord.Interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Config can only be viewed inside a server.", ephemeral=True
        )
        return

    cfg = get_guild_config(interaction.guild_id)
    channels = ", ".join(cfg["watched_channels"]) if cfg["watched_channels"] else "all channels"
    await interaction.response.send_message(
        f"**Server configuration:**\n"
        f"  Confidence threshold: `{cfg['confidence_threshold']}`\n"
        f"  Cooldown: `{cfg['cooldown_seconds']}s` per channel\n"
        f"  Watched channels: `{channels}`",
        ephemeral=True,
    )


@config_group.command(name="threshold", description="Set the minimum confidence for the bot to respond (0.0–1.0)")
@app_commands.describe(value="Confidence threshold, e.g. 0.7. Higher = stricter matching.")
async def config_threshold(
    interaction: discord.Interaction, value: app_commands.Range[float, 0.0, 1.0]
):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "You don't have permission to manage config.", ephemeral=True
        )
        return
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Config can only be changed inside a server.", ephemeral=True
        )
        return

    cfg = get_guild_config(interaction.guild_id)
    cfg["confidence_threshold"] = value
    save_guild_config(interaction.guild_id, cfg)
    reload_guild_classifier(interaction.guild_id)

    await interaction.response.send_message(
        f"Confidence threshold set to `{value}`.", ephemeral=True
    )
    logger.info(f"[guild={interaction.guild_id}] {interaction.user} set confidence_threshold={value}")


@config_group.command(name="cooldown", description="Set the response cooldown per channel in seconds")
@app_commands.describe(value="Seconds between responses in the same channel. 0 = no cooldown.")
async def config_cooldown(
    interaction: discord.Interaction, value: app_commands.Range[int, 0, 3600]
):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "You don't have permission to manage config.", ephemeral=True
        )
        return
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Config can only be changed inside a server.", ephemeral=True
        )
        return

    cfg = get_guild_config(interaction.guild_id)
    cfg["cooldown_seconds"] = value
    save_guild_config(interaction.guild_id, cfg)

    await interaction.response.send_message(
        f"Cooldown set to `{value}s` per channel.", ephemeral=True
    )
    logger.info(f"[guild={interaction.guild_id}] {interaction.user} set cooldown_seconds={value}")


@config_group.command(name="channels", description="Set which channels the bot watches")
@app_commands.describe(channels="Comma-separated channel names. Leave blank to watch all channels.")
async def config_channels(interaction: discord.Interaction, channels: str = ""):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "You don't have permission to manage config.", ephemeral=True
        )
        return
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Config can only be changed inside a server.", ephemeral=True
        )
        return

    parsed = [ch.strip() for ch in channels.split(",") if ch.strip()]
    cfg = get_guild_config(interaction.guild_id)
    cfg["watched_channels"] = parsed
    save_guild_config(interaction.guild_id, cfg)

    display = ", ".join(parsed) if parsed else "all channels"
    await interaction.response.send_message(
        f"Now watching: `{display}`.", ephemeral=True
    )
    logger.info(f"[guild={interaction.guild_id}] {interaction.user} set watched_channels={parsed}")


@config_group.command(name="reset", description="Reset this server's configuration to global defaults")
async def config_reset(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "You don't have permission to manage config.", ephemeral=True
        )
        return
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Config can only be changed inside a server.", ephemeral=True
        )
        return

    cfg = _default_config()
    _guild_config[interaction.guild_id] = cfg
    save_guild_config(interaction.guild_id, cfg)
    reload_guild_classifier(interaction.guild_id)

    channels = ", ".join(cfg["watched_channels"]) if cfg["watched_channels"] else "all channels"
    await interaction.response.send_message(
        f"Configuration reset to defaults:\n"
        f"  Confidence threshold: `{cfg['confidence_threshold']}`\n"
        f"  Cooldown: `{cfg['cooldown_seconds']}s`\n"
        f"  Watched channels: `{channels}`",
        ephemeral=True,
    )
    logger.info(f"[guild={interaction.guild_id}] {interaction.user} reset config to defaults")


# --- Chat listener -----------------------------------------------------


@client.event
async def on_guild_join(guild: discord.Guild):
    get_guild_commands(guild.id)  # seeds the file if it doesn't exist
    logger.info(f"Joined guild {guild.name} (id={guild.id}), initialized command file")


@client.event
async def on_guild_remove(guild: discord.Guild):
    _guild_commands.pop(guild.id, None)
    _guild_classifiers.pop(guild.id, None)
    _guild_config.pop(guild.id, None)
    logger.info(f"Removed from guild {guild.name} (id={guild.id}), cleared from cache")


_sync_to_guild: bool = False  # set by CLI arg before client.run()


@client.event
async def on_ready():
    asyncio.create_task(_cleanup_cooldown_cache())
    for guild in client.guilds:
        get_guild_commands(guild.id)  # seeds the file if it doesn't exist

    if _sync_to_guild:
        if not SYNC_GUILD_ID:
            logger.error("--sync-guild flag used but SYNC_GUILD_ID is not set in .env — skipping sync")
        else:
            guild_obj = discord.Object(id=SYNC_GUILD_ID)
            tree.copy_global_to(guild=guild_obj)
            await tree.sync(guild=guild_obj)
            logger.info(f"Synced {len(tree.get_commands())} slash commands to test guild {SYNC_GUILD_ID} (instant)")
    else:
        await tree.sync()
        logger.info(f"Synced {len(tree.get_commands())} slash commands globally (may take up to 1 hour)")

    logger.info(f"Logged in as {client.user} (id={client.user.id})")
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
    cfg = get_guild_config(message.guild.id)
    if cfg["watched_channels"] and message.channel.name not in cfg["watched_channels"]:
        return
    if len(message.content.strip()) < 5:
        return
    if message.content.strip().startswith(("!", "/")):
        return
    if _on_cooldown(message.channel.id, cfg["cooldown_seconds"]):
        return

    clf = get_guild_classifier(message.guild.id)
    loop = asyncio.get_running_loop()
    matched = await loop.run_in_executor(None, clf.classify, message.content)

    if matched:
        logger.info(
            f"[guild={message.guild.id}] #{message.channel.name} | {message.author}: {message.content!r} "
            f"→ !{matched['name']} (conf={matched['confidence']:.2f})"
        )
        await message.reply(matched["response"], mention_author=False)


# --- Entrypoint --------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sync-guild",
        action="store_true",
        help="Sync slash commands to the test guild in SYNC_GUILD_ID instead of globally (instant).",
    )
    args = parser.parse_args()
    _sync_to_guild = args.sync_guild

    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN not set. Add it to your .env file.")
    if not ANTHROPIC_API_KEY:
        raise SystemExit("ANTHROPIC_API_KEY not set. Add it to your .env file.")
    if not ADMIN_IDS:
        logger.warning(
            "ADMIN_IDS is empty. Users with Manage Server permission can still manage commands."
        )

    client.run(DISCORD_TOKEN)
