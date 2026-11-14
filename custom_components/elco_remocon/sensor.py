"""Sensor entities for Elco Remocon-Net."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfPressure, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import (
    TIME_PROG_BUFFER_CANDIDATE_IDS,
    TIME_PROG_DHW_ID,
    TIME_PROG_HEATING_IDS,
    RemoconData,
    _int_or,
)
from .const import CONF_ZONE, DEFAULT_ZONE, DOMAIN
from .coordinator import ElcoRemoconCoordinator
from .identifiers import entity_unique_id

_DAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


@dataclass(kw_only=True)
class ElcoSensorDescription(SensorEntityDescription):
    """Describe an Elco sensor entity."""

    key: str
    translation_key: str
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    native_unit_of_measurement: str | None = None
    entity_category: EntityCategory | None = None
    suggested_display_precision: int = 1
    value_fn: Callable[[RemoconData], StateType]
    exists_fn: Callable[[RemoconData], bool] = lambda _: True


SENSORS: tuple[ElcoSensorDescription, ...] = (
    ElcoSensorDescription(
        key="outside_temp",
        translation_key="outside_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.outside_temp,
    ),
    ElcoSensorDescription(
        key="desired_temp",
        translation_key="desired_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.desired_temp if d.desired_temp > 0 else None,
    ),
    ElcoSensorDescription(
        key="reduced_temp",
        translation_key="reduced_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.reduced_temp if d.reduced_temp > 0 else None,
    ),
    ElcoSensorDescription(
        key="flow_temp",
        translation_key="flow_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.flow_temperature,
        exists_fn=lambda d: d.flow_temperature is not None,
    ),
    ElcoSensorDescription(
        key="system_pressure",
        translation_key="system_pressure",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.BAR,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.system_pressure,
        exists_fn=lambda d: d.system_pressure is not None,
    ),
    ElcoSensorDescription(
        key="dhw_temp",
        translation_key="dhw_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.dhw_temp if d.dhw_temp > 0 else None,
        exists_fn=lambda d: d.dhw_available,
    ),
    ElcoSensorDescription(
        key="dhw_comfort_temp",
        translation_key="dhw_comfort_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.dhw_comfort_temp,
        exists_fn=lambda d: d.dhw_comfort_temp is not None,
    ),
    ElcoSensorDescription(
        key="dhw_reduced_temp",
        translation_key="dhw_reduced_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.dhw_reduced_temp,
        exists_fn=lambda d: d.dhw_reduced_temp is not None,
    ),
)


def _prog_day_slices(prog_data: Any, day: int) -> list[dict[str, Any]]:
    """Return the slices of one day across all plan entries of a program."""
    weekly = (prog_data.get("timeProg") or {}).get("weeklyPlan") or {}
    for plan in weekly.get("plans") or []:
        for plan_day in (plan.get("days") if isinstance(plan, dict) else None) or []:
            if not isinstance(plan_day, dict):
                continue
            try:
                if _int_or(plan_day.get("day"), -1) == day:
                    slices = plan_day.get("slices")
                    return [s for s in slices if isinstance(s, dict)] if isinstance(slices, list) else []
            except (TypeError, ValueError):
                continue
    return []


def _prog_week_map(prog_data: Any) -> dict[str, Any]:
    """Full 7-day map of a program: day name -> list of {from, temp} slices."""
    out: dict[str, Any] = {}
    for day in range(7):
        slices: list[dict[str, Any]] = []
        for s in _prog_day_slices(prog_data, day):
            from_m = _int_or(s.get("from"), 0)
            slices.append({"from": f"{from_m // 60:02d}:{from_m % 60:02d}", "temp": s.get("temp")})
        out[_DAY_NAMES[day]] = slices
    return out


def _prog_timeline(prog_data: Any, day: int) -> list[str]:
    """96-slot quarter-hour timeline for one day ('comfort'/'reduced')."""
    weekly = (prog_data.get("timeProg") or {}).get("weeklyPlan") or {}
    allowed = weekly.get("allowedTemp") or [0, 1]
    slices = _prog_day_slices(prog_data, day)
    try:
        slices = sorted(slices, key=lambda s: _int_or(s.get("from"), 0))
    except (TypeError, ValueError):
        pass
    slots: list[str] = []
    for quarter in range(96):
        minute = quarter * 15
        temp_idx = allowed.index(0) if 0 in allowed else 0
        for s in slices:
            if _int_or(s.get("from"), 0) <= minute:
                temp_idx = _int_or(s.get("temp"), 0)
        is_comfort = temp_idx == allowed.index(1) if 1 in allowed else False
        slots.append("comfort" if is_comfort else "reduced")
    return slots


SCHEDULE_PROGRAMS: tuple[tuple[str, str, int | tuple[int, ...], Any], ...] = (
    # (key, translation_key, progId(s), exists_fn on RemoconData)
    ("schedule_heating_z1", "schedule_heating_z1", TIME_PROG_HEATING_IDS[0],
     lambda d: TIME_PROG_HEATING_IDS[0] in d.time_progs),
    ("schedule_heating_z2", "schedule_heating_z2", TIME_PROG_HEATING_IDS[1],
     lambda d: TIME_PROG_HEATING_IDS[1] in d.time_progs),
    ("schedule_heating_z3", "schedule_heating_z3", TIME_PROG_HEATING_IDS[2],
     lambda d: TIME_PROG_HEATING_IDS[2] in d.time_progs),
    ("schedule_dhw", "schedule_dhw", TIME_PROG_DHW_ID,
     lambda d: TIME_PROG_DHW_ID in d.time_progs),
    ("schedule_buffer", "schedule_buffer", TIME_PROG_BUFFER_CANDIDATE_IDS,
     lambda d: any(p in d.time_progs for p in TIME_PROG_BUFFER_CANDIDATE_IDS)),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Elco sensors."""
    coordinator: ElcoRemoconCoordinator = hass.data[DOMAIN][entry.entry_id]
    gw_id = entry.data["gateway_id"]
    zone = entry.data.get(CONF_ZONE, DEFAULT_ZONE)

    entities = [
        ElcoSensor(coordinator, gw_id, zone, desc)
        for desc in SENSORS
        if desc.exists_fn(coordinator.data)
    ]
    # Weekly schedule read-back sensors (flat R2 gateways only).
    if coordinator.client.is_flat_api:
        entities.extend(
            ElcoScheduleSensor(coordinator, gw_id, zone, key, translation_key, prog)
            for key, translation_key, prog, exists in SCHEDULE_PROGRAMS
            if exists(coordinator.data)
        )
    async_add_entities(entities)


