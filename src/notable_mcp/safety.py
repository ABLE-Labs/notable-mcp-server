"""Safety guardrails for NOTABLE MCP tools.

Validates parameters before any robot command is executed.
Based on pipette.json specs from the NOTABLE Neon system.
"""

from __future__ import annotations

import re

# Pipette volume ranges (uL): {code: (min, max)}
PIPETTE_VOLUME_RANGE: dict[str, tuple[float, float]] = {
    "1ch_1000ul": (1, 1000),
    "1ch_200ul": (1, 200),
    "1ch_20ul": (0.5, 20),
    "8ch_1000ul": (1, 1000),
    "8ch_200ul": (1, 200),
    "8ch_20ul": (0.5, 20),
}

# Deck count (4 rows x 3 columns)
DECK_MIN = 1
DECK_MAX = 12

# Well pattern: A1-H12
_WELL_PATTERN = re.compile(r"^[A-H](1[0-2]|[1-9])$")

# Max tips in a 96-well tip rack
MAX_TIPS = 96

# Operational limits to prevent robot DoS
MAX_MIX_CYCLES = 100
MAX_SHAKE_DURATION_SEC = 3600  # 1 hour
MAX_DELAY_SEC = 3600  # 1 hour

# Flow rate limits (uL/s) — conservative bounds across all pipette types
MIN_FLOW_RATE = 0.5
MAX_FLOW_RATE = 3000.0

# Acceleration time limits (seconds)
MIN_ACCEL_SEC = 1
MAX_ACCEL_SEC = 30


class SafetyError(Exception):
    """Raised when a safety check fails."""


def validate_well(well: str) -> None:
    if not _WELL_PATTERN.match(well):
        raise SafetyError(
            f"Invalid well '{well}'. Must be A1-H12 (row A-H, column 1-12)."
        )


def validate_deck_number(deck_number: int) -> None:
    if not (DECK_MIN <= deck_number <= DECK_MAX):
        raise SafetyError(
            f"Deck number {deck_number} out of range. Must be {DECK_MIN}-{DECK_MAX}."
        )


def validate_volume(volume: float, pipette_code: str | None = None) -> None:
    if volume <= 0:
        raise SafetyError(f"Volume must be positive, got {volume}.")

    if pipette_code and pipette_code in PIPETTE_VOLUME_RANGE:
        lo, hi = PIPETTE_VOLUME_RANGE[pipette_code]
        if not (lo <= volume <= hi):
            raise SafetyError(
                f"Volume {volume}uL out of range for {pipette_code} "
                f"(valid: {lo}-{hi}uL)."
            )


def validate_pipette_number(pipette_number: int) -> None:
    if pipette_number not in (1, 2):
        raise SafetyError(
            f"Pipette number must be 1 (left) or 2 (right), got {pipette_number}."
        )


def validate_rpm(rpm: int) -> None:
    if not (100 <= rpm <= 3000):
        raise SafetyError(f"Shaker RPM {rpm} out of range (100-3000).")


def validate_pipette_code(code: str) -> None:
    if code not in PIPETTE_VOLUME_RANGE:
        raise SafetyError(
            f"Unknown pipette code '{code}'. "
            f"Valid: {list(PIPETTE_VOLUME_RANGE.keys())}"
        )


def validate_transfer_params(
    source_deck: int,
    source_well: str,
    dest_deck: int,
    dest_well: str,
    volume: float,
    pipette_number: int,
    pipette_code: str | None = None,
) -> None:
    """Validate all parameters for a transfer_liquid call."""
    validate_pipette_number(pipette_number)
    validate_deck_number(source_deck)
    validate_deck_number(dest_deck)
    validate_well(source_well)
    validate_well(dest_well)
    validate_volume(volume, pipette_code)


def validate_flow_rate(rate: float | bool) -> None:
    """Validate flow rate. True/False are accepted (API handles defaults)."""
    if isinstance(rate, bool):
        return
    if not (MIN_FLOW_RATE <= rate <= MAX_FLOW_RATE):
        raise SafetyError(
            f"Flow rate {rate} uL/s out of range ({MIN_FLOW_RATE}-{MAX_FLOW_RATE})."
        )


def validate_accel_sec(accel_sec: int) -> None:
    if not (MIN_ACCEL_SEC <= accel_sec <= MAX_ACCEL_SEC):
        raise SafetyError(
            f"Acceleration time {accel_sec}s out of range ({MIN_ACCEL_SEC}-{MAX_ACCEL_SEC}s)."
        )


def parse_well_range(well_spec: str) -> list[str]:
    """Parse well specification into list of wells.

    Formats:
    - Single: "A1" -> ["A1"]
    - Range: "A1:A6" -> ["A1", "A2", ..., "A6"]
    - Comma-separated: "A1,A2,A3" -> ["A1", "A2", "A3"]
    """
    rows = "ABCDEFGH"

    if "," in well_spec:
        wells = [w.strip() for w in well_spec.split(",")]
        for w in wells:
            validate_well(w)
        return wells

    if ":" in well_spec:
        parts = well_spec.split(":")
        if len(parts) != 2:
            raise SafetyError(f"Invalid well range '{well_spec}'. Use 'A1:A6' format.")
        start, end = parts[0].strip(), parts[1].strip()
        validate_well(start)
        validate_well(end)

        start_idx = rows.index(start[0]) * 12 + int(start[1:]) - 1
        end_idx = rows.index(end[0]) * 12 + int(end[1:]) - 1

        if start_idx > end_idx:
            raise SafetyError(f"Invalid well range '{well_spec}': start must be before end.")

        return [f"{rows[i // 12]}{i % 12 + 1}" for i in range(start_idx, end_idx + 1)]

    validate_well(well_spec)
    return [well_spec]


def validate_tip_sequence_length(start_well: str, count: int) -> None:
    """Ensure tip sequence does not exceed 96-well rack capacity."""
    rows = "ABCDEFGH"
    start_row = rows.index(start_well[0])
    start_col = int(start_well[1:]) - 1
    start_index = start_row * 12 + start_col
    if start_index + count > MAX_TIPS:
        available = MAX_TIPS - start_index
        raise SafetyError(
            f"Not enough tips: need {count} starting from {start_well}, "
            f"but only {available} positions remain in the rack. "
            "Use a new tip rack or start from an earlier position."
        )
