"""NOTABLE MCP Server -- main entry point.

Exposes NOTABLE liquid handler capabilities as MCP tools
that any AI agent can discover and use.

Usage:
    # Connect to real robot
    notable-mcp --host localhost --port 7777

    # Simulation mode (no robot needed)
    notable-mcp --simulate
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .client import NotableAPIError, NotableClient
from .safety import SafetyError
from .simulator import SimulatedClient
from .state import ServerState
from .tools import config, control, diagnostics, liquid, modules, protocol, status

logger = logging.getLogger("notable_mcp")

# ---------------------------------------------------------------------------
# Server instructions — sent to the AI agent on connection
# ---------------------------------------------------------------------------

SERVER_INSTRUCTIONS = """\
You are controlling a NOTABLE liquid handling robot (ABLE Labs) via MCP tools.
Incorrect configuration can cause physical damage. Follow this workflow strictly.

## ABOUT THE NOTABLE ROBOT

NOTABLE is a 12-deck-slot liquid handling robot with:
- **2 pipette slots**: Slot 1 (left) and Slot 2 (right)
- **Pipette types**: 1-channel (1ch) and 8-channel (8ch), in 20uL / 200uL / 1000uL volumes
- **Deck**: 12 slots (4 rows x 3 columns) for tip racks, well plates, reservoirs, trash, and modules
- **Modules**: ODTC (On-Deck Thermocycler), Orbital Shaker, and others
- **Well format**: Standard 96-well (A1-H12, rows A-H, columns 1-12)

## MANDATORY SETUP WORKFLOW

Before ANY liquid handling, you MUST complete these steps IN ORDER:

1. **get_available_resources** — discover valid pipette codes, labware codes, and module options.
2. **ASK the user** about the physical robot state:
   - Which pipettes are physically mounted? (slot 1 = left, slot 2 = right)
   - What labware is on each deck slot? (plates, tip racks, reservoirs, trash)
   - Where are the tip racks and how many tips are available?
3. **configure_pipette** — set ONLY what the user confirmed.
4. **configure_deck** — set ONLY what the user confirmed.
5. **initialize_robot** — homes axes, syncs physical state, and optionally initializes modules.
6. Now liquid handling tools are available.

## CRITICAL SAFETY RULES

- NEVER guess or assume the physical configuration.
- NEVER call configure_pipette or configure_deck without explicit user confirmation.
- NEVER invent deck slot numbers, tip rack locations, or pipette types.
- If you are unsure about ANY physical state, ASK the user before proceeding.
- Wrong configuration → robot moves to wrong position → collision → hardware damage.

## LIQUID HANDLING GUIDE

- **transfer_liquid**: Single well-to-well transfer. Picks up tip → aspirate → dispense → drop tip.
- **distribute_liquid**: One source → multiple destinations. Uses a new tip per destination.
- **mix_liquid**: Repeated aspirate/dispense in one well. For resuspending or mixing.
- Always specify **tip_deck** (deck slot where tip rack is placed).
- Tips are auto-tracked — used tips are skipped automatically.
- If tips run out, inform the user to replace the tip rack.
- Use **source_z_reference** / **dest_z_reference** to control pipette depth:
  - "bottom": Inside the liquid (default, for most transfers)
  - "top": Above the well (to avoid contamination)

## MODULE GUIDE

- **ODTC (Thermocycler)**: Close door → run method → open door. Methods are blocking.
  Run pre-heat methods first (e.g., "PRE25"), then PCR methods.
- **Shaker**: Specify RPM (100-3000) and duration. Robot must be initialized first.

## PIPETTE VOLUME RANGES

| Pipette | Min (uL) | Max (uL) |
|---------|----------|----------|
| 1ch_20ul / 8ch_20ul | 0.5 | 20 |
| 1ch_200ul / 8ch_200ul | 1 | 200 |
| 1ch_1000ul / 8ch_1000ul | 1 | 1000 |

## ERROR DIAGNOSIS

- **diagnose_error**: Run when something fails. Checks connection, config, tips, modules, and recent errors.
- **get_error_log**: View recent WARNING/ERROR logs. Add clear=true to reset after reading.

