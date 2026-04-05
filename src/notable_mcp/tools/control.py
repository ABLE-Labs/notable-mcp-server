"""Tools: initialize_robot, emergency_stop"""

from __future__ import annotations

import json
import logging

from ..state import ServerState

logger = logging.getLogger("notable_mcp")


async def initialize_robot(
    client,
    state: ServerState,
    home_axes: bool = True,
    move_to_ready: bool = True,
    modules: list[str] | None = None,
) -> str:
    """Initialize the robot and optionally connected modules.

    After initialization, reads the robot's current pipette and deck
    configuration to sync ServerState with the physical hardware.
    """
    results = {}

    if modules:
        await client.module_use(modules)
        for module_name in modules:
            if module_name == "odtc":
                await client.odtc_initialize()
            elif module_name == "shaker":
                await client.shaker_initialize()
        results["modules_initialized"] = modules

    result = await client.initialize(home_axes=home_axes, move_to_ready=move_to_ready)
    results["robot"] = result
    results["status"] = "ok"

    # Sync physical state from robot
    try:
        pipette_config = await client.get_pipette_config()
        deck_config = await client.get_deck_config()
        if pipette_config:
            state.update_pipette_config(pipette_config)
        if deck_config:
            state.update_deck_config(deck_config)
        results["synced_pipette_config"] = state.pipette_config
        results["synced_deck_config"] = {
            s: v for s, v in state.deck_config.items() if v
        }
        logger.info(
            f"Config synced from robot — pipettes: {state.pipette_config}, "
            f"deck slots: {list(results['synced_deck_config'].keys())}"
        )
    except Exception as e:
        logger.warning(f"Failed to sync config from robot: {e}")
        results["config_sync_warning"] = str(e)

    state.set_initialized()
    logger.info(f"Robot initialized (home={home_axes}, ready={move_to_ready}, modules={modules})")
    return json.dumps(results, indent=2, ensure_ascii=False)


async def emergency_stop(client, state: ServerState) -> str:
    """Emergency stop the robot immediately.

    Stops all motion, clears command queue. Robot must be
    re-initialized after an emergency stop.
    """
    logger.warning("EMERGENCY STOP triggered")
    result = await client.stop()
    state.reset()
    return json.dumps(
        {"status": "stopped", "message": "Emergency stop executed. Robot must be re-initialized.", "result": result},
        indent=2,
        ensure_ascii=False,
    )


async def pause_robot(client, state: ServerState) -> str:
    """Pause the robot. Resume with resume_robot."""
    logger.info("Robot paused")
    result = await client.pause()
    return json.dumps({"status": "paused", "result": result}, indent=2, ensure_ascii=False)


async def resume_robot(client, state: ServerState) -> str:
    """Resume the robot after a pause."""
    logger.info("Robot resumed")
    result = await client.resume()
    return json.dumps({"status": "resumed", "result": result}, indent=2, ensure_ascii=False)
