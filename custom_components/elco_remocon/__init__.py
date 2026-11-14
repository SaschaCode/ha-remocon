"""Elco Remocon-Net integration for Home Assistant."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .api import (
    TIME_PROG_DHW_ID,
    TIME_PROG_HEATING_IDS,
    RemoconClient,
    RemoconDataError,
)
from .const import CONF_GATEWAY_ID, CONF_ZONE, DOMAIN
from .coordinator import ElcoRemoconCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.WATER_HEATER,
    Platform.SWITCH,
    Platform.TIME,
]

# program name -> progId (writable programs only; buffer progIds 4-6 are
# rejected by SetTimeProg and stay read-only via the schedule sensors)
SERVICE_PROGRAMS: dict[str, int] = {
    "heating_z1": TIME_PROG_HEATING_IDS[0],
    "heating_z2": TIME_PROG_HEATING_IDS[1],
    "heating_z3": TIME_PROG_HEATING_IDS[2],
    "dhw": TIME_PROG_DHW_ID,
}

_DAYS = ("sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday")

SET_TIME_PROGRAM_SCHEMA = vol.Schema({
    vol.Required("program"): vol.In(SERVICE_PROGRAMS),
    vol.Optional("day"): vol.In(_DAYS),
    vol.Optional("comfort_start"): cv.time,
    vol.Optional("comfort_end"): cv.time,
    vol.Optional("blocks"): vol.All(
        cv.ensure_list,
        [vol.All([cv.time, cv.time], vol.Length(min=2, max=2))],
        vol.Length(min=1, max=3),
    ),
})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Elco Remocon-Net from a config entry."""
    coordinator = ElcoRemoconCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def handle_set_time_program(call: ServiceCall) -> None:
        """Set a time program: single block (comfort_start/end), optionally
        for one day only, or multiple blocks applied to the whole week."""
        prog_id = SERVICE_PROGRAMS[call.data["program"]]
        day_name = call.data.get("day")
        day = _DAYS.index(day_name) if day_name else None
        blocks_field = call.data.get("blocks")

        if blocks_field:
            if day is not None:
                raise ValueError("day is not supported together with multiple blocks")
            blocks = [
                (b[0].strftime("%H:%M"), b[1].strftime("%H:%M")) for b in blocks_field
            ]

            def _write() -> None:
                coordinator.client.set_time_prog_week(prog_id, blocks)
        else:
            start = call.data.get("comfort_start")
            end = call.data.get("comfort_end")
            if start is None or end is None:
                raise ValueError("Provide comfort_start/comfort_end or blocks")
            hhmm = (start.strftime("%H:%M"), end.strftime("%H:%M"))

            def _write() -> None:
                if day is not None:
                    coordinator.client.set_time_prog_slice(prog_id, day, *hhmm)
                else:
                    coordinator.client.set_time_prog_week(prog_id, [hhmm])

        try:
            await hass.async_add_executor_job(_write)
        except RemoconDataError as err:
            raise ValueError(str(err)) from err
        await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN, "set_time_program", handle_set_time_program,
        schema=SET_TIME_PROGRAM_SCHEMA,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, "set_time_program")
    return unload_ok
