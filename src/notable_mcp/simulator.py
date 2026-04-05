"""Simulation mode for NOTABLE MCP Server.

Provides a fake API client that responds with realistic data
without requiring a physical robot or Neon server.
"""

from __future__ import annotations

import copy


# Default resource libraries for simulation
_PIPETTE_LIBRARY = {
    "1ch_1000ul": {"channel": 1, "volume": 1000},
    "1ch_200ul": {"channel": 1, "volume": 200},
    "1ch_20ul": {"channel": 1, "volume": 20},
    "8ch_1000ul": {"channel": 8, "volume": 1000},
    "8ch_200ul": {"channel": 8, "volume": 200},
    "8ch_20ul": {"channel": 8, "volume": 20},
}

_MODULE_LIBRARY = {
    "odtc": {"description": "On-Deck Thermocycler"},
    "shaker": {"description": "Orbital Shaker"},
}

_ADAPTER_LIBRARY = {
    "qinstruments_20x6ml_vial_2016-1074": {"description": "20x6ml vial adapter"},
}

_LABWARE_LIBRARY = {
    "tip_rack": {
        "ablelabs_tip_box_1000": {"volume": 1000, "rows": 8, "columns": 12},
        "ablelabs_tip_box_200": {"volume": 200, "rows": 8, "columns": 12},
        "ablelabs_tip_box_20": {"volume": 20, "rows": 8, "columns": 12},
    },
    "well_plate": {
        "spl_96_well_plate_30096": {"rows": 8, "columns": 12, "volume": 300},
    },
    "reservoir": {
        "ablelabs_reservoir_1ch": {"rows": 1, "columns": 1, "volume": 195000},
    },
    "trash": {
        "ablelabs_trash": {"rows": 1, "columns": 1},
    },
}


class SimulatorState:
    """Tracks simulated robot state."""

    def __init__(self):
        self.initialized = False
        self.pipette_config: dict = {"1": None, "2": None}
        self.deck_config: dict = {str(i): None for i in range(1, 13)}
        self.tip_attached: dict[int, bool] = {1: False, 2: False}
        self.position: dict[int, dict] = {
            1: {"deck": None, "well": None},
            2: {"deck": None, "well": None},
        }
        self.odtc_door_open: bool = True
        self.odtc_running: bool = False
        self.modules_connected: list[str] = []


