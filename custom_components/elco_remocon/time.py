"""Time entities for Elco Remocon-Net — writable weekly schedule blocks.

Each program (DHW, heating zones, buffer) gets SCHEDULE_MAX_BLOCKS blocks of
a start and end `time` entity. Writing any of them applies the same blocks to
the whole week via RemoconClient.set_time_prog_week (SetTimeProg, read-back
verified). The read-back sensors in sensor.py mirror the result.
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
    SCHEDULE_MAX_BLOCKS,
    TIME_PROG_DHW_ID,
    TIME_PROG_HEATING_IDS,
    RemoconClient,
    _int_or,
)
from .const import CONF_ZONE, DEFAULT_ZONE, DOMAIN
from .coordinator import ElcoRemoconCoordinator
from .identifiers import entity_unique_id

_LOGGER = logging.getLogger(__name__)

# (key prefix, translation key prefix, progId(s)) — writable programs only;
# the buffer program (progIds 4-6) is exposed read-only via the schedule
# sensor in sensor.py, because SetTimeProg rejects those progIds.
TIME_PROGRAMS: tuple[tuple[str, str, int | tuple[int, ...]], ...] = (
    ("heating_z1", "heating_z1", TIME_PROG_HEATING_IDS[0]),
    ("heating_z2", "heating_z2", TIME_PROG_HEATING_IDS[1]),
    ("heating_z3", "heating_z3", TIME_PROG_HEATING_IDS[2]),
    ("dhw", "dhw", TIME_PROG_DHW_ID),
)


def _prog_data(data: Any, prog: int | tuple[int, ...]) -> Any:
    progs = data.time_progs
    if isinstance(prog, tuple):
        for prog_id in prog:
            if prog_id in progs:
                return progs[prog_id]
        return None
    return progs.get(prog)


def _prog_id(prog: int | tuple[int, ...], data: Any) -> int | None:
    """Resolve the actual progId behind a (possibly candidate-tuple) entry."""
    progs = data.time_progs
    if isinstance(prog, tuple):
        for prog_id in prog:
            if prog_id in progs:
                return prog_id
        return None
    return prog if prog in progs else None


def _blocks_from_prog(prog_data: Any) -> list[tuple[int, int]]:
    """Extract comfort blocks (start, end minutes) from a program's plan.

    Uses Monday's slices (day 1); blocks are slices where temp == comfort
    index, paired with the next slice's start.
    """
    weekly = (prog_data.get("timeProg") or {}).get("weeklyPlan") or {}
    allowed = weekly.get("allowedTemp") or [0, 1]
    if 1 not in allowed:
        return []
    comfort_idx = allowed.index(1)
    slices = []
    for plan in weekly.get("plans") or []:
        if not isinstance(plan, dict):
            continue
        for plan_day in plan.get("days") or []:
            if not isinstance(plan_day, dict):
                continue
            try:
                if _int_or(plan_day.get("day"), -1) == 1:
                    raw = plan_day.get("slices")
                    slices = [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []
            except (TypeError, ValueError):
                continue
    blocks: list[tuple[int, int]] = []
    open_start: int | None = None
    for s in slices:
        if not isinstance(s, dict):
            continue
        try:
            from_m = _int_or(s.get("from"), 0)
            temp = _int_or(s.get("temp"), 0)
        except (TypeError, ValueError):
            continue
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
    """Set up Elco time entities."""
    coordinator: ElcoRemoconCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.client.is_flat_api:
        _LOGGER.info(
            "Skipping time entities for gateway %s — flat R2 write API required",
            entry.data["gateway_id"],
        )
        return
    gw_id = entry.data["gateway_id"]
    zone = entry.data.get(CONF_ZONE, DEFAULT_ZONE)

    entities: list[ElcoScheduleTime] = []
    for prefix, translation_prefix, prog in TIME_PROGRAMS:
        if _prog_data(coordinator.data, prog) is None:
            continue
        for block in range(1, SCHEDULE_MAX_BLOCKS + 1):
            entities.append(ElcoScheduleTime(
                coordinator, gw_id, zone, prefix, translation_prefix, prog, block, "start"
            ))
            entities.append(ElcoScheduleTime(
                coordinator, gw_id, zone, prefix, translation_prefix, prog, block, "end"
            ))
    async_add_entities(entities)


class ElcoScheduleTime(CoordinatorEntity[ElcoRemoconCoordinator], TimeEntity):
    """One writable start/end time of a weekly schedule block."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: ElcoRemoconCoordinator,
        gw_id: str,
        zone: str,
        prefix: str,
        translation_prefix: str,
        prog: int | tuple[int, ...],
        block: int,
        part: str,  # "start" | "end"
    ) -> None:
        super().__init__(coordinator)
        self._prog = prog
        self._block = block
        self._part = part
        key = f"schedule_{prefix}_block_{block}_{part}"
        self._attr_translation_key = f"schedule_{translation_prefix}_block_{block}_{part}"
        self._attr_unique_id = entity_unique_id(gw_id, zone, key)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, gw_id)},
            "name": "Remocon-Net Heat Pump",
            "manufacturer": "Elco",
            "model": "Aerotop SPK",
        }

    def _current_blocks(self) -> list[tuple[int, int]]:
        prog_data = _prog_data(self.coordinator.data, self._prog)
        if prog_data is None:
            return []
        blocks = _blocks_from_prog(prog_data)
        while len(blocks) < self._block:
            blocks.append((0, 0))  # placeholder, filtered on write
        return blocks

    @property
    def native_value(self) -> dt_time | None:
        """Current read-back value of this block's start/end."""
        blocks = self._current_blocks()
        start, end = blocks[self._block - 1]
        if start == 0 and end == 0:
            return None
        return _minutes_to_time(start if self._part == "start" else end)

    async def async_set_value(self, value: dt_time) -> None:
        """Write this block's start/end and apply all blocks to the week."""
        prog_id = _prog_id(self._prog, self.coordinator.data)
        if prog_id is None:
            raise ValueError("Time program is not available on this gateway")
        blocks = self._current_blocks()
        while len(blocks) < self._block:
            blocks.append((0, 0))
        minutes = value.hour * 60 + value.minute
        start, end = blocks[self._block - 1]
        if self._part == "start":
            start = minutes
        else:
            end = minutes
        blocks[self._block - 1] = (start, end)
        # Drop empty trailing placeholders.
        real_blocks = [(s, e) for s, e in blocks if not (s == 0 and e == 0)]
        if not real_blocks:
            raise ValueError("At least one complete time block is required")
        hhmm = [
            (RemoconClient._minutes_to_str(s), RemoconClient._minutes_to_str(e))
            for s, e in real_blocks
        ]
        await self.hass.async_add_executor_job(
            self.coordinator.client.set_time_prog_week, prog_id, hhmm
        )
        await self.coordinator.async_request_refresh()
