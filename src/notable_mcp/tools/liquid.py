"""Tools: transfer_liquid, distribute_liquid, mix_liquid"""

from __future__ import annotations

import json
import logging

from ..safety import SafetyError, validate_transfer_params, validate_well
from ..safety import validate_volume, validate_deck_number
from ..state import ServerState

logger = logging.getLogger("notable_mcp")


async def _do_single_transfer(
    client,
    state: ServerState,
    pipette_number: int,
    tip_deck: int,
    tip_well: str,
    source_deck: int,
    source_well: str,
    dest_deck: int,
    dest_well: str,
    volume: float,
    aspirate_flow_rate: float | bool = True,
    dispense_flow_rate: float | bool = True,
    source_z_reference: str = "bottom",
    dest_z_reference: str = "bottom",
    new_tip: bool = True,
    drop_tip_to_trash: bool = True,
) -> dict:
    """Execute a single transfer with tip safety recovery on failure."""
    tip_picked_up = False

    try:
        # 1. Pick up tip
        if new_tip:
            await client.pick_up_tip(
                pipette_number=pipette_number,
                deck_number=tip_deck,
                well=tip_well,
            )
            tip_picked_up = True
            state.tips.mark_used(tip_deck, tip_well)

        # 2. Move to source -> aspirate
        await client.move_to(
            pipette_number=pipette_number,
            deck_number=source_deck,
            well=source_well,
            z_reference=source_z_reference,
        )
        await client.aspirate(
            pipette_number=pipette_number,
            volume=volume,
            flow_rate=aspirate_flow_rate,
        )

        # 3. Move to destination -> dispense
        await client.move_to(
            pipette_number=pipette_number,
            deck_number=dest_deck,
            well=dest_well,
            z_reference=dest_z_reference,
        )
        await client.dispense(
            pipette_number=pipette_number,
            volume=volume,
            flow_rate=dispense_flow_rate,
        )

        # 4. Blow out -> move up -> ready plunger
        await client.blow_out(pipette_number=pipette_number)
        await client.move_z(
            pipette_number=pipette_number,
            deck_number=dest_deck,
            z_reference="top_just",
        )
        await client.ready_plunger(pipette_number=pipette_number)

        # 5. Drop tip
        if drop_tip_to_trash:
            await client.drop_tip(pipette_number=pipette_number)
        else:
            await client.drop_tip(
                pipette_number=pipette_number,
                deck_number=tip_deck,
                well=tip_well,
            )
        tip_picked_up = False

    except Exception:
        if tip_picked_up:
            logger.warning("Transfer failed with tip attached — attempting emergency tip drop")
            try:
                await client.drop_tip(pipette_number=pipette_number)
            except Exception as drop_err:
                logger.error(f"Emergency tip drop also failed: {drop_err}")
        raise

    return {
        "source": f"Deck{source_deck}:{source_well}",
        "dest": f"Deck{dest_deck}:{dest_well}",
        "volume": volume,
    }


def _validate_action_context(
    state: ServerState,
    pipette_number: int,
    deck_numbers: list[int],
) -> str | None:
    """Common validation for all liquid actions. Returns pipette_code."""
    state.require_initialized()
    state.require_pipette_mounted(pipette_number)
    for dn in deck_numbers:
        state.require_deck_configured(dn)
    return state.get_pipette_code(pipette_number)


async def transfer_liquid(
    client,
    state: ServerState,
    source_deck: int,
    source_well: str,
    dest_deck: int,
    dest_well: str,
    volume: float,
    pipette_number: int = 1,
    tip_deck: int | None = None,
    tip_well: str = "A1",
    aspirate_flow_rate: float | bool = True,
    dispense_flow_rate: float | bool = True,
    source_z_reference: str = "bottom",
    dest_z_reference: str = "bottom",
) -> str:
    """Transfer liquid from one well to another."""
    if tip_deck is None:
        raise SafetyError("tip_deck is required. Specify the deck slot where the tip rack is located.")

    validate_well(tip_well)
    pipette_code = _validate_action_context(
        state, pipette_number, [source_deck, dest_deck, tip_deck]
    )
    validate_transfer_params(
        source_deck=source_deck, source_well=source_well,
        dest_deck=dest_deck, dest_well=dest_well,
        volume=volume, pipette_number=pipette_number,
        pipette_code=pipette_code,
    )

    # Auto-advance tip if already used
    actual_tip = tip_well
    if state.tips.is_used(tip_deck, actual_tip):
        actual_tip = state.tips.next_available(tip_deck, actual_tip)
        logger.info(f"Tip auto-advanced to {actual_tip}")

    logger.info(
        f"transfer_liquid: Deck{source_deck}:{source_well} -> Deck{dest_deck}:{dest_well}, "
        f"{volume}uL, pipette={pipette_number}"
    )

    async with state.robot_action():
        result = await _do_single_transfer(
            client=client, state=state,
            pipette_number=pipette_number,
            tip_deck=tip_deck, tip_well=actual_tip,
            source_deck=source_deck, source_well=source_well,
            dest_deck=dest_deck, dest_well=dest_well,
            volume=volume,
            aspirate_flow_rate=aspirate_flow_rate,
            dispense_flow_rate=dispense_flow_rate,
            source_z_reference=source_z_reference,
            dest_z_reference=dest_z_reference,
        )

    return json.dumps({"status": "ok", "transfer": result}, indent=2, ensure_ascii=False)


