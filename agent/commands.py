"""Slash command parser for CircuitBot.

Handles /design, /add, /modify, /help and other slash commands.
Only /design triggers the full automated pipeline.
"""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class SlashCommand:
    """Parsed slash command."""
    command: str          # The command name (e.g., "design", "add", "modify")
    args: str             # Arguments after the command
    raw: str              # Original raw text
    is_slash_command: bool  # Whether this was a slash command


# Commands that trigger the full design pipeline
PIPELINE_COMMANDS = {"design"}

# All supported commands
SUPPORTED_COMMANDS = {
    "design": "Full PCB design pipeline - generates schematic and PCB from description",
    "add": "Add a component to the current design",
    "modify": "Modify an existing component or connection",
    "remove": "Remove a component from the design",
    "help": "Show available commands and usage",
    "status": "Show current design status",
    "export": "Export the current design",
}


def parse_command(text: str) -> SlashCommand:
    """Parse a slash command from user input.

    Args:
        text: Raw user input text

    Returns:
        SlashCommand with parsed command and arguments
    """
    text = text.strip()

    # Check if this is a slash command
    if not text.startswith("/"):
        return SlashCommand(
            command="",
            args=text,
            raw=text,
            is_slash_command=False,
        )

    # Parse /command [args]
    match = re.match(r"^/(\w+)\s*(.*)", text, re.DOTALL)
    if not match:
        return SlashCommand(
            command="",
            args=text,
            raw=text,
            is_slash_command=False,
        )

    command = match.group(1).lower()
    args = match.group(2).strip()

    return SlashCommand(
        command=command,
        args=args,
        raw=text,
        is_slash_command=True,
    )


def is_pipeline_command(text: str) -> bool:
    """Check if the text is a /design command that triggers the full pipeline.

    Args:
        text: Raw user input text

    Returns:
        True if this is a /design command
    """
    cmd = parse_command(text)
    return cmd.is_slash_command and cmd.command in PIPELINE_COMMANDS


def get_command_help() -> str:
    """Get help text for all supported commands.

    Returns:
        Formatted help string
    """
    lines = ["Available commands:"]
    for cmd, desc in SUPPORTED_COMMANDS.items():
        lines.append(f"  /{cmd} — {desc}")
    lines.append("")
    lines.append("Examples:")
    lines.append('  /design ESP32 with BME280 temperature sensor')
    lines.append("  /add 10k resistor")
    lines.append("  /modify R1 value to 4.7k")
    lines.append("  /remove C3")
    return "\n".join(lines)


def get_command_hint(text: str) -> Optional[str]:
    """Get a hint for the user if they might have meant a slash command.

    Args:
        text: Raw user input text

    Returns:
        Hint string if applicable, None otherwise
    """
    text_lower = text.lower().strip()

    # Detect common patterns that should use /design
    design_patterns = [
        r"^design\b",
        r"^create\b",
        r"^build\b",
        r"^make\b",
        r"^generate\b",
    ]
    for pattern in design_patterns:
        if re.match(pattern, text_lower):
            return f"Tip: Use `/design {text}` to start the full design pipeline."

    return None
