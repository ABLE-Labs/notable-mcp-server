"""Tools: save_protocol, list_protocols, get_protocol, delete_protocol

run_protocol execution is handled in server.py (needs access to _dispatch).
"""

from __future__ import annotations

import json
import logging

from ..safety import SafetyError
from ..state import ServerState

logger = logging.getLogger("notable_mcp")

# Tools that can appear in a protocol step
PROTOCOL_ALLOWED_TOOLS = frozenset({
    "transfer_liquid",
    "distribute_liquid",
    "mix_liquid",
    "run_thermocycler",
    "shake_plate",
    "control_odtc_door",
})


def _validate_steps(steps: list[dict]) -> None:
    """Validate protocol step format."""
    if not steps:
        raise SafetyError("프로토콜에 최소 1개 이상의 step이 필요합니다.")

    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise SafetyError(f"Step {i}: dict 형식이어야 합니다.")
        if "tool" not in step:
            raise SafetyError(f"Step {i}: 'tool' 필드가 없습니다.")
        if step["tool"] not in PROTOCOL_ALLOWED_TOOLS:
            raise SafetyError(
                f"Step {i}: '{step['tool']}'은 프로토콜에 사용할 수 없는 도구입니다. "
                f"허용: {sorted(PROTOCOL_ALLOWED_TOOLS)}"
            )
        if "arguments" not in step or not isinstance(step.get("arguments"), dict):
            raise SafetyError(f"Step {i}: 'arguments' dict 필드가 필요합니다.")


async def save_protocol(
    client,
    state: ServerState,
    name: str,
    description: str,
    steps: list[dict],
    setup: dict | None = None,
    capture_setup: bool = False,
) -> str:
    """Save a named protocol (sequence of tool calls).

    setup: explicit HW config (pipette_config, deck_config, modules).
    capture_setup: if true, snapshot current state as setup (overrides setup arg).
    """
    _validate_steps(steps)

    if capture_setup:
        setup = state.get_current_setup()
        logger.info(f"Captured current setup for protocol '{name}': {list(setup.keys())}")

    entry = state.protocols.save(name, description, steps, setup=setup)
    summary = {k: v for k, v in entry.items() if k != "steps"}
    logger.info(f"Protocol saved: '{name}' ({len(steps)} steps, setup={'yes' if setup else 'no'})")
    return json.dumps({"status": "ok", "protocol": summary}, indent=2, ensure_ascii=False)


async def list_protocols(client, state: ServerState) -> str:
    """List all saved protocols."""
    protocols = state.protocols.list_all()
    return json.dumps(
        {"protocols": protocols, "count": len(protocols)},
        indent=2,
        ensure_ascii=False,
    )


async def get_protocol(client, state: ServerState, name: str) -> str:
    """Get details of a saved protocol including all steps."""
    protocol = state.protocols.get(name)
    if not protocol:
        raise SafetyError(f"프로토콜 '{name}'을 찾을 수 없습니다.")
    return json.dumps(protocol, indent=2, ensure_ascii=False)


async def delete_protocol(client, state: ServerState, name: str) -> str:
    """Delete a saved protocol."""
    if not state.protocols.delete(name):
        raise SafetyError(f"프로토콜 '{name}'을 찾을 수 없습니다.")
    logger.info(f"Protocol deleted: '{name}'")
    return json.dumps({"status": "ok", "deleted": name}, indent=2, ensure_ascii=False)
