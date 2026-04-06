"""Tools: run_thermocycler, shake_plate, control_odtc_door"""

from __future__ import annotations

import json
import logging

from ..safety import MAX_SHAKE_DURATION_SEC, SafetyError, validate_rpm
from ..state import ServerState

logger = logging.getLogger("notable_mcp")


async def run_thermocycler(
    client,
    state: ServerState,
    method_name: str,
) -> str:
    """Run an ODTC thermocycler method.

    The API call is blocking — it waits for the method to complete
    before returning. Use get_available_resources or ODTC pre-methods/methods
    endpoints to discover valid method names.
    """
    state.require_initialized()
    state.require_odtc_door_closed()
    state.require_odtc_not_running()

    logger.info(f"run_thermocycler: {method_name}")

    async with state.robot_action():
        state.set_odtc_running(True)
        try:
            await client.odtc_start_method(method_name=method_name)
            state.set_odtc_running(False)
            return json.dumps(
                {"status": "completed", "method": method_name},
                indent=2, ensure_ascii=False,
            )
        except Exception:
            state.set_odtc_running(False)
            raise


async def shake_plate(
    client,
    state: ServerState,
    rpm: int,
    duration_sec: float,
    accel_sec: int = 5,
) -> str:
    """Shake a plate on the orbital shaker module."""
    state.require_initialized()
    validate_rpm(rpm)
    if duration_sec <= 0:
        raise SafetyError(f"duration_sec must be positive, got {duration_sec}.")
    if duration_sec > MAX_SHAKE_DURATION_SEC:
        raise SafetyError(f"duration_sec {duration_sec} exceeds maximum ({MAX_SHAKE_DURATION_SEC}s).")

    logger.info(f"shake_plate: {rpm} RPM, {duration_sec}s")

    async with state.robot_action():
        result = await client.shaker_shake(
            rpm=rpm,
            duration_sec=duration_sec,
            accel_sec=accel_sec,
        )

    return json.dumps(
        {"status": "ok", "rpm": rpm, "duration_sec": duration_sec, "result": result},
        indent=2, ensure_ascii=False,
    )


async def control_odtc_door(client, state: ServerState, open: bool) -> str:
    """Open or close the ODTC door."""
    state.require_initialized()

    # Prevent opening door while method is running
    if open and state.odtc_running:
        raise SafetyError(
            "Cannot open ODTC door while a method is running. "
            "Wait for completion or stop the method first."
        )

    action = "Opening" if open else "Closing"
    logger.info(f"control_odtc_door: {action}")

    async with state.robot_action():
        result = await client.odtc_door(open=open)
        state.set_odtc_door(closed=not open)

    action_past = "opened" if open else "closed"
    return json.dumps(
        {"status": "ok", "door": action_past, "result": result},
        indent=2, ensure_ascii=False,
    )