class SimulatedClient:
    """Drop-in replacement for NotableClient that simulates responses."""

    def __init__(self):
        self._state = SimulatorState()

    async def close(self):
        pass

    async def check_connection(self) -> dict:
        return {"status": "ok", "simulated": True}

    # ------------------------------------------------------------------
    # Resource
    # ------------------------------------------------------------------

    async def get_pipette_library(self) -> dict:
        return copy.deepcopy(_PIPETTE_LIBRARY)

    async def get_labware_library(self) -> dict:
        return copy.deepcopy(_LABWARE_LIBRARY)

    async def get_module_library(self) -> dict:
        return copy.deepcopy(_MODULE_LIBRARY)

    async def get_adapter_library(self) -> dict:
        return copy.deepcopy(_ADAPTER_LIBRARY)

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    async def get_pipette_config(self) -> dict:
        return copy.deepcopy(self._state.pipette_config)

    async def set_pipette_config(self, config: dict) -> dict:
        self._state.pipette_config.update(config)
        return copy.deepcopy(self._state.pipette_config)

    async def get_deck_config(self) -> dict:
        return copy.deepcopy(self._state.deck_config)

    async def set_deck_config(self, config: dict) -> dict:
        self._state.deck_config.update(config)
        return copy.deepcopy(self._state.deck_config)

    # ------------------------------------------------------------------
    # Controller
    # ------------------------------------------------------------------

    async def initialize(self, home_axes: bool = True, move_to_ready: bool = True) -> dict:
        self._state.initialized = True
        return {"status": "ok", "simulated": True}

    async def pick_up_tip(self, pipette_number: int, deck_number: int, well: str) -> dict:
        self._state.tip_attached[pipette_number] = True
        self._state.position[pipette_number] = {"deck": deck_number, "well": well}
        return {"status": "ok", "simulated": True}

    async def drop_tip(
        self,
        pipette_number: int,
        deck_number: int | None = None,
        well: str | None = None,
    ) -> dict:
        self._state.tip_attached[pipette_number] = False
        return {"status": "ok", "simulated": True}

    async def move_to(
        self,
        pipette_number: int,
        deck_number: int,
        well: str,
        z_reference: str = "top_just",
        z_speed: int | bool = True,
    ) -> dict:
        self._state.position[pipette_number] = {"deck": deck_number, "well": well}
        return {"status": "ok", "simulated": True}

    async def move_z(
        self,
        pipette_number: int,
        deck_number: int,
        z_reference: str = "bottom",
        z_speed: int | bool = True,
    ) -> dict:
        return {"status": "ok", "simulated": True}

    async def aspirate(
        self, pipette_number: int, volume: float, flow_rate: float | bool = True
    ) -> dict:
        return {"status": "ok", "volume": volume, "simulated": True}

    async def dispense(
        self, pipette_number: int, volume: float, flow_rate: float | bool = True
    ) -> dict:
        return {"status": "ok", "volume": volume, "simulated": True}

    async def blow_out(self, pipette_number: int) -> dict:
        return {"status": "ok", "simulated": True}

    async def ready_plunger(self, pipette_number: int) -> dict:
        return {"status": "ok", "simulated": True}

    async def mix(
        self,
        pipette_number: int,
        cycle: int,
        volume: float,
        flow_rate: float | bool = True,
        delay: float = 0,
    ) -> dict:
        return {"status": "ok", "cycle": cycle, "volume": volume, "simulated": True}

    # ------------------------------------------------------------------
    # Action
    # ------------------------------------------------------------------

    async def get_robot_status(self) -> dict:
        return {
            "initialized": self._state.initialized,
            "pipette_config": self._state.pipette_config,
            "tip_attached": self._state.tip_attached,
            "simulated": True,
        }

    async def pause(self) -> dict:
        return {"status": "paused", "simulated": True}

    async def resume(self) -> dict:
        return {"status": "running", "simulated": True}

    async def stop(self) -> dict:
        return {"status": "stopped", "simulated": True}

    # ------------------------------------------------------------------
    # Module
    # ------------------------------------------------------------------

    async def module_use(self, module_names: list[str]) -> dict:
        self._state.modules_connected = list(set(self._state.modules_connected + module_names))
        return {"status": "ok", "modules": self._state.modules_connected, "simulated": True}

    async def get_module_status(self) -> dict:
        return {
            "modules": self._state.modules_connected,
            "odtc_door_open": self._state.odtc_door_open,
            "simulated": True,
        }

    async def odtc_initialize(self) -> dict:
        return {"status": "ok", "simulated": True}

    async def odtc_door(self, open: bool) -> dict:
        self._state.odtc_door_open = open
        return {"status": "ok", "open": open, "simulated": True}

    async def odtc_start_method(self, method_name: str) -> dict:
        """Simulate blocking method execution (completes immediately)."""
        self._state.odtc_running = True
        # In real API, this blocks until method completes
        self._state.odtc_running = False
        return {"status": "ok", "method": method_name, "simulated": True}

    async def odtc_stop_method(self) -> dict:
        self._state.odtc_running = False
        return {"status": "ok", "simulated": True}

    async def odtc_get_temperature(self) -> dict:
        return {"mount": 25.0, "lid": 25.0, "simulated": True}

    async def odtc_get_overview(self) -> dict:
        return {
            "is_running": self._state.odtc_running,
            "state": "Idle" if not self._state.odtc_running else "Busy",
            "method_name": None,
            "elapsed_sec": None,
            "temperature": {"mount": 25.0, "lid": 25.0},
            "simulated": True,
        }

    async def odtc_get_pre_methods(self) -> dict:
        return {"methods": ["init_test"], "simulated": True}

    async def odtc_get_methods(self) -> dict:
        return {"methods": ["method_test"], "simulated": True}

    async def shaker_initialize(self) -> dict:
        return {"status": "ok", "simulated": True}

    async def shaker_shake(
        self, rpm: int, duration_sec: float, accel_sec: int = 5
    ) -> dict:
        return {
            "status": "ok",
            "rpm": rpm,
            "duration_sec": duration_sec,
            "simulated": True,
        }
