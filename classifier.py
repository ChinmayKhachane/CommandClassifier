import anthropic
import json
import logging

logger = logging.getLogger(__name__)


class IntentClassifier:
    def __init__(self, commands: list[dict], confidence_threshold: float = 0.6):
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
        self.commands = commands
        self.confidence_threshold = confidence_threshold
        self._system_prompt = self._build_system_prompt()

    def reload(self, commands: list[dict]):
        """Hot-reload the command list without restarting."""
        self.commands = commands
        self._system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        commands_block = "\n".join(
            f'- "{cmd["name"]}": {cmd["description"]}'
            for cmd in self.commands
        )

        return f"""You are an intent classifier for a chat bot. Your job is to determine
if a chat message matches one of the available commands.

Available commands:
{commands_block}

Rules:
- Messages do NOT need to look like commands. People speak naturally.
- Only match if the message has a genuine connection to a command's purpose.
- General chatter, greetings, jokes, emotes, reactions, and off-topic messages should NOT match any command.
- When in doubt, respond with "none".

Respond with ONLY a JSON object in this exact format, no other text:
{{"command": "<command_name or none>", "confidence": <0.0 to 1.0>}}"""

    def classify(self, message: str) -> dict | None:
        """
        Classify a chat message against available commands.
        Returns the matched command dict if confident enough, else None.
        """
        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=64,
                system=self._system_prompt,
                messages=[{"role": "user", "content": message}],
            )

            raw = response.content[0].text.strip()
            logger.debug(f"Raw LLM response: {raw!r}")

            # strip markdown fences and any preamble
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            # extract the JSON object even if there's extra text around it
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start == -1 or end == 0:
                logger.warning(f"No JSON found in response: {raw!r}")
                return None
            raw = raw[start:end]

            result = json.loads(raw)

            command_name = result.get("command", "none")
            confidence = float(result.get("confidence", 0.0))

            logger.info(
                f"Message: {message!r} → command={command_name}, confidence={confidence:.2f}"
            )

            if command_name == "none" or confidence < self.confidence_threshold:
                return None

            # find the matching command
            for cmd in self.commands:
                if cmd["name"] == command_name:
                    return {**cmd, "confidence": confidence}

            return None

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Classification parse error: {e}")
            return None
        except anthropic.APIError as e:
            logger.error(f"Anthropic API error: {e}")
            return None