async def distribute_liquid(
    client,
    state: ServerState,
    source_deck: int,
    source_well: str,
    dest_deck: int,
    dest_wells: list[str],
    volume: float,
    pipette_number: int = 1,
    tip_deck: int | None = None,
    tip_well: str = "A1",
) -> str:
    """Distribute liquid from one source to multiple destinations.

    Tip selection is handled by TipTracker — each transfer uses the next
    available tip automatically. No pre-generated sequence needed.
    """
    if tip_deck is None:
        raise SafetyError("tip_deck is required.")
    if not dest_wells:
        raise SafetyError("dest_wells must contain at least one well.")

    validate_well(tip_well)
    pipette_code = _validate_action_context(
        state, pipette_number, [source_deck, dest_deck, tip_deck]
    )
    for well in dest_wells:
        validate_transfer_params(
            source_deck=source_deck, source_well=source_well,
            dest_deck=dest_deck, dest_well=well,
            volume=volume, pipette_number=pipette_number,
            pipette_code=pipette_code,
        )

    # Pre-validate tip availability before starting any transfers
    available = state.tips.available_count(tip_deck, tip_well)
    if available < len(dest_wells):
        raise SafetyError(
            f"Not enough tips: need {len(dest_wells)} but only {available} "
            f"available on deck {tip_deck} from {tip_well}. Use a new tip rack."
        )

    logger.info(
        f"distribute_liquid: Deck{source_deck}:{source_well} -> Deck{dest_deck}:{dest_wells}, "
        f"{volume}uL x{len(dest_wells)}"
    )

    async with state.robot_action():
        transfers = []
        for dest_well in dest_wells:
            # Let TipTracker find next available tip each iteration
            next_tip = state.tips.next_available(tip_deck, tip_well)
            result = await _do_single_transfer(
                client=client, state=state,
                pipette_number=pipette_number,
                tip_deck=tip_deck, tip_well=next_tip,
                source_deck=source_deck, source_well=source_well,
                dest_deck=dest_deck, dest_well=dest_well,
                volume=volume,
            )
            transfers.append(result)

    return json.dumps(
        {"status": "ok", "transfers": transfers, "count": len(transfers)},
        indent=2, ensure_ascii=False,
    )


async def mix_liquid(
    client,
    state: ServerState,
    deck_number: int,
    well: str,
    volume: float,
    cycles: int = 3,
    pipette_number: int = 1,
    tip_deck: int | None = None,
    tip_well: str = "A1",
    flow_rate: float | bool = True,
) -> str:
    """Mix liquid in a well by repeated aspirate/dispense cycles."""
    if tip_deck is None:
        raise SafetyError("tip_deck is required.")
    if cycles < 1:
        raise SafetyError(f"cycles must be at least 1, got {cycles}.")

    validate_well(tip_well)
    validate_well(well)
    pipette_code = _validate_action_context(
        state, pipette_number, [deck_number, tip_deck]
    )
    validate_deck_number(deck_number)
    validate_volume(volume, pipette_code)

    # Auto-advance tip
    actual_tip = tip_well
    if state.tips.is_used(tip_deck, actual_tip):
        actual_tip = state.tips.next_available(tip_deck, actual_tip)

    logger.info(f"mix_liquid: Deck{deck_number}:{well}, {volume}uL x{cycles} cycles")

    async with state.robot_action():
        tip_picked_up = False
        try:
            await client.pick_up_tip(
                pipette_number=pipette_number,
                deck_number=tip_deck, well=actual_tip,
            )
            tip_picked_up = True
            state.tips.mark_used(tip_deck, actual_tip)

            await client.move_to(
                pipette_number=pipette_number,
                deck_number=deck_number, well=well,
                z_reference="bottom",
            )

            # Use native mix endpoint (aspirate/dispense cycles handled by firmware)
            await client.mix(
                pipette_number=pipette_number,
                cycle=cycles,
                volume=volume,
                flow_rate=flow_rate,
            )

            await client.blow_out(pipette_number=pipette_number)
            await client.move_z(
                pipette_number=pipette_number,
                deck_number=deck_number, z_reference="top_just",
            )
            await client.ready_plunger(pipette_number=pipette_number)
            await client.drop_tip(pipette_number=pipette_number)
            tip_picked_up = False

        except Exception:
            if tip_picked_up:
                logger.warning("Mix failed with tip attached — attempting emergency tip drop")
                try:
                    await client.drop_tip(pipette_number=pipette_number)
                except Exception as drop_err:
                    logger.error(f"Emergency tip drop also failed: {drop_err}")
            raise

    return json.dumps(
        {"status": "ok", "well": f"Deck{deck_number}:{well}", "volume": volume, "cycles": cycles},
        indent=2, ensure_ascii=False,
    )