## PROTOCOL SAVE & RE-RUN

Protocols let you save a sequence of liquid handling steps and re-execute them later.

1. **save_protocol**: Define steps as [{tool, arguments}, ...]. Only action tools allowed.
   - **capture_setup=true**: Snapshots current pipette/deck config into the protocol.
   - **setup**: Or provide explicit pipette_config, deck_config, modules.
2. **list_protocols** / **get_protocol**: Browse saved protocols.
3. **run_protocol**: Re-execute a saved protocol. Stops on first error.
   - If the protocol has **setup**: auto-configures pipette, deck, and initializes the robot.
   - If no setup: robot must already be configured and initialized.
   - **IMPORTANT**: Always confirm with the user that the physical robot state matches before running.
   - Use **dry_run=true** to validate all steps against the simulator first without touching the real robot.
4. **get_protocol_run_history**: View recent run results (status, steps completed, duration) for a protocol.
5. **delete_protocol**: Remove a protocol.
"""

# ---------------------------------------------------------------------------
# Tool definitions -- these are what AI agents see
# ---------------------------------------------------------------------------

TOOLS = [
    # --- Status ---
    Tool(
        name="get_robot_status",
        description=(
            "Get current NOTABLE robot and module status. "
            "Returns initialization state, pipette config, deck config, and module status."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_available_resources",
        description=(
            "Get all available pipettes, labware, modules, and adapters. "
            "Call this first to discover valid codes for configure_pipette and configure_deck."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    # --- Config ---
    Tool(
        name="configure_pipette",
        description=(
            'Configure which pipettes are mounted. Slot "1" = left, "2" = right. '
            'Example: {"1": "1ch_1000ul", "2": "8ch_20ul"}. '
            "Call get_available_resources first to see valid pipette codes. "
            "CRITICAL: Configuration MUST match the physically mounted pipettes. "
            "NEVER guess — always ask the user what pipettes are installed before calling this."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "pipette_config": {
                    "type": "object",
                    "description": 'Slot ("1" or "2") to pipette code.',
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["pipette_config"],
        },
    ),
    Tool(
        name="configure_deck",
        description=(
            "Configure the deck layout (slots 1-12). "
            'Simple: {"1": "spl_96_well_plate_30096"}. '
            'Module: {"7": {"module": "odtc", "labware": "spl_96_well_plate_30096"}}. '
            "Call get_available_resources first to see valid codes. "
            "CRITICAL: Configuration MUST match the physical labware placement on the robot. "
            "NEVER guess — always ask the user to confirm the deck layout (what labware is in which slot, "
            "where tip racks are placed) before calling this."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "deck_config": {
                    "type": "object",
                    "description": "Deck slot number (string) to labware code or module config.",
                },
            },
            "required": ["deck_config"],
        },
    ),
    # --- Control ---
    Tool(
        name="initialize_robot",
        description=(
            "Initialize the robot (home axes + ready position) and optionally modules. "
            "MUST be called before any movement or liquid handling commands. "
            "After initialization, returns the synced pipette and deck configuration "
            "read from the robot's current physical state."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "home_axes": {"type": "boolean", "description": "Home all axes. Default: true.", "default": True},
                "move_to_ready": {"type": "boolean", "description": "Move to ready position. Default: true.", "default": True},
                "modules": {
                    "type": "array", "items": {"type": "string"},
                    "description": 'Modules to init, e.g. ["odtc", "shaker"].',
                },
            },
        },
    ),
    Tool(
        name="emergency_stop",
        description=(
            "EMERGENCY STOP. Immediately halts all robot motion. "
            "Robot must be re-initialized after calling this."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="pause_robot",
        description="Pause the robot. Call resume_robot to continue.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="resume_robot",
        description="Resume the robot after a pause.",
        inputSchema={"type": "object", "properties": {}},
    ),
    # --- Liquid handling ---
    Tool(
        name="transfer_liquid",
        description=(
            "Transfer liquid from one well to another. Automatically handles: "
            "pick up tip -> aspirate -> dispense -> drop tip. "
            "Requires: configure_pipette, configure_deck, initialize_robot called first. "
            "All parameters (deck slots, tip_deck, pipette_number) must correspond to "
            "the confirmed physical setup — do not invent values."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "source_deck": {"type": "integer", "description": "Source deck slot (1-12)."},
                "source_well": {"type": "string", "description": 'Source well (e.g. "A1").'},
                "dest_deck": {"type": "integer", "description": "Destination deck slot (1-12)."},
                "dest_well": {"type": "string", "description": 'Destination well (e.g. "B3").'},
                "volume": {"type": "number", "description": "Volume in uL."},
                "pipette_number": {"type": "integer", "description": "1 (left) or 2 (right). Default: 1.", "default": 1},
                "tip_deck": {"type": "integer", "description": "Deck slot of the tip rack."},
                "tip_well": {"type": "string", "description": 'Tip to pick up. Default: "A1".', "default": "A1"},
                "aspirate_flow_rate": {
                    "type": ["number", "boolean"],
                    "description": "Flow rate in uL/s, true for max speed, or false to keep current. Use low values (e.g. 50) for viscous liquids.",
                    "default": True,
                },
                "dispense_flow_rate": {
                    "type": ["number", "boolean"],
                    "description": "Flow rate in uL/s, true for max speed, or false to keep current.",
                    "default": True,
                },
                "source_z_reference": {
                    "type": "string", "enum": ["top", "top_just", "bottom", "bottom_just"],
                    "description": "Z height at source well. Default: bottom.", "default": "bottom",
                },
                "dest_z_reference": {
                    "type": "string", "enum": ["top", "top_just", "bottom", "bottom_just"],
                    "description": "Z height at dest well. Default: bottom.", "default": "bottom",
                },
            },
            "required": ["source_deck", "source_well", "dest_deck", "dest_well", "volume", "tip_deck"],
        },
    ),
    Tool(
        name="distribute_liquid",
        description=(
            "Distribute from one source to multiple destination wells. "
            "New tip per transfer to prevent cross-contamination. "
            "All parameters must correspond to the confirmed physical setup."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "source_deck": {"type": "integer", "description": "Source deck slot (1-12)."},
                "source_well": {"type": "string", "description": 'Source well (e.g. "A1").'},
                "dest_deck": {"type": "integer", "description": "Destination deck slot (1-12)."},
                "dest_wells": {
                    "type": "array", "items": {"type": "string"},
                    "description": 'Destination wells (e.g. ["A1", "A2", "A3"]).',
                },
                "volume": {"type": "number", "description": "Volume per destination in uL."},
                "pipette_number": {"type": "integer", "description": "1 or 2. Default: 1.", "default": 1},
                "tip_deck": {"type": "integer", "description": "Deck slot of the tip rack."},
                "tip_well": {"type": "string", "description": 'Starting tip. Default: "A1".', "default": "A1"},
            },
            "required": ["source_deck", "source_well", "dest_deck", "dest_wells", "volume", "tip_deck"],
        },
    ),
    Tool(
        name="mix_liquid",
        description=(
            "Mix liquid in a well by repeated aspirate/dispense cycles. "
            "Common for resuspending pellets, mixing reagents, etc. "
            "All parameters must correspond to the confirmed physical setup."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "deck_number": {"type": "integer", "description": "Deck slot (1-12)."},
                "well": {"type": "string", "description": 'Well to mix (e.g. "A1").'},
                "volume": {"type": "number", "description": "Mix volume in uL."},
                "cycles": {"type": "integer", "description": "Number of mix cycles. Default: 3.", "default": 3},
                "pipette_number": {"type": "integer", "description": "1 or 2. Default: 1.", "default": 1},
                "tip_deck": {"type": "integer", "description": "Deck slot of the tip rack."},
                "tip_well": {"type": "string", "description": 'Tip to use. Default: "A1".', "default": "A1"},
                "flow_rate": {
                    "type": ["number", "boolean"],
                    "description": "Flow rate (uL/s) or true for default.",
                    "default": True,
                },
            },
            "required": ["deck_number", "well", "volume", "tip_deck"],
        },
    ),
    # --- Modules ---
    Tool(
        name="run_thermocycler",
        description=(
            "Run an ODTC thermocycler method. Close door first with control_odtc_door. "
            "This call BLOCKS until the method completes. "
            "Run pre-heat methods before PCR methods (e.g. PRE25 → PCR_Protocol_1)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "method_name": {"type": "string", "description": "Method name to run (from pre-methods or methods list)."},
            },
            "required": ["method_name"],
        },
    ),
    Tool(
        name="shake_plate",
        description="Shake a plate on the orbital shaker module.",
        inputSchema={
            "type": "object",
            "properties": {
                "rpm": {"type": "integer", "description": "Speed in RPM (100-3000)."},
                "duration_sec": {"type": "number", "description": "Duration in seconds."},
                "accel_sec": {"type": "integer", "description": "Accel time in seconds. Default: 5.", "default": 5},
            },
            "required": ["rpm", "duration_sec"],
        },
    ),
    Tool(
        name="control_odtc_door",
        description="Open or close the thermocycler (ODTC) door.",
        inputSchema={
            "type": "object",
            "properties": {
                "open": {"type": "boolean", "description": "true = open, false = close."},
            },
            "required": ["open"],
        },
    ),
    # --- Tip management ---
    Tool(
        name="reset_tip_tracking",
        description=(
            "Reset tip tracking for a specific deck slot after physically replacing "
            "a tip rack. Without this, the server thinks those tips are still used. "
            "Call this when the user confirms a fresh tip rack has been placed."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "deck_number": {
                    "type": "integer",
                    "description": "Deck slot (1-12) where tip rack was replaced.",
                },
            },
            "required": ["deck_number"],
        },
    ),
    # --- Diagnostics ---
    Tool(
        name="diagnose_error",
        description=(
            "Run comprehensive diagnostics on the robot system. "
            "Checks API connection, initialization state, pipette/deck config, "
            "tip availability, module status, and recent errors. "
            "Use this when something goes wrong to identify the root cause."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_error_log",
        description=(
            "Get recent error and warning logs from the MCP server. "
            "Useful for debugging failures or understanding what went wrong."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "Number of recent log entries to return. Default: 30.",
                    "default": 30,
                },
                "clear": {
                    "type": "boolean",
                    "description": "Clear the log after reading. Default: false.",
                    "default": False,
                },
            },
        },
    ),
    # --- Protocol ---
    Tool(
        name="save_protocol",
        description=(
            "Save a sequence of liquid handling steps as a named protocol for re-use. "
            "Each step is {tool, arguments}. Only action tools are allowed "
            "(transfer_liquid, distribute_liquid, mix_liquid, run_thermocycler, "
            "shake_plate, control_odtc_door). "
            "Use capture_setup=true to snapshot current pipette/deck config into the protocol. "
            "When a protocol has setup, run_protocol will auto-configure the robot before execution."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Protocol name (unique identifier)."},
                "description": {"type": "string", "description": "Human-readable description of what this protocol does."},
                "steps": {
                    "type": "array",
                    "description": 'List of steps. Each: {"tool": "transfer_liquid", "arguments": {...}}.',
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string"},
                            "arguments": {"type": "object"},
                        },
                        "required": ["tool", "arguments"],
                    },
                },
                "setup": {
                    "type": "object",
                    "description": (
                        "Explicit HW setup. Keys: pipette_config, deck_config, modules. "
                        "If provided, run_protocol auto-configures before executing steps."
                    ),
                    "properties": {
                        "pipette_config": {"type": "object", "description": 'e.g. {"1": "1ch_1000ul"}'},
                        "deck_config": {"type": "object", "description": 'e.g. {"1": "spl_96_well_plate_30096"}'},
                        "modules": {"type": "array", "items": {"type": "string"}, "description": 'e.g. ["odtc"]'},
                    },
                },
                "capture_setup": {
                    "type": "boolean",
                    "description": "If true, snapshot current pipette/deck config as setup. Overrides setup param.",
                    "default": False,
                },
            },
            "required": ["name", "description", "steps"],
        },
    ),
    Tool(
        name="list_protocols",
        description="List all saved protocols with their names, descriptions, and step counts.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_protocol",
        description="Get full details of a saved protocol including all steps.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Protocol name."},
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="run_protocol",
        description=(
            "Execute a saved protocol. Runs each step sequentially. "
            "Stops immediately on the first error to prevent further damage. "
            "If the protocol includes setup (pipette/deck config), the robot is "
            "auto-configured and initialized before execution. "
            "If no setup is stored, the robot must already be configured and initialized. "
            "IMPORTANT: Before running, confirm with the user that the physical "
            "robot state matches the protocol's setup. "
            "Use dry_run=true to validate all steps against the simulator first "
            "without touching the real robot."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Protocol name to execute."},
                "dry_run": {
                    "type": "boolean",
                    "description": (
                        "If true, execute against simulator only — validates all steps "
                        "without touching the real robot. Default: false."
                    ),
                    "default": False,
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="delete_protocol",
        description="Delete a saved protocol.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Protocol name to delete."},
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="get_protocol_run_history",
        description=(
            "Get the execution history for a saved protocol. "
            "Shows recent run results: status (ok/error), completed steps, duration, "
            "dry_run flag, and timestamps. "
            "Useful for checking whether a protocol has been successfully run recently."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Protocol name."},
                "limit": {
                    "type": "integer",
                    "description": "Number of recent runs to return (1-50). Default: 10.",
                    "default": 10,
                },
            },
            "required": ["name"],
        },
    ),
]


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

def create_server(client, state: ServerState) -> Server:
    """Create and configure the MCP server with all tools registered."""
    server = Server("notable-mcp", instructions=SERVER_INSTRUCTIONS)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            result = await _dispatch(client, state, name, arguments)
            return [TextContent(type="text", text=result)]
        except SafetyError as e:
            logger.warning(f"Safety blocked {name}: {e}")
            return [TextContent(type="text", text=json.dumps({"error": str(e)}))]
        except NotableAPIError as e:
            logger.error(f"API error in {name}: {e}")
            return [TextContent(type="text", text=json.dumps(
                {"error": f"Robot API error (HTTP {e.status_code}). Use diagnose_error for details."}
            ))]
        except Exception as e:
            # Log full traceback internally but return sanitized message
            logger.error(f"Error in {name}: {e}", exc_info=True)
            return [TextContent(type="text", text=json.dumps(
                {"error": f"Internal error in {name}. Check get_error_log for details."}
            ))]

    return server


async def _dispatch(client, state: ServerState, name: str, args: dict) -> str:
    """Route tool calls to their handler functions."""
    match name:
        # Status
        case "get_robot_status":
            return await status.get_robot_status(client, state)
        case "get_available_resources":
            return await status.get_available_resources(client, state)
        # Config
        case "configure_pipette":
            return await config.configure_pipette(client, state, **args)
        case "configure_deck":
            return await config.configure_deck(client, state, **args)
        # Control
        case "initialize_robot":
            return await control.initialize_robot(client, state, **args)
        case "emergency_stop":
            return await control.emergency_stop(client, state)
        case "pause_robot":
            return await control.pause_robot(client, state)
        case "resume_robot":
            return await control.resume_robot(client, state)
        # Liquid
        case "transfer_liquid":
            return await liquid.transfer_liquid(client, state, **args)
        case "distribute_liquid":
            return await liquid.distribute_liquid(client, state, **args)
        case "mix_liquid":
            return await liquid.mix_liquid(client, state, **args)
        # Modules
        case "run_thermocycler":
            return await modules.run_thermocycler(client, state, **args)
        case "shake_plate":
            return await modules.shake_plate(client, state, **args)
        case "control_odtc_door":
            return await modules.control_odtc_door(client, state, **args)
        # Tip management
        case "reset_tip_tracking":
            return await _reset_tip_tracking(state, **args)
        # Diagnostics
        case "diagnose_error":
            return await diagnostics.diagnose_error(client, state)
        case "get_error_log":
            return await diagnostics.get_error_log(client, state, **args)
        # Protocol
        case "save_protocol":
            return await protocol.save_protocol(client, state, **args)
        case "list_protocols":
            return await protocol.list_protocols(client, state)
        case "get_protocol":
            return await protocol.get_protocol(client, state, **args)
        case "delete_protocol":
            return await protocol.delete_protocol(client, state, **args)
        case "run_protocol":
            return await _run_protocol(client, state, **args)
        case "get_protocol_run_history":
            return await _get_protocol_run_history(state, **args)
        case _:
            raise ValueError(f"Unknown tool: {name}")


async def _reset_tip_tracking(state: ServerState, deck_number: int) -> str:
    """Reset tip tracking for a deck slot after tip rack replacement."""
    from .safety import validate_deck_number
    validate_deck_number(deck_number)
    previous_used = state.tips.used_count(deck_number)
    state.tips.reset_deck(deck_number)
    logger.info(f"Tip tracking reset for deck {deck_number} (was {previous_used} used)")
    return json.dumps(
        {"status": "ok", "deck_number": deck_number, "tips_cleared": previous_used},
        indent=2, ensure_ascii=False,
    )


async def _run_protocol(client, state: ServerState, name: str, dry_run: bool = False) -> str:
    """Execute a saved protocol step by step. Stops on first error.

    When dry_run=True, execution uses an isolated SimulatedClient + fresh
    ServerState so the real robot is never touched.
    """
    _log_name = state.protocols._sanitize_log_str(name)
    proto = state.protocols.get(name)
    if not proto:
        raise ValueError(f"Protocol '{_log_name}' not found.")

    # Re-validate steps at run time (defense in depth: protocol file
    # may have been manually edited after save)
    protocol._validate_steps(proto["steps"])

    start_time = time.monotonic()

    # dry_run: execute against an isolated SimulatedClient + fresh ServerState
    dry_warning = None
    if dry_run:
        exec_client = SimulatedClient()
        exec_state = ServerState(protocol_dir=None)
        if not proto.get("setup"):
            # No stored setup — bootstrap dry state from current robot config
            exec_state.update_pipette_config(dict(state.pipette_config))
            exec_state.update_deck_config(dict(state.deck_config))
            exec_state.set_initialized()
            dry_warning = "No setup stored in protocol — dry run used current robot config."
    else:
        exec_client = client
        exec_state = state

    def _record(status: str, completed: int, total: int, phase: str | None, error_step: int | None) -> None:
        state.protocols.record_run(name, {
            "status": status,
            "dry_run": dry_run,
            "completed_steps": completed,
            "total_steps": total,
            "phase": phase,
            "error_step": error_step,
            "duration_sec": round(time.monotonic() - start_time, 2),
        })

    setup = proto.get("setup")
    if setup:
        try:
            logger.info(f"Protocol '{_log_name}': applying {'dry run ' if dry_run else ''}setup")
            if "pipette_config" in setup:
                await _dispatch(exec_client, exec_state, "configure_pipette",
                                {"pipette_config": setup["pipette_config"]})
            if "deck_config" in setup:
                await _dispatch(exec_client, exec_state, "configure_deck",
                                {"deck_config": setup["deck_config"]})
            init_args: dict = {"home_axes": True, "move_to_ready": True}
            if "modules" in setup:
                init_args["modules"] = setup["modules"]
            await _dispatch(exec_client, exec_state, "initialize_robot", init_args)
        except Exception as e:
            logger.error(f"Protocol '{_log_name}' setup failed: {e}", exc_info=True)
            _record("error", 0, len(proto["steps"]), "setup", None)
            return json.dumps(
                {
                    "status": "error",
                    "protocol": name,
                    "dry_run": dry_run,
                    "phase": "setup",
                    "setup_error": "Setup failed. Check get_error_log for details.",
                    "completed_steps": 0,
                    "total_steps": len(proto["steps"]),
                },
                indent=2,
                ensure_ascii=False,
            )
    else:
        exec_state.require_initialized()

    steps = proto["steps"]
    results = []

    for i, step in enumerate(steps, 1):
        tool_name = step["tool"]
        tool_args = step["arguments"]
        try:
            result_str = await _dispatch(exec_client, exec_state, tool_name, tool_args)
            results.append({
                "step": i,
                "tool": tool_name,
                "status": "ok",
                "result": json.loads(result_str),
            })
        except Exception as e:
            logger.error(f"Protocol '{_log_name}' failed at step {i} ({tool_name}): {e}", exc_info=True)
            results.append({
                "step": i,
                "tool": tool_name,
                "status": "error",
                "error": f"Step {i} ({tool_name}) failed. Check get_error_log for details.",
            })
            _record("error", i - 1, len(steps), "execution", i)
            return json.dumps(
                {
                    "status": "error",
                    "protocol": name,
                    "dry_run": dry_run,
                    "phase": "execution",
                    "completed_steps": i - 1,
                    "total_steps": len(steps),
                    "results": results,
                },
                indent=2,
                ensure_ascii=False,
            )

    logger.info(f"Protocol '{_log_name}' {'dry run ' if dry_run else ''}completed ({len(steps)} steps)")
    _record("ok", len(steps), len(steps), None, None)

    response: dict = {
        "status": "ok",
        "protocol": name,
        "dry_run": dry_run,
        "completed_steps": len(steps),
        "total_steps": len(steps),
        "results": results,
    }
    if dry_run:
        response["message"] = "Dry run succeeded. All steps validated against simulator."
    if dry_warning:
        response["warning"] = dry_warning
    return json.dumps(response, indent=2, ensure_ascii=False)


async def _get_protocol_run_history(state: ServerState, name: str, limit: int = 10) -> str:
    """Return recent run history for a protocol."""
    runs = state.protocols.get_run_history(name, limit)
    return json.dumps(
        {"protocol": name, "runs": runs, "count": len(runs)},
        indent=2, ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="notable-mcp",
        description="MCP Server for NOTABLE liquid handler",
    )
    parser.add_argument("--host", default="localhost", help="Neon API host (default: localhost)")
    parser.add_argument("--port", type=int, default=7777, help="Neon API port (default: 7777)")
    parser.add_argument("--tls", action="store_true", help="Use HTTPS for Neon API connection")
    parser.add_argument(
        "--api-key", default=None,
        help="API key for Neon API authentication (or set NOTABLE_API_KEY env var)",
    )
    parser.add_argument("--simulate", action="store_true", help="Simulation mode (no robot needed)")
    parser.add_argument(
        "--protocol-dir",
        default=None,
        help="Directory for protocol storage (default: ~/.notable-mcp/protocols)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.simulate:
        client = SimulatedClient()
        logger.info("Starting in SIMULATION mode")
    else:
        scheme = "https" if args.tls else "http"
        base_url = f"{scheme}://{args.host}:{args.port}"
        api_key = args.api_key or os.environ.get("NOTABLE_API_KEY")
        if args.api_key:
            logger.warning(
                "API key passed via --api-key flag (visible in process list). "
                "Prefer NOTABLE_API_KEY environment variable for security."
            )
        client = NotableClient(base_url=base_url, api_key=api_key)
        logger.info(f"Connecting to {base_url}{' (TLS)' if args.tls else ''}{' (authenticated)' if api_key else ''}")

    protocol_dir = Path(args.protocol_dir) if args.protocol_dir else Path.home() / ".notable-mcp" / "protocols"
    state = ServerState(protocol_dir=protocol_dir)
    # Attach error log buffer to root logger so all WARNING+ records are captured
    logging.getLogger().addHandler(state.error_log)
    logger.info(f"Protocol storage: {protocol_dir}")
    server = create_server(client, state)

    async def run():
        try:
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())
        finally:
            await client.close()
            logger.info("Server shut down, client closed")

    asyncio.run(run())


if __name__ == "__main__":
    main()
