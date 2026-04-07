"""Server-level state management for NOTABLE MCP Server.

Tracks robot state (initialization, pipette config, deck config, tips, ODTC)
and enforces stateful safety constraints that individual validators cannot.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import json as _json
import logging
import re
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .safety import SafetyError

logger = logging.getLogger("notable_mcp")


class ConfigPersistence:
    """Save/load last used pipette+deck config for quick re-setup."""

    def __init__(self, storage_dir: Path):
        self._path = storage_dir / "last_config.json"

    def save(self, pipette_config: dict, deck_config: dict) -> None:
        data = {
            "pipette_config": {k: v for k, v in pipette_config.items() if v},
            "deck_config": {k: v for k, v in deck_config.items() if v},
            "saved_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            _json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8",
        )

    def load(self) -> dict | None:
        if not self._path.exists():
            return None
        try:
            return _json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return None


class VolumeTracker:
    """Track liquid volume per well. Warn when aspiration may exceed remaining."""

    def __init__(self):
        self._volumes: dict[tuple[int, str], float] = {}
        self._tracked: set[tuple[int, str]] = set()

    def record_dispense(self, deck: int, well: str, volume: float) -> None:
        key = (deck, well)
        self._tracked.add(key)
        self._volumes[key] = self._volumes.get(key, 0.0) + volume

    def record_aspirate(self, deck: int, well: str, volume: float) -> str | None:
        """Record aspiration. Returns warning if volume may be insufficient."""
        key = (deck, well)
        if key not in self._tracked:
            return None
        current = self._volumes.get(key, 0.0)
        self._volumes[key] = current - volume
        if self._volumes[key] < 0:
            return (
                f"Volume warning: Deck{deck}:{well} estimated at {current:.1f}uL "
                f"but aspirating {volume}uL (deficit: {-self._volumes[key]:.1f}uL)"
            )
        return None

    def set_volume(self, deck: int, well: str, volume: float) -> None:
        key = (deck, well)
        self._tracked.add(key)
        self._volumes[key] = volume

    def get_volume(self, deck: int, well: str) -> float | None:
        key = (deck, well)
        if key not in self._tracked:
            return None
        return self._volumes.get(key, 0.0)

    def reset(self) -> None:
        self._volumes.clear()
        self._tracked.clear()


class TipTracker:
    """Tracks tip usage per deck slot across calls with optional file persistence."""

    def __init__(self, storage_path: Path | None = None):
        self._used: dict[int, set[str]] = {}
        self._storage_path = storage_path
        if storage_path and storage_path.exists():
            self._load()

    def _save(self) -> None:
        if not self._storage_path:
            return
        data = {str(k): sorted(v) for k, v in self._used.items() if v}
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._storage_path.write_text(
            _json.dumps(data, indent=2), encoding="utf-8",
        )

    def _load(self) -> None:
        try:
            data = _json.loads(self._storage_path.read_text(encoding="utf-8"))
            self._used = {int(k): set(v) for k, v in data.items()}
            logger.info(f"Tip state loaded: {sum(len(v) for v in self._used.values())} used tips")
        except Exception as e:
            logger.warning(f"Failed to load tip state: {e}")

    def mark_used(self, deck_number: int, well: str) -> None:
        if deck_number not in self._used:
            self._used[deck_number] = set()
        self._used[deck_number].add(well)
        logger.debug(f"Tip used: Deck{deck_number}:{well}")
        self._save()

    def is_used(self, deck_number: int, well: str) -> bool:
        return well in self._used.get(deck_number, set())

    def next_available(self, deck_number: int, start_well: str = "A1") -> str:
        """Find next unused tip starting from start_well."""
        from .safety import validate_well
        validate_well(start_well)

        used = self._used.get(deck_number, set())
        rows = "ABCDEFGH"
        start_row = rows.index(start_well[0])
        start_col = int(start_well[1:]) - 1

        for i in range(start_row * 12 + start_col, 96):
            row_idx = i // 12
            col_idx = i % 12
            well = f"{rows[row_idx]}{col_idx + 1}"
            if well not in used:
                return well

        raise SafetyError(
            f"No tips remaining on deck {deck_number}. Replace the tip rack."
        )

    def used_count(self, deck_number: int) -> int:
        return len(self._used.get(deck_number, set()))

    def available_count(self, deck_number: int, start_well: str = "A1") -> int:
        """Count unused tips from start_well to end of rack."""
        from .safety import validate_well
        validate_well(start_well)

        used = self._used.get(deck_number, set())
        rows = "ABCDEFGH"
        start_row = rows.index(start_well[0])
        start_col = int(start_well[1:]) - 1
        count = 0
        for i in range(start_row * 12 + start_col, 96):
            row_idx = i // 12
            col_idx = i % 12
            well = f"{rows[row_idx]}{col_idx + 1}"
            if well not in used:
                count += 1
        return count

    def reset_deck(self, deck_number: int) -> None:
        self._used.pop(deck_number, None)
        self._save()

    def reset_all(self) -> None:
        self._used.clear()
        self._save()


class ErrorLogBuffer(logging.Handler):
    """In-memory ring buffer that captures WARNING+ log records."""

    def __init__(self, maxlen: int = 200):
        super().__init__(level=logging.WARNING)
        self.records: deque[dict] = deque(maxlen=maxlen)
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append({
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": self.format(record),
        })

    def get_recent(self, count: int = 30) -> list[dict]:
        return list(self.records)[-count:]

    def clear(self) -> None:
        self.records.clear()


class ProtocolStore:
    """Protocol storage with optional file persistence.

    If storage_dir is provided, protocols are saved as JSON files
    and loaded on startup. Otherwise, in-memory only.
    """

    # Characters unsafe for filenames on Windows/Linux/macOS
    _UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
    # Reserved device names on Windows (CON, PRN, AUX, NUL, COM1-9, LPT1-9)
    _WIN_RESERVED = frozenset({
        "con", "prn", "aux", "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    })

    # Storage limits
    MAX_PROTOCOLS = 100
    MAX_STEPS_PER_PROTOCOL = 500
    MAX_NAME_LENGTH = 128
    MAX_HISTORY_PER_PROTOCOL = 50

    # HMAC key for file integrity verification (detects accidental corruption)
    _INTEGRITY_KEY = b"notable-mcp-protocol-integrity-v1"

    def __init__(self, storage_dir: Path | None = None):
        self._protocols: dict[str, dict] = {}
        self._run_history: dict[str, deque[dict]] = {}
        self._storage_dir = storage_dir
        if storage_dir:
            storage_dir.mkdir(parents=True, exist_ok=True)
            self._load_all()

    @classmethod
    def _compute_checksum(cls, data: dict) -> str:
        """HMAC-SHA256 of protocol content (excluding _checksum field)."""
        content = {k: v for k, v in data.items() if k != "_checksum"}
        raw = _json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hmac.new(cls._INTEGRITY_KEY, raw, hashlib.sha256).hexdigest()

    @classmethod
    def _verify_checksum(cls, data: dict) -> bool:
        """Return True if checksum matches or is absent (legacy file)."""
        stored = data.get("_checksum")
        if stored is None:
            return True  # legacy file without checksum — accept but warn
        return hmac.compare_digest(stored, cls._compute_checksum(data))

    def _safe_filename(self, name: str) -> str:
        sanitized = self._UNSAFE_RE.sub("_", name).strip() or "_"
        # Prevent Windows reserved device names (e.g. CON.json would fail)
        if sanitized.split(".")[0].lower() in self._WIN_RESERVED:
            sanitized = f"_{sanitized}"
        return sanitized

    def _protocol_path(self, name: str) -> Path:
        return self._storage_dir / f"{self._safe_filename(name)}.json"

    def _load_all(self) -> None:
        """Load all .json files from storage_dir on startup."""
        count = 0
        for path in self._storage_dir.glob("*.json"):
            try:
                data = _json.loads(path.read_text(encoding="utf-8"))
                if "name" not in data or "steps" not in data:
                    continue
                if "_checksum" not in data:
                    logger.warning(
                        f"Protocol '{data['name']}' ({path.name}) has no integrity "
                        "checksum — re-saving to add one."
                    )
                elif not self._verify_checksum(data):
                    logger.warning(
                        f"Protocol '{data['name']}' failed integrity check "
                        f"({path.name}) — skipped. File may be corrupted or manually edited."
                    )
                    continue
                self._protocols[data["name"]] = data
                # Re-persist legacy files to add checksum
                if "_checksum" not in data:
                    self._persist(data["name"])
                count += 1
            except Exception as e:
                logger.warning(f"Failed to load protocol from {path.name}: {e}")
        if count:
            logger.info(f"Loaded {count} protocol(s) from {self._storage_dir}")

    def _persist(self, name: str) -> None:
        if not self._storage_dir:
            return
        proto = self._protocols.get(name)
        if proto:
            # Compute checksum before writing to disk
            proto["_checksum"] = self._compute_checksum(proto)
            path = self._protocol_path(name)
            path.write_text(
                _json.dumps(proto, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    def _remove_file(self, name: str) -> None:
        if not self._storage_dir:
            return
        path = self._protocol_path(name)
        path.unlink(missing_ok=True)

    @staticmethod
    def sanitize_log_str(s: str, max_len: int = 80) -> str:
        """Strip control characters and truncate for safe log inclusion."""
        clean = re.sub(r'[\x00-\x1f\x7f]', '', s)
        return clean[:max_len] + ("..." if len(clean) > max_len else "")

    def save(
        self, name: str, description: str, steps: list[dict],
        setup: dict | None = None,
    ) -> dict:
        if not name or not name.strip():
            raise SafetyError("Protocol name must not be empty.")
        if len(name) > self.MAX_NAME_LENGTH:
            raise SafetyError(
                f"Protocol name too long ({len(name)} chars, max {self.MAX_NAME_LENGTH})."
            )
        if len(steps) > self.MAX_STEPS_PER_PROTOCOL:
            raise SafetyError(
                f"Protocol has {len(steps)} steps (max {self.MAX_STEPS_PER_PROTOCOL})."
            )
        # Count limit only applies to new protocols (updating existing is ok)
        if name not in self._protocols and len(self._protocols) >= self.MAX_PROTOCOLS:
            raise SafetyError(
                f"Protocol storage full ({self.MAX_PROTOCOLS} protocols). "
                "Delete unused protocols first."
            )

        entry = {
            "name": name,
            "description": description,
            "steps": steps,
            "step_count": len(steps),
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        if setup:
            entry["setup"] = setup
        self._protocols[name] = entry
        self._persist(name)
        return entry

    def get(self, name: str) -> dict | None:
        proto = self._protocols.get(name)
        if proto is None:
            return None
        result = copy.deepcopy(proto)
        result.pop("_checksum", None)
        return result

    def list_all(self) -> list[dict]:
        return [
            {k: v for k, v in p.items() if k not in ("steps", "_checksum")}
            for p in self._protocols.values()
        ]

    def delete(self, name: str) -> bool:
        removed = self._protocols.pop(name, None) is not None
        if removed:
            self._remove_file(name)
            self._run_history.pop(name, None)
        return removed

    def record_run(self, name: str, entry: dict) -> None:
        """Append a run result to in-memory history (up to MAX_HISTORY_PER_PROTOCOL)."""
        full_entry = {
            "run_id": uuid.uuid4().hex[:8],
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            **entry,
        }
        if name not in self._run_history:
            self._run_history[name] = deque(maxlen=self.MAX_HISTORY_PER_PROTOCOL)
        self._run_history[name].append(full_entry)

    def get_run_history(self, name: str, limit: int = 10) -> list[dict]:
        """Return recent run entries for a protocol (most recent last).

        Raises ValueError if the protocol does not exist.
        """
        if name not in self._protocols:
            raise ValueError(f"Protocol '{name}' not found.")
        limit = max(1, min(limit, self.MAX_HISTORY_PER_PROTOCOL))
        return list(self._run_history.get(name, deque()))[-limit:]


class ServerState:
    """Tracks robot state across tool calls."""

    def __init__(self, protocol_dir: Path | None = None):
        self._storage_dir = Path.home() / ".notable-mcp"
        self._storage_dir.mkdir(parents=True, exist_ok=True)

        self.initialized: bool = False
        self.pipette_config: dict[str, str | None] = {"1": None, "2": None}
        self.deck_config: dict[str, Any] = {str(i): None for i in range(1, 13)}
        self._action_lock = asyncio.Lock()
        self.tips = TipTracker(storage_path=self._storage_dir / "tip_state.json")
        self.volumes = VolumeTracker()
        # ODTC state
        self.odtc_door_closed: bool = False
        self.odtc_running: bool = False
        # Resource cache for labware validation
        self._labware_codes: set[str] | None = None
        # Error log buffer
        self.error_log = ErrorLogBuffer()
        # Config persistence
        self._config_store = ConfigPersistence(self._storage_dir)
        # Protocol store (file-backed if protocol_dir provided)
        self.protocols = ProtocolStore(storage_dir=protocol_dir)

    # ------------------------------------------------------------------
    # Atomic action lock (Fix #1: TOCTOU race condition)
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def robot_action(self):
        """Acquire robot action lock atomically. Rejects if busy.

        Combines check + acquire in one step — no TOCTOU gap.
        In asyncio single-thread model, .locked() + __aenter__
        execute in the same synchronous turn with no yield between.
        """
        if self._action_lock.locked():
            raise SafetyError(
                "Robot is busy executing another command. "
                "Wait for the current operation to complete."
            )
        async with self._action_lock:
            yield

    # ------------------------------------------------------------------
    # State-aware guards
    # ------------------------------------------------------------------

    def require_initialized(self) -> None:
        if not self.initialized:
            raise SafetyError(
                "Robot is not initialized. Call initialize_robot first."
            )

    def require_pipette_mounted(self, pipette_number: int) -> None:
        code = self.get_pipette_code(pipette_number)
        if not code:
            raise SafetyError(
                f"No pipette mounted on slot {pipette_number} "
                f"({'left' if pipette_number == 1 else 'right'}). "
                "Call configure_pipette first."
            )

    def require_deck_configured(self, deck_number: int) -> None:
        config = self.deck_config.get(str(deck_number))
        if not config:
            raise SafetyError(
                f"Deck slot {deck_number} is empty (no labware assigned). "
                "Call configure_deck first to assign labware to this slot."
            )

    def require_odtc_door_closed(self) -> None:
        if not self.odtc_door_closed:
            raise SafetyError(
                "ODTC door is open. Close it first with control_odtc_door(open=false)."
            )

    def require_odtc_not_running(self) -> None:
        if self.odtc_running:
            raise SafetyError(
                "ODTC is running a method. Wait for completion or call stop first."
            )

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def get_current_setup(self) -> dict:
        """Snapshot current pipette/deck config for protocol storage."""
        setup: dict = {}
        pipettes = {k: v for k, v in self.pipette_config.items() if v}
        if pipettes:
            setup["pipette_config"] = pipettes
        decks = {k: v for k, v in self.deck_config.items() if v}
        if decks:
            setup["deck_config"] = decks
        return setup

    def get_pipette_code(self, pipette_number: int) -> str | None:
        return self.pipette_config.get(str(pipette_number))

    def is_labware_code_valid(self, code: str) -> bool:
        """Check if labware code is in the cached resource library."""
        if self._labware_codes is None:
            return True  # no cache yet, skip validation
        return code in self._labware_codes

    # ------------------------------------------------------------------
    # State mutations
    # ------------------------------------------------------------------

    def set_initialized(self) -> None:
        self.initialized = True
        logger.info("Robot initialized")

    def save_config(self) -> None:
        """Auto-save current config for quick re-setup."""
        self._config_store.save(self.pipette_config, self.deck_config)

    def load_last_config(self) -> dict | None:
        """Load last saved config."""
        return self._config_store.load()

    def update_pipette_config(self, config: dict[str, str | None]) -> None:
        for slot, code in config.items():
            self.pipette_config[slot] = code
        logger.info(f"Pipette config updated: {self.pipette_config}")
        self.save_config()

    def update_deck_config(self, config: dict[str, Any]) -> None:
        for slot, value in config.items():
            self.deck_config[slot] = value
        logger.info(f"Deck config updated (slots: {[s for s, v in config.items() if v]})")
        self.save_config()

    def cache_labware_codes(self, labware_library: dict) -> None:
        """Cache all labware codes from the resource library for validation."""
        codes: set[str] = set()
        for category_or_code, value in labware_library.items():
            if isinstance(value, dict):
                # Nested: {"tip_rack": {"ablelabs_tip_box_1000": {...}, ...}, ...}
                codes.update(value.keys())
            else:
                # Flat: {"ablelabs_tip_box_1000": {...}, ...}
                codes.add(category_or_code)
        self._labware_codes = codes
        logger.info(f"Labware cache loaded: {len(codes)} codes")

    def set_odtc_door(self, closed: bool) -> None:
        self.odtc_door_closed = closed
        logger.info(f"ODTC door {'closed' if closed else 'opened'}")

    def set_odtc_running(self, running: bool) -> None:
        self.odtc_running = running

    def reset(self) -> None:
        """Reset state after emergency stop."""
        self.initialized = False
        self.odtc_running = False
        self.odtc_door_closed = False  # conservative: treat as unknown/open
        self.tips.reset_all()
        self.volumes.reset()
        logger.warning("State reset after emergency stop")
