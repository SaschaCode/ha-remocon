"""Time entities for Elco Remocon-Net — writable weekly schedule blocks.

Each writable program (DHW, heating zones) gets ONE start and ONE end
`time` entity (block 1) — mirroring the Remocon web UI, which shows one
time window per day view. Writing applies that block to the whole week via
RemoconClient.set_time_prog_week (SetTimeProg, read-back verified).

Per-day schedules and additional blocks (2-3) are not separate entities to
avoid a long flat list; they are set through the `elco_remocon.set_time_program`
service (registered in __init__.py), which supports `day` and multiple blocks.
The read-back sensors in sensor.py mirror the result.
"""

from __future__ import annotations

import logging
from datetime import time as dt_time
from typing import Any

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import (
    TIME_PROG_DHW_ID,
    TIME_PROG_HEATING_IDS,
    RemoconClient,
    _int_or,
)
from .const import CONF_ZONE, DEFAULT_ZONE, DOMAIN
from .coordinator import ElcoRemoconCoordinator
from .identifiers import entity_unique_id

_LOGGER = logging.getLogger(__name__)

# (key prefix, translation key prefix, progId) — writable programs only;
# the buffer program (progIds 4-6) is exposed read-only via the schedule
# sensor in sensor.py, because SetTimeProg rejects those progIds.
TIME_PROGRAMS: tuple[tuple[str, str, int], ...] = (
    ("heating_z1", "heating_z1", TIME_PROG_HEATING_IDS[0]),
    ("heating_z2", "heating_z2", TIME_PROG_HEATING_IDS[1]),
    ("heating_z3", "heating_z3", TIME_PROG_HEATING_IDS[2]),
    ("dhw", "dhw", TIME_PROG_DHW_ID),
)


def _prog_data(data: Any, prog_id: int) -> Any:
    return data.time_progs.get(prog_id)


def _blocks_from_prog(prog_data: Any, day: int = 1) -> list[tuple[int, int]]:
    """Extract comfort blocks (start, end minutes) from a program's plan
    for one day (default Monday = JS day 1). Merges the day's slices across
    all plan entries (the gateway may split one day over several plans)."""
    weekly = (prog_data.get("timeProg") or {}).get("weeklyPlan") or {}
    allowed = weekly.get("allowedTemp") or [0, 1]
    if 1 not in allowed:
        return []
    comfort_idx = allowed.index(1)
    slices: list[dict[str, Any]] = []
    for plan in weekly.get("plans") or []:
        if not isinstance(plan, dict):
            continue
        for plan_day in plan.get("days") or []:
            if not isinstance(plan_day, dict):
                continue
            if _int_or(plan_day.get("day"), -1) == day:
                raw = plan_day.get("slices")
                if isinstance(raw, list):
                    slices.extend(s for s in raw if isinstance(s, dict))
    try:
        slices = sorted(slices, key=lambda s: _int_or(s.get("from"), 0))
    except (TypeError, ValueError):
        pass
    blocks: list[tuple[int, int]] = []
    open_start: int | None = None
    for s in slices:
        from_m = _int_or(s.get("from"), 0)
        temp = _int_or(s.get("temp"), 0)
        if temp == comfort_idx and open_start is None:
            open_start = from_m
        elif temp != comfort_idx and open_start is not None:
            blocks.append((open_start, from_m))
            open_start = None
    return blocks


def _minutes_to_time(minutes: int) -> dt_time:
    return dt_time(minutes // 60, minutes % 60)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Elco time entities — one start/end pair per program."""
    coordinator: ElcoRemoconCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.client.is_flat_api:
        _LOGGER.info(
            "Skipping time entities for gateway %s — flat R2 write API required",
            entry.data["gateway_id"],
        )
        return
    gw_id = entry.data["gateway_id"]
    zone = entry.data.get(CONF_ZONE, DEFAULT_ZONE)

    entities = [
        ElcoScheduleTime(coordinator, gw_id, zone, prefix, translation_prefix, prog, part)
        for prefix, translation_prefix, prog in TIME_PROGRAMS
        if _prog_data(coordinator.data, prog) is not None
        for part in ("start", "end")
    ]
    async_add_entities(entities)


class ElcoScheduleTime(CoordinatorEntity[ElcoRemoconCoordinator], TimeEntity):
    """Writable start/end of a program's first (block 1) comfort window."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: ElcoRemoconCoordinator,
        gw_id: str,
        zone: str,
        prefix: str,
        translation_prefix: str,
        prog_id: int,
        part: str,  # "start" | "end"
    ) -> None:
        super().__init__(coordinator)
        self._prog_id = prog_id
        self._part = part
        key = f"schedule_{prefix}_{part}"
        self._attr_translation_key = f"schedule_{translation_prefix}_{part}"
        self._attr_unique_id = entity_unique_id(gw_id, zone, key)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, gw_id)},
            "name": "Remocon-Net Heat Pump",
            "manufacturer": "Elco",
            "model": "Aerotop SPK",
        }

    @property
    def native_value(self) -> dt_time | None:
        """Current read-back value of block 1's start/end."""
        prog_data = _prog_data(self.coordinator.data, self._prog_id)
        if prog_data is None:
            return None
        blocks = _blocks_from_prog(prog_data)
        if not blocks:
            return None
        start, end = blocks[0]
        return _minutes_to_time(start if self._part == "start" else end)

    async def async_set_value(self, value: dt_time) -> None:
        """Write block 1's start/end and apply it to the whole week."""
        prog_data = _prog_data(self.coordinator.data, self._prog_id)
        if prog_data is None:
            raise ValueError("Time program is not available on this gateway")
        blocks = _blocks_from_prog(prog_data) or [(0, 0)]
        minutes = value.hour * 60 + value.minute
        start, end = blocks[0]
        if self._part == "start":
            start = minutes
        else:
            end = minutes
        hhmm = (RemoconClient._minutes_to_str(start), RemoconClient._minutes_to_str(end))
        await self.hass.async_add_executor_job(
            self.coordinator.client.set_time_prog_week, self._prog_id, [hhmm]
        )
        await self.coordinator.async_request_refresh()
