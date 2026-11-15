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
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfPressure,
    UnitOfTemperature,
)
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
    iter_plan_days,
)
from .const import CONF_ZONE, DEFAULT_ZONE, DOMAIN
from .coordinator import ElcoRemoconCoordinator
from .identifiers import entity_unique_id

_DAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")

# Heat pump operating mode (menuItem 340, node T_13.9.0), live-verified on the
# Aerotop MONO 15.2 ODU gateway 2026-11-05. Values map to stable state keys.
HP_MODE_MAP = {
    0: "off",
    1: "standby",
    2: "cooling",
    3: "heating",
    4: "heating_booster",
    5: "cooling_booster",
    6: "rating_heating",
    7: "rating_cooling",
    8: "frost_protection",
    9: "defrost",
    10: "overheat_protection",
    11: "compressor_delay",
    12: "system_fault",
    13: "severe_system_fault",
    14: "refrigerant_pump_down",
    15: "emergency",
    16: "fan_test",
    17: "defrost",
    18: "cascade_heating",
    19: "cascade_cooling",
}


@dataclass(kw_only=True)
class ElcoSensorDescription(SensorEntityDescription):
    """Describe an Elco sensor entity."""

    key: str
    translation_key: str
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    native_unit_of_measurement: str | None = None
    entity_category: EntityCategory | None = None
    suggested_display_precision: int | None = 1
    icon: str | None = None
    options: list[str] | None = None  # for ENUM device class
    # When True and the gateway uses the flat R2 API, the entity is always
    # created at setup (the source item is live-verified on that family), even
    # if the very first poll could not fetch it yet; the state then shows
    # unknown until the first successful menuItems read instead of the entity
    # never appearing.
    create_on_flat: bool = False
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
        # Exists when the gateway reports ANY flow value: the measured temp
        # (HeatingFlowTemp) or, on gateways without it, the setpoint as a
        # fallback so the historical entity keeps showing something useful.
        exists_fn=lambda d: d.flow_temperature is not None
        or d.flow_setpoint_temperature is not None,
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
    # Diagnostics (flat R2 gateways; no entity when the item is missing) ---
    ElcoSensorDescription(
        key="flow_setpoint_temp",
        translation_key="flow_setpoint_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.flow_setpoint_temperature,
        exists_fn=lambda d: d.flow_setpoint_temperature is not None,
    ),
    ElcoSensorDescription(
        key="hp_outside_temp",
        translation_key="hp_outside_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        create_on_flat=True,  # menuItem verified live on flat R2 gateways
        value_fn=lambda d: d.hp_outside_temp,
        exists_fn=lambda d: d.hp_outside_temp is not None,
    ),
    ElcoSensorDescription(
        key="return_temp",
        translation_key="return_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.return_temperature,
        exists_fn=lambda d: d.return_temperature is not None,
    ),
    ElcoSensorDescription(
        key="room_temp",
        translation_key="room_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        # The item exists but reads 0.0 when no room sensor is connected;
        # report unknown in that case instead of a bogus 0 °C.
        value_fn=lambda d: d.room_temp if d.has_room_sensor and d.room_temp > 0 else None,
        exists_fn=lambda d: d.has_room_sensor,
    ),
    ElcoSensorDescription(
        key="power",
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda d: d.power_w,
        exists_fn=lambda d: d.power_w is not None,
    ),
    ElcoSensorDescription(
        key="compressor_freq",
        translation_key="compressor_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.compressor_freq_hz,
        exists_fn=lambda d: d.compressor_freq_hz is not None,
    ),
    ElcoSensorDescription(
        key="hp_water_temp",
        translation_key="hp_water_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        create_on_flat=True,  # menuItem verified live on flat R2 gateways
        value_fn=lambda d: d.hp_water_temperature,
        exists_fn=lambda d: d.hp_water_temperature is not None,
    ),
    ElcoSensorDescription(
        key="hp_mode",
        translation_key="hp_mode",
        icon="mdi:heat-pump-outline",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        create_on_flat=True,  # menuItem 340 verified live on flat R2 gateways
        # Enum sensor: HA treats the value as text (options below), never numeric.
        options=list(dict.fromkeys(HP_MODE_MAP.values())),
        value_fn=lambda d: (
            HP_MODE_MAP.get(d.hp_mode, str(d.hp_mode)) if d.hp_mode is not None else None
        ),
        exists_fn=lambda d: d.hp_mode is not None,
    ),
    ElcoSensorDescription(
        key="heating_curve_offset",
        translation_key="heating_curve_offset",
        native_unit_of_measurement="K",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.heating_curve_offset,
        exists_fn=lambda d: d.heating_curve_offset is not None,
    ),
    # EM20 metering. The cloud reports per-bucket kWh (last 48 h of 2 h
    # buckets); the value here is the sum over that window. For a monotonic
    # lifetime counter use the recorder statistics / Energy dashboard, which
    # HA derives from TOTAL_INCREASING state changes is NOT possible here —
    # instead these are windowed totals, so state_class stays None and the
    # energy dashboard is fed via the consumption report statistics instead.
    ElcoSensorDescription(
        key="energy_consumed_ch",
        translation_key="energy_consumed_ch",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.energy_consumed_ch_kwh,
        exists_fn=lambda d: d.energy_consumed_ch_kwh is not None,
    ),
    ElcoSensorDescription(
        key="energy_consumed_dhw",
        translation_key="energy_consumed_dhw",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.energy_consumed_dhw_kwh,
        exists_fn=lambda d: d.energy_consumed_dhw_kwh is not None,
    ),
    ElcoSensorDescription(
        key="energy_consumed_total",
        translation_key="energy_consumed_total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda d: d.energy_consumed_total_kwh,
        exists_fn=lambda d: d.energy_consumed_total_kwh is not None,
    ),
)


