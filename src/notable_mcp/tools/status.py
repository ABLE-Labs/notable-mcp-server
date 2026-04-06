"""Tools: get_robot_status, get_available_resources"""

from __future__ import annotations

import json
import logging

from ..client import RobotClient
from ..state import ServerState

logger = logging.getLogger("notable_mcp")


async def get_robot_status(client: RobotClient, state: ServerState) -> str:
    """Get current robot and module status."""
    robot_status = await client.get_robot_status()

    result = {
        "robot": robot_status,
        "server_state": {
            "initialized": state.initialized,
            "pipette_config": state.pipette_config,
            "deck_configured_slots": [
                int(s) for s, v in state.deck_config.items() if v
            ],
            "odtc_door_closed": state.odtc_door_closed,
            "odtc_running": state.odtc_running,
            "tips_used": {
                str(deck): state.tips.used_count(deck)
                for deck in range(1, 13)
                if state.tips.used_count(deck) > 0
            },
        },
    }

    try:
        module_status = await client.get_module_status()
        result["modules"] = module_status
    except Exception as e:
        logger.warning(f"Failed to get module status: {e}")
        result["modules"] = {"error": str(e)}

    return json.dumps(result, indent=2, ensure_ascii=False, default=str)


async def get_available_resources(client: RobotClient, state: ServerState) -> str:
    """Get all available pipettes, labware, modules, and adapters.

    Also caches labware codes for configure_deck validation.
    """
    pipettes = await client.get_pipette_library()
    labware = await client.get_labware_library()
    modules = await client.get_module_library()
    adapters = await client.get_adapter_library()

    # Cache labware codes for future configure_deck validation
    state.cache_labware_codes(labware)

    result = {
        "pipettes": pipettes,
        "labware": labware,
        "modules": modules,
        "adapters": adapters,
    }
    return json.dumps(result, indent=2, ensure_ascii=False)