class ElcoScheduleSensor(CoordinatorEntity[ElcoRemoconCoordinator], SensorEntity):
    """Read-back of a weekly time program: today's slices as state, full week
    plus a 96-slot quarter-hour timeline as attributes."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: ElcoRemoconCoordinator,
        gw_id: str,
        zone: str,
        key: str,
        translation_key: str,
        prog: int | tuple[int, ...],
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_translation_key = translation_key
        self._attr_unique_id = entity_unique_id(gw_id, zone, key)
        self._prog = prog
        self._attr_device_info = {
            "identifiers": {(DOMAIN, gw_id)},
            "name": "Remocon-Net Heat Pump",
            "manufacturer": "Elco",
            "model": "Aerotop SPK",
        }

    def _prog_data(self) -> Any:
        progs = self.coordinator.data.time_progs
        if isinstance(self._prog, tuple):
            for prog_id in self._prog:
                if prog_id in progs:
                    return progs[prog_id]
            return None
        return progs.get(self._prog)

    @property
    def native_value(self) -> StateType:
        """Today's comfort blocks as HH:MM-HH:MM, comma separated."""
        prog_data = self._prog_data()
        if prog_data is None:
            return None
        today = datetime.now().weekday()  # Monday=0
        day = (today + 1) % 7  # JS Date.getDay(): Sunday=0
        weekly = (prog_data.get("timeProg") or {}).get("weeklyPlan") or {}
        allowed = weekly.get("allowedTemp") or [0, 1]
        comfort_idx = allowed.index(1) if 1 in allowed else None
        slices = _prog_day_slices(prog_data, day)
        blocks: list[str] = []
        open_start: int | None = None
        for s in slices:
            from_m = _int_or(s.get("from"), 0)
            temp = _int_or(s.get("temp"), 0)
            if comfort_idx is not None and temp == comfort_idx and open_start is None:
                open_start = from_m
            elif temp != comfort_idx and open_start is not None:
                blocks.append(
                    f"{open_start // 60:02d}:{open_start % 60:02d}-"
                    f"{from_m // 60:02d}:{from_m % 60:02d}"
                )
                open_start = None
        return ", ".join(blocks) if blocks else "reduced"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Full week map + today's quarter-hour timeline (timeline card data)."""
        prog_data = self._prog_data()
        if prog_data is None:
            return {}
        today = datetime.now().weekday()
        day = (today + 1) % 7
        return {
            "weekly": _prog_week_map(prog_data),
            "timeline": _prog_timeline(prog_data, day),
        }


class ElcoSensor(CoordinatorEntity[ElcoRemoconCoordinator], SensorEntity):
    """Elco sensor entity."""

    _attr_has_entity_name = True
    entity_description: ElcoSensorDescription

    def __init__(
        self,
        coordinator: ElcoRemoconCoordinator,
        gw_id: str,
        zone: str,
        description: ElcoSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = entity_unique_id(gw_id, zone, description.key)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, gw_id)},
            "name": "Remocon-Net Heat Pump",
            "manufacturer": "Elco",
            "model": "Aerotop SPK",
        }

    @property
    def native_value(self) -> StateType:
        """Return sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)