def _prog_day_slices(prog_data: Any, day: int) -> list[dict[str, Any]]:
    """Return the slices of one day across all plan entries of a program.

    Tolerates both gateway shapes: per-day dicts (``days: [{day, slices}]``)
    and flat plan-level entries (``days: [N,...], slices: [...]``).
    """
    weekly = (prog_data.get("timeProg") or {}).get("weeklyPlan") or {}
    merged: list[dict[str, Any]] = []
    for plan in weekly.get("plans") or []:
        for day_num, slices in iter_plan_days(plan):
            if day_num == day:
                merged.extend(s for s in slices if isinstance(s, dict))
    return merged


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

    flat = coordinator.client.is_flat_api
    entities = [
        ElcoSensor(coordinator, gw_id, zone, desc)
        for desc in SENSORS
        if desc.exists_fn(coordinator.data) or (flat and desc.create_on_flat)
    ]
    # Weekly schedule read-back sensors (flat R2 gateways only).
    if coordinator.client.is_flat_api:
        entities.extend(
            ElcoScheduleSensor(coordinator, gw_id, zone, key, translation_key, prog)
            for key, translation_key, prog, exists in SCHEDULE_PROGRAMS
            if exists(coordinator.data)
        )
        # Per-day read-back sensors for the writable programs (day 0=Sunday..6=Saturday).
        day_progs = SCHEDULE_PROGRAMS[:4]  # heating_z1..3 + dhw (buffer stays read-only weekly)
        entities.extend(
            ElcoScheduleDaySensor(coordinator, gw_id, zone, key.split("_", 1)[1], translation_key.split("_", 1)[1], prog if isinstance(prog, int) else prog[0], day)
            for key, translation_key, prog, exists in day_progs
            if exists(coordinator.data)
            for day in range(7)
        )
    async_add_entities(entities)


def _prog_day_blocks(prog_data: Any, day: int) -> list[str]:
    """Comfort blocks of one day as ['HH:MM-HH:MM', ...]."""
    weekly = (prog_data.get("timeProg") or {}).get("weeklyPlan") or {}
    allowed = weekly.get("allowedTemp") or [0, 1]
    comfort_idx = allowed.index(1) if 1 in allowed else None
    slices = _prog_day_slices(prog_data, day)
    try:
        slices = sorted(slices, key=lambda s: _int_or(s.get("from"), 0))
    except (TypeError, ValueError):
        pass
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
    return blocks


class ElcoScheduleDaySensor(CoordinatorEntity[ElcoRemoconCoordinator], SensorEntity):
    """Read-back of one weekday of a time program: that day's comfort blocks
    as state ('HH:MM-HH:MM, ...' or 'reduced'), the day's slices and its
    96-slot quarter-hour timeline as attributes."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: ElcoRemoconCoordinator,
        gw_id: str,
        zone: str,
        prefix: str,
        translation_prefix: str,
        prog_id: int,
        day: int,  # JS Date.getDay(): 0=Sunday .. 6=Saturday
    ) -> None:
        super().__init__(coordinator)
        self._prog_id = prog_id
        self._day = day
        key = f"schedule_{prefix}_d{day}"
        self._attr_translation_key = f"schedule_{translation_prefix}_day{day}"
        self._attr_unique_id = entity_unique_id(gw_id, zone, key)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, gw_id)},
            "name": "Remocon-Net Heat Pump",
            "manufacturer": "Elco",
            "model": "Aerotop SPK",
        }

    def _prog_data(self) -> Any:
        return self.coordinator.data.time_progs.get(self._prog_id)

    @property
    def native_value(self) -> StateType:
        prog_data = self._prog_data()
        if prog_data is None:
            return None
        blocks = _prog_day_blocks(prog_data, self._day)
        return ", ".join(blocks) if blocks else "reduced"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        prog_data = self._prog_data()
        if prog_data is None:
            return {}
        blocks = _prog_day_blocks(prog_data, self._day)
        comfort_minutes = sum(
            int(b.split("-")[1].replace(":", "")) // 100 * 60 + int(b.split("-")[1].replace(":", "")) % 100
            - (int(b.split("-")[0].replace(":", "")) // 100 * 60 + int(b.split("-")[0].replace(":", "")) % 100)
            for b in blocks
        )
        return {
            "day": _DAY_NAMES[self._day],
            "comfort_minutes": comfort_minutes,
            "slices": [
                {"from": f"{_int_or(s.get('from'), 0) // 60:02d}:{_int_or(s.get('from'), 0) % 60:02d}",
                 "temp": s.get("temp")}
                for s in _prog_day_slices(prog_data, self._day)
            ],
            "timeline": _prog_timeline(prog_data, self._day),
        }


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
        if description.icon:
            self._attr_icon = description.icon
        # A text/enum sensor (no device_class, no unit) must not carry
        # suggested_display_precision: HA would treat its state as numeric and
        # raise ValueError on string states (e.g. 'heating').
        if description.device_class is None and description.native_unit_of_measurement is None:
            self._attr_suggested_display_precision = None
        if description.options is not None:
            self._attr_options = description.options
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
