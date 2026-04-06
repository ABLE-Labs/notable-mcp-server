"""Tools: configure_pipette, configure_deck"""

from __future__ import annotations

import json
import logging

from ..client import RobotClient
from ..safety import SafetyError, validate_deck_number, validate_pipette_code
from ..state import ServerState

logger = logging.getLogger("notable_mcp")


async def configure_pipette(client: RobotClient, state: ServerState, pipette_config: dict[str, str]) -> str:
    """Configure which pipettes are mounted on the robot."""
    for slot, code in pipette_config.items():
        if slot not in ("1", "2"):
            raise SafetyError(f"Pipette slot must be '1' (left) or '2' (right), got '{slot}'.")
        if code:
            validate_pipette_code(code)

    result = await client.set_pipette_config(pipette_config)
    state.update_pipette_config(pipette_config)
    return json.dumps({"status": "ok", "pipette_config": result}, indent=2, ensure_ascii=False)


async def configure_deck(client: RobotClient, state: ServerState, deck_config: dict[str, str | dict]) -> str:
    """Configure the deck layout."""
    for slot_str, value in deck_config.items():
        try:
            validate_deck_number(int(slot_str))
        except ValueError:
            raise SafetyError(f"Deck slot must be a number 1-12, got '{slot_str}'.")

        # Type check: only str, dict, or None allowed
        if value is not None and not isinstance(value, (str, dict)):
            raise SafetyError(
                f"Deck slot {slot_str} value must be a labware code (string), "
                f"module config (object), or null — got {type(value).__name__}."
            )

        # Validate labware code against cached library
        if isinstance(value, str):
            labware_code = value
        elif isinstance(value, dict):
            labware_code = value.get("labware")
        else:
            continue

        if labware_code and not state.is_labware_code_valid(labware_code):
            raise SafetyError(
                f"Unknown labware code '{labware_code}' for deck slot {slot_str}. "
                "Call get_available_resources first to see valid codes."
            )

    result = await client.set_deck_config(deck_config)
    state.update_deck_config(deck_config)
    return json.dumps({"status": "ok", "deck_config": result}, indent=2, ensure_ascii=False)
