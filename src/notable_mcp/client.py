"""NOTABLE Neon REST API client.

Thin httpx wrapper over the REST API at localhost:7777.
No SDK dependency — all calls are plain HTTP.
"""

from __future__ import annotations

import httpx


class NotableAPIError(Exception):
    """Raised when the NOTABLE API returns an error."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"[{status_code}] {detail}")


class NotableClient:
    """Async HTTP client for the NOTABLE Neon REST API."""

    def __init__(
        self,
        base_url: str = "http://localhost:7777",
        timeout: float = 300,
        api_key: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        headers = {}
        if api_key:
            headers["X-API-Key"] = api_key
        self._client = httpx.AsyncClient(
            base_url=self.base_url, timeout=timeout, headers=headers,
        )

    async def close(self):
        await self._client.aclose()

    async def check_connection(self) -> dict:
        """Quick connectivity check against the API."""
        return await self.get("/action/robot/status")

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    async def _request(self, method: str, path: str, json: dict | None = None) -> dict:
        resp = await self._client.request(method, f"/api/v1{path}", json=json)
        if resp.status_code != 200:
            raise NotableAPIError(resp.status_code, resp.text)
        body = resp.json()
        if not body.get("success", True):
            raise NotableAPIError(
                body.get("status_code", resp.status_code),
                str(body.get("data", "Unknown error")),
            )
        return body.get("data")

    async def get(self, path: str) -> dict:
        return await self._request("GET", path)

    async def post(self, path: str, json: dict | None = None) -> dict:
        return await self._request("POST", path, json=json)

    # ------------------------------------------------------------------
    # Resource — library queries
    # ------------------------------------------------------------------

    async def get_pipette_library(self) -> dict:
        return await self.get("/resource/library/pipette")

    async def get_labware_library(self) -> dict:
        return await self.get("/resource/library/labware")

    async def get_module_library(self) -> dict:
        return await self.get("/resource/library/module")

    async def get_adapter_library(self) -> dict:
        return await self.get("/resource/library/adapter")

    # ------------------------------------------------------------------
    # Config — pipette & deck
    # ------------------------------------------------------------------

    async def get_pipette_config(self) -> dict:
        return await self.get("/config/pipette/")

    async def set_pipette_config(self, config: dict) -> dict:
        return await self.post("/config/pipette/", json=config)

    async def get_deck_config(self) -> dict:
        return await self.get("/config/deck/")

    async def set_deck_config(self, config: dict) -> dict:
        return await self.post("/config/deck/", json=config)

    # ------------------------------------------------------------------
    # Controller — upper module (pipette movements + liquid handling)
    # ------------------------------------------------------------------

    async def initialize(self, home_axes: bool = True, move_to_ready: bool = True) -> dict:
        return await self.post(
            "/controller/upper-module/initialize",
            json={"home_axes": home_axes, "move_to_ready": move_to_ready},
        )

    async def pick_up_tip(self, pipette_number: int, deck_number: int, well: str) -> dict:
        return await self.post(
            "/controller/upper-module/pick-up-tip",
            json={"pipette_number": pipette_number, "deck_number": deck_number, "well": well},
        )

    async def drop_tip(
        self,
        pipette_number: int,
        deck_number: int | None = None,
        well: str | None = None,
    ) -> dict:
        payload: dict = {"pipette_number": pipette_number}
        if deck_number is not None:
            payload["deck_number"] = deck_number
        if well is not None:
            payload["well"] = well
        return await self.post("/controller/upper-module/drop-tip", json=payload)

    async def move_to(
        self,
        pipette_number: int,
        deck_number: int,
        well: str,
        z_reference: str = "top_just",
        z_speed: int | bool = True,
    ) -> dict:
        return await self.post(
            "/controller/upper-module/move-to",
            json={
                "pipette_number": pipette_number,
                "deck_number": deck_number,
                "well": well,
                "z_reference": z_reference,
                "z_speed": z_speed,
            },
        )

    async def move_z(
        self,
        pipette_number: int,
        deck_number: int,
        z_reference: str = "bottom",
        z_speed: int | bool = True,
    ) -> dict:
        return await self.post(
            "/controller/upper-module/move-z",
            json={
                "pipette_number": pipette_number,
                "deck_number": deck_number,
                "z_reference": z_reference,
                "z_speed": z_speed,
            },
        )

    async def aspirate(
        self, pipette_number: int, volume: float, flow_rate: float | bool = True
    ) -> dict:
        return await self.post(
            "/controller/upper-module/aspirate",
            json={
                "pipette_number": pipette_number,
                "volume": volume,
                "flow_rate": flow_rate,
            },
        )

    async def dispense(
        self, pipette_number: int, volume: float, flow_rate: float | bool = True
    ) -> dict:
        return await self.post(
            "/controller/upper-module/dispense",
            json={
                "pipette_number": pipette_number,
                "volume": volume,
                "flow_rate": flow_rate,
            },
        )

    async def blow_out(self, pipette_number: int) -> dict:
        return await self.post(
            "/controller/upper-module/blow-out",
            json={"pipette_number": pipette_number},
        )

    async def ready_plunger(self, pipette_number: int) -> dict:
        return await self.post(
            "/controller/upper-module/ready-plunger",
            json={"pipette_number": pipette_number},
        )

    async def mix(
        self,
        pipette_number: int,
        cycle: int,
        volume: float,
        flow_rate: float | bool = True,
        delay: float = 0,
    ) -> dict:
        """Mix by repeated aspirate/dispense at current position."""
        return await self.post(
            "/controller/upper-module/mix",
            json={
                "pipette_number": pipette_number,
                "cycle": cycle,
                "volume": volume,
                "flow_rate": flow_rate,
                "delay": delay,
            },
        )

    # ------------------------------------------------------------------
    # Action — robot status / control
    # ------------------------------------------------------------------

    async def get_robot_status(self) -> dict:
        return await self.get("/action/robot/status")

    async def pause(self) -> dict:
        return await self.post("/action/robot/status/pause")

    async def resume(self) -> dict:
        return await self.post("/action/robot/status/resume")

    async def stop(self) -> dict:
        return await self.post("/action/robot/status/stop")

    # ------------------------------------------------------------------
    # Module — ODTC / Shaker
    # ------------------------------------------------------------------

    async def module_use(self, module_names: list[str]) -> dict:
        return await self.post("/module/use", json={"module_names": module_names})

    async def get_module_status(self) -> dict:
        return await self.get("/module/status")

    # ODTC
    async def odtc_initialize(self) -> dict:
        return await self.post("/module/odtc/initialize")

    async def odtc_door(self, open: bool) -> dict:
        return await self.post("/module/odtc/door", json={"open": open})

    async def odtc_start_method(self, method_name: str) -> dict:
        """Start ODTC method (blocking — waits for completion)."""
        return await self.post(
            "/module/odtc/start-method",
            json={"method_name": method_name},
        )

    async def odtc_stop_method(self) -> dict:
        return await self.post("/module/odtc/stop-method")

    async def odtc_get_temperature(self) -> dict:
        return await self.get("/module/odtc/temperature")

    async def odtc_get_overview(self) -> dict:
        """Get ODTC overview (running status, method, elapsed, temperature)."""
        return await self.get("/module/odtc/overview")

    async def odtc_get_pre_methods(self) -> dict:
        return await self.get("/module/odtc/pre-methods")

    async def odtc_get_methods(self) -> dict:
        return await self.get("/module/odtc/methods")

    # Shaker
    async def shaker_initialize(self) -> dict:
        return await self.post("/module/shaker/initialize")

    async def shaker_shake(
        self, rpm: int, duration_sec: float, accel_sec: int = 5
    ) -> dict:
        return await self.post(
            "/module/shaker/shake",
            json={"rpm": rpm, "duration_sec": duration_sec, "accel_sec": accel_sec},
        )
