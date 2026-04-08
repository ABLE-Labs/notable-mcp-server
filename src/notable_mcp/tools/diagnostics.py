"""Tools: diagnose_error, get_error_log"""

from __future__ import annotations

import json
import logging

from ..client import RobotClient
from ..state import ServerState

logger = logging.getLogger("notable_mcp")


async def diagnose_error(client: RobotClient, state: ServerState) -> str:
    """Run comprehensive diagnostics on the robot system.

    Checks connection, initialization, config, tips, modules,
    and recent errors — returns all issues found.
    """
    issues: list[str] = []
    checks: dict = {}

    # 1. API connection
    try:
        await client.check_connection()
        checks["connection"] = "ok"
    except Exception as e:
        checks["connection"] = f"FAILED: {e}"
        issues.append(f"API connection failed: {e}")

    # 2. Initialization
    checks["initialized"] = state.initialized
    if not state.initialized:
        issues.append("Robot not initialized. Call initialize_robot first.")

    # 3. Pipette config
    checks["pipette_config"] = state.pipette_config
    if not any(v for v in state.pipette_config.values()):
        issues.append("No pipette configured. Call configure_pipette.")

    # 4. Deck config
    configured_decks = [s for s, v in state.deck_config.items() if v]
    checks["deck_configured"] = configured_decks
    if not configured_decks:
        issues.append("No deck configured. Call configure_deck.")

    # 5. Tip availability
    tip_warnings: list[str] = []
    for deck in range(1, 13):
        used = state.tips.used_count(deck)
        if used > 0:
            remaining = 96 - used
            if remaining == 0:
                tip_warnings.append(f"Deck {deck}: tips exhausted (96/96 used)")
            elif remaining < 10:
                tip_warnings.append(f"Deck {deck}: tips low ({used}/96 used, {remaining} remaining)")
    if tip_warnings:
        checks["tip_warnings"] = tip_warnings
        issues.extend(tip_warnings)

    # 6. ODTC state
    checks["odtc"] = {
        "door_closed": state.odtc_door_closed,
        "running": state.odtc_running,
    }
    if state.odtc_running:
        issues.append("ODTC method is running — wait for completion.")

    # 7. Robot status from API
    if checks["connection"] == "ok":
        try:
            robot_status = await client.get_robot_status()
            checks["robot_status"] = robot_status
        except Exception as e:
            checks["robot_status"] = f"FAILED: {e}"
            issues.append(f"Failed to query robot status: {e}")

        try:
            module_status = await client.get_module_status()
            checks["module_status"] = module_status
        except Exception as e:
            checks["module_status"] = f"FAILED: {e}"

    # 8. Recent errors
    recent_errors = state.error_log.get_recent(5)
    if recent_errors:
        checks["recent_errors"] = recent_errors
        issues.append(f"{len(recent_errors)} recent error(s) found — use get_error_log for details.")

    return json.dumps(
        {
            "status": "issues_found" if issues else "ok",
            "issue_count": len(issues),
            "issues": issues,
            "checks": checks,
        },
        indent=2,
        ensure_ascii=False,
        default=str,
    )


async def get_error_log(
    client: RobotClient,
    state: ServerState,
    count: int = 30,
    clear: bool = False,
) -> str:
    """Get recent error and warning logs from the server."""
    records = state.error_log.get_recent(count)
    if clear:
        state.error_log.clear()
        logger.info("Error log cleared")

    return json.dumps(
        {
            "count": len(records),
            "cleared": clear,
            "logs": records,
        },
        indent=2,
        ensure_ascii=False,
    )
