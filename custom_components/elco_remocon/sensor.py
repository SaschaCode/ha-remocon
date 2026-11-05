"""Sensor entities for Elco Remocon-Net."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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

from .api import RemoconData
from .const import CONF_ZONE, DEFAULT_ZONE, DOMAIN
from .coordinator import ElcoRemoconCoordinator
from .identifiers import entity_unique_id

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
    suggested_display_precision: int = 1
    icon: str | None = None
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
        value_fn=lambda d: d.hp_water_temperature,
        exists_fn=lambda d: d.hp_water_temperature is not None,
    ),
    ElcoSensorDescription(
        key="hp_mode",
        translation_key="hp_mode",
        icon="mdi:heat-pump-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
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
    async_add_entities(entities)


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
