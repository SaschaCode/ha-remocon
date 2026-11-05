"""Tests for the Phase-2 diagnostics: flat R2 item mapping, menuItems,
EM20 consumption report, entity existence rules, and the options flow.

All HTTP responses are simulated from tests/fixtures/dump_flat_r2_redacted.json;
these tests never contact Remocon-Net.
"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.elco_remocon.api import (
    CONSUMPTION_KIND_CH,
    CONSUMPTION_KIND_DHW,
    CONSUMPTION_PERIOD_BUCKETS,
    METERING_POLL_INTERVAL,
    RemoconClient,
    RemoconData,
)
from custom_components.elco_remocon.config_flow import (
    ElcoRemoconConfigFlow,
    ElcoRemoconOptionsFlow,
)
from custom_components.elco_remocon.const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

FIXTURE = Path(__file__).parent / "fixtures" / "dump_flat_r2_redacted.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def flat_items_from_fixture() -> dict:
    """Flat item dict {id: item} from planthome groups plus v2 dataItems
    (the live gateway exposes HeatingFlowTemp via the v2 dataItems call)."""
    fx = load_fixture()
    items = {}
    for group in ("planthome_full", "planthome_status"):
        for item in fx.get(group, []):
            items[item["id"]] = item
    for item in fx.get("data_items", []):
        items[item["id"]] = item
    return items


def menu_items_from_fixture() -> dict:
    fx = load_fixture()
    return {int(k): v[0] for k, v in fx["menu_items"].items()
            if isinstance(v, list) and v and isinstance(v[0], dict)}


def consumption_from_fixture() -> dict:
    fx = load_fixture()
    out = {}
    for seq in fx["consumption"]:
        out[(int(seq["k"]), int(seq["p"]))] = [float(v) for v in seq["v"]]
    return out


def make_client() -> RemoconClient:
    return RemoconClient(email="e@example.com", password="pw", gateway_id="GW123", zone="1")


# ---------------------------------------------------------------------------
# Parsing: fixture -> RemoconData
# ---------------------------------------------------------------------------

def test_flat_data_maps_all_diagnostic_items():
    data = RemoconClient._flat_data(flat_items_from_fixture())
    # live gateway: measured flow = HeatingFlowTemp (v2 dataItem),
    # setpoint = ChFlowSetpointTemp; the two fields are distinct
    assert data.flow_temperature == 40.0          # HeatingFlowTemp (measured)
    assert data.flow_setpoint_temperature == 52.0  # ChFlowSetpointTemp (setpoint)
    # items this gateway does not expose stay None (-> no entity)
    assert data.return_temperature is None
    assert data.power_w is None                    # no power dataItem on this gw
    assert data.compressor_freq_hz is None
    assert data.heating_curve_offset == 0.0        # HeatingFlowOffset (v2)
    assert data.room_temp == 0.0                   # ZoneMeasuredTemp present, 0.0
    assert data.has_room_sensor is True


def test_menu_items_overlay_hp_mode_flow_and_temps():
    fx = load_fixture()
    menu = menu_items_from_fixture()
    data = RemoconClient._flat_data(flat_items_from_fixture())
    RemoconClient._apply_menu_items(data, menu)
    # values read live from the gateway dump
    assert data.hp_mode == int(menu[340]["value"])
    assert data.defrost_active is None  # live mode is not defrost
    assert data.hp_water_temperature == menu[348]["value"]
    assert data.hp_outside_temp == menu[341]["value"]
    # menuItem 349 (sentinel 3276.0) must NOT leak into flow_temperature
    assert data.flow_temperature == 40.0      # HeatingFlowTemp dataItem
    assert data.flow_setpoint_temperature == 52.0  # ChFlowSetpointTemp


def test_menu_item_340_defrost_sets_defrost_flag():
    data = RemoconData()
    RemoconClient._apply_menu_items(data, {340: {"id": 340, "value": 9}})
    assert data.hp_mode == 9
    assert data.defrost_active is True


def test_menu_item_340_booster_sets_backup_heater_flag():
    data = RemoconData()
    RemoconClient._apply_menu_items(data, {340: {"id": 340, "value": 4}})
    assert data.hp_mode == 4
    assert data.backup_heater_on is True


def test_consumption_report_sums_48h_window():
    data = RemoconData()
    RemoconClient._apply_consumption(data, consumption_from_fixture())
    ch = consumption_from_fixture()[(CONSUMPTION_KIND_CH, CONSUMPTION_PERIOD_BUCKETS)]
    dhw = consumption_from_fixture()[(CONSUMPTION_KIND_DHW, CONSUMPTION_PERIOD_BUCKETS)]
    assert data.energy_consumed_ch_kwh == round(sum(ch), 4)
    assert data.energy_consumed_dhw_kwh == round(sum(dhw), 4)
    assert data.energy_consumed_total_kwh == round(sum(ch) + sum(dhw), 4)
    assert data.energy_consumed_ch_kwh == pytest.approx(sum(ch), abs=1e-6)


def test_consumption_report_empty_leaves_none():
    data = RemoconData()
    RemoconClient._apply_consumption(data, {})
    assert data.energy_consumed_ch_kwh is None
    assert data.energy_consumed_total_kwh is None


# ---------------------------------------------------------------------------
# Missing items -> None fields (drives exists_fn -> no entity)
# ---------------------------------------------------------------------------

def test_missing_diagnostic_items_yield_none():
    minimal = {
        "ZoneComfortTemp": {"id": "ZoneComfortTemp", "value": 21},
        "ZoneEconomyTemp": {"id": "ZoneEconomyTemp", "value": 17},
        "ZoneMeasuredTemp": {"id": "ZoneMeasuredTemp", "value": 20},
    }
    data = RemoconClient._flat_data(minimal)
    assert data.flow_temperature is None
    assert data.flow_setpoint_temperature is None
    assert data.return_temperature is None
    assert data.power_w is None
    assert data.compressor_freq_hz is None
    assert data.backup_heater_on is None
    assert data.defrost_active is None
    assert data.dhw_request is None
    assert data.dhw_heating_rod_on is None
    assert data.heating_curve_offset is None
    assert data.hp_water_temperature is None
    # room sensor present here
    assert data.has_room_sensor is True
    assert data.room_temp == 20


def test_no_room_sensor_means_no_room_entity():
    minimal = {
        "ZoneComfortTemp": {"id": "ZoneComfortTemp", "value": 21},
        "ZoneEconomyTemp": {"id": "ZoneEconomyTemp", "value": 17},
    }
    data = RemoconClient._flat_data(minimal)
    assert data.has_room_sensor is False


# ---------------------------------------------------------------------------
# get_data end-to-end with mocked _request (menuItems + consumption)
# ---------------------------------------------------------------------------

def _flat_response(items):
    return {"ok": True, "data": {"items": list(items.values())}}


def test_get_data_flat_includes_diagnostics_and_metering():
    client = make_client()
    client._flat_api = True
    items = flat_items_from_fixture()
    menu = menu_items_from_fixture()
    cons = load_fixture()["consumption"]

    def fake_request(method, path, **kwargs):
        if "menuItems=" in path:
            mid = int(path.split("menuItems=")[1])
            return [menu[mid]] if mid in menu else []
        if "consSequencesApi8" in path:
            return cons
        return _flat_response(items)

    with patch.object(client, "_request", side_effect=fake_request):
        data = client.get_data()

    assert data.power_w is None                  # no power dataItem on this gateway
    assert data.backup_heater_on is None         # menuItem 337 EMPTY on this plant
    assert data.hp_mode == 1                     # menuItem 340 = Standby in the live dump
    ch48 = consumption_from_fixture()[(CONSUMPTION_KIND_CH, CONSUMPTION_PERIOD_BUCKETS)]
    assert data.energy_consumed_ch_kwh == pytest.approx(sum(ch48), abs=1e-6)
    assert data.flow_temperature == 40.0
    assert data.flow_setpoint_temperature == 52.0


def test_get_data_flat_survives_menu_and_metering_failures():
    client = make_client()
    client._flat_api = True
    items = flat_items_from_fixture()

    def fake_request(method, path, **kwargs):
        if "menuItems=" in path or "consSequencesApi8" in path:
            raise RuntimeError("boom")
        return _flat_response(items)

    with patch.object(client, "_request", side_effect=fake_request):
        data = client.get_data()  # must not raise

    assert data.comfort_temp == 21.0
    assert data.flow_temperature == 40.0   # from dataItems still works
    assert data.backup_heater_on is None   # menuItems failed -> None
    assert data.energy_consumed_ch_kwh is None


def test_metering_is_rate_limited():
    client = make_client()
    client._flat_api = True
    items = flat_items_from_fixture()
    calls = {"cons": 0}

    def fake_request(method, path, **kwargs):
        if "consSequencesApi8" in path:
            calls["cons"] += 1
            return load_fixture()["consumption"]
        if "menuItems=" in path:
            return []
        return _flat_response(items)

    with patch.object(client, "_request", side_effect=fake_request):
        client.get_data()
        client.get_data()  # within METERING_POLL_INTERVAL -> skipped
        assert calls["cons"] == 1
        client._last_metering_fetch -= METERING_POLL_INTERVAL + 1
        client.get_data()
        assert calls["cons"] == 2


# ---------------------------------------------------------------------------
# Entity existence rules (exists_fn)
# ---------------------------------------------------------------------------

def _stub_sensor_platform_modules():
    """Stub the homeassistant.components.sensor module tree for import."""
    import sys, types
    from dataclasses import dataclass

    class Entity:
        pass

    @dataclass(kw_only=True)
    class EntityDescription:
        key: str
        translation_key: str = ""

    def module(name, **attrs):
        m = sys.modules.get(name) or types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m

    comp = module("homeassistant.components")
    comp.__path__ = []
    module(
        "homeassistant.components.sensor",
        SensorDeviceClass=types.SimpleNamespace(
            TEMPERATURE="temperature", PRESSURE="pressure", POWER="power",
            ENERGY="energy", FREQUENCY="frequency",
        ),
        SensorStateClass=types.SimpleNamespace(MEASUREMENT="measurement", TOTAL_INCREASING="total_increasing"),
        SensorEntity=type("SensorEntity", (Entity,), {}),
        SensorEntityDescription=EntityDescription,
    )
    const = sys.modules.get("homeassistant.const") or module("homeassistant.const")
    const.EntityCategory = types.SimpleNamespace(DIAGNOSTIC="diagnostic", CONFIG="config")
    const.UnitOfPressure = types.SimpleNamespace(BAR="bar")
    const.UnitOfTemperature = types.SimpleNamespace(CELSIUS="°C")
    const.UnitOfPower = types.SimpleNamespace(WATT="W")
    const.UnitOfEnergy = types.SimpleNamespace(KILO_WATT_HOUR="kWh")
    const.UnitOfFrequency = types.SimpleNamespace(HERTZ="Hz")
    const.UnitOfElectricCurrent = types.SimpleNamespace(AMPERE="A")
    module("homeassistant.helpers.entity_platform", AddConfigEntryEntitiesCallback=object)
    module("homeassistant.helpers.typing", StateType=object)
    uc = sys.modules.get("homeassistant.helpers.update_coordinator") or module("homeassistant.helpers.update_coordinator")

    class CoordinatorEntity:
        def __class_getitem__(cls, item):
            return cls
    uc.CoordinatorEntity = CoordinatorEntity
    sys.modules.pop("custom_components.elco_remocon.sensor", None)


def test_sensor_descriptions_exist_fn():
    _stub_sensor_platform_modules()
    from custom_components.elco_remocon.sensor import SENSORS

    by_key = {d.key: d for d in SENSORS}
    full = RemoconClient._flat_data(flat_items_from_fixture())
    RemoconClient._apply_menu_items(full, menu_items_from_fixture())
    RemoconClient._apply_consumption(full, consumption_from_fixture())

    # live gateway exposes: flow_setpoint, room (item present), hp_water_temp,
    # heating_curve_offset, energy_* ; it does NOT expose return_temp, power,
    # compressor_freq on this plant
    for key in (
        "flow_setpoint_temp", "room_temp", "hp_water_temp", "heating_curve_offset",
        "energy_consumed_ch", "energy_consumed_dhw", "energy_consumed_total",
        "hp_mode",
    ):
        assert by_key[key].exists_fn(full), key
    for key in ("return_temp", "power", "compressor_freq"):
        assert not by_key[key].exists_fn(full), key
    # flow_temp exists via setpoint fallback even without measured flow
    assert by_key["flow_temp"].exists_fn(full)

    empty = RemoconData()
    for key in (
        "flow_setpoint_temp", "return_temp", "power", "compressor_freq",
        "hp_water_temp", "heating_curve_offset",
        "energy_consumed_ch", "energy_consumed_dhw", "energy_consumed_total",
    ):
        assert not by_key[key].exists_fn(empty), key
    # room_temp depends on has_room_sensor
    assert not by_key["room_temp"].exists_fn(empty)
    # existing flow_temp entity keeps its key and unique id
    assert by_key["flow_temp"].key == "flow_temp"
    assert by_key["flow_temp"].exists_fn(full)


def _stub_binary_sensor_platform_modules():
    import sys, types
    from dataclasses import dataclass

    class Entity:
        pass

    @dataclass(kw_only=True)
    class EntityDescription:
        key: str
        translation_key: str = ""

    def module(name, **attrs):
        m = sys.modules.get(name) or types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m

    comp = sys.modules.get("homeassistant.components") or module("homeassistant.components")
    comp.__path__ = []
    module(
        "homeassistant.components.binary_sensor",
        BinarySensorDeviceClass=types.SimpleNamespace(HEAT="heat", COLD="cold", RUNNING="running"),
        BinarySensorEntity=type("BinarySensorEntity", (Entity,), {}),
        BinarySensorEntityDescription=EntityDescription,
    )
    module("homeassistant.helpers.entity_platform", AddConfigEntryEntitiesCallback=object)
    uc = sys.modules.get("homeassistant.helpers.update_coordinator") or module("homeassistant.helpers.update_coordinator")

    class CoordinatorEntity:
        def __class_getitem__(cls, item):
            return cls
    uc.CoordinatorEntity = CoordinatorEntity
    sys.modules.pop("custom_components.elco_remocon.binary_sensor", None)


def test_binary_sensor_descriptions_exist_fn():
    _stub_binary_sensor_platform_modules()
    from custom_components.elco_remocon.binary_sensor import BINARY_SENSORS

    by_key = {d.key: d for d in BINARY_SENSORS}
    full = RemoconClient._flat_data(flat_items_from_fixture())
    RemoconClient._apply_menu_items(full, menu_items_from_fixture())

    # live gateway: hp_mode=1 (standby) -> defrost/dhw flags stay None;
    # menuItem 337 EMPTY -> backup_heater None; IsResistorOn/DefrostActive/
    # DhwHeatRequest absent from the flat dump -> None
    for key in ("backup_heater", "defrost", "dhw_request", "dhw_heating_rod"):
        assert not by_key[key].exists_fn(full), key

    empty = RemoconData()
    for key in ("backup_heater", "defrost", "dhw_request", "dhw_heating_rod"):
        assert not by_key[key].exists_fn(empty), key


def test_flow_temp_unique_id_is_stable():
    """The existing flow_temp entity unique ID must not change."""
    from custom_components.elco_remocon.identifiers import entity_unique_id

    assert entity_unique_id("GW123", "1", "flow_temp") == "GW123_flow_temp"
    assert entity_unique_id("GW123", "1", "flow_setpoint_temp") == "GW123_flow_setpoint_temp"


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------

def _entry(options=None):
    return SimpleNamespace(options=options or {}, data={"gateway_id": "GW123"})


def test_options_flow_shows_form_with_default():
    flow = ElcoRemoconOptionsFlow()
    flow.config_entry = _entry()
    result = asyncio.run(flow.async_step_init())
    assert result["type"] == "form"
    assert result["step_id"] == "init"


def test_options_flow_accepts_valid_interval():
    flow = ElcoRemoconOptionsFlow()
    flow.config_entry = _entry()
    result = asyncio.run(flow.async_step_init({CONF_SCAN_INTERVAL: 60}))
    assert result["type"] == "create_entry"
    assert result["data"] == {CONF_SCAN_INTERVAL: 60}


def test_options_flow_range_constants():
    assert MIN_SCAN_INTERVAL == 30
    assert MAX_SCAN_INTERVAL == 600
    assert DEFAULT_SCAN_INTERVAL == 120


def test_config_flow_exposes_options_flow():
    assert hasattr(ElcoRemoconConfigFlow, "async_get_options_flow")


# ---------------------------------------------------------------------------
# CLI dump-items redaction
# ---------------------------------------------------------------------------

def test_cli_redacts_credentials_and_gateway_id():
    import remocon

    dirty = {
        "email": "user@example.com",
        "password": "secret",
        "gatewayId": "0C8B950B67D8",
        "nested": [{"token": "abc", "value": "0C8B950B67D8"}],
    }
    clean = remocon._redact(dirty)
    assert clean["email"] == "<REDACTED>"
    assert clean["password"] == "<REDACTED>"
    assert clean["gatewayId"] == "<REDACTED>"
    assert clean["nested"][0]["token"] == "<REDACTED>"
    assert clean["nested"][0]["value"] == "<GWID>"


def test_cli_fixture_is_redacted():
    text = FIXTURE.read_text(encoding="utf-8")
    assert "0C8B950B67D8" not in text
    assert "password" not in text.lower() or "<REDACTED>" in text


# ---------------------------------------------------------------------------
# Live-gateway behaviours (Aerotop MONO 15.2 ODU, 2026-11-05)
# ---------------------------------------------------------------------------

def test_flow_temp_falls_back_to_setpoint_when_no_measured_item():
    """Gateways without HeatingFlowTemp/ChFlowTemp still show flow_temp (setpoint)."""
    items = {
        "ZoneComfortTemp": {"id": "ZoneComfortTemp", "value": 21},
        "ChFlowSetpointTemp": {"id": "ChFlowSetpointTemp", "value": 52},
    }
    data = RemoconClient._flat_data(items)
    assert data.flow_temperature == 52.0          # setpoint fallback
    assert data.flow_setpoint_temperature == 52.0


def test_room_temp_zero_means_unknown_value():
    """ZoneMeasuredTemp exists but 0.0 (no room sensor) -> sensor value None."""
    _stub_sensor_platform_modules()
    from custom_components.elco_remocon.sensor import SENSORS
    d = RemoconData(room_temp=0.0, has_room_sensor=True)
    desc = {x.key: x for x in SENSORS}["room_temp"]
    assert desc.exists_fn(d) is True
    assert desc.value_fn(d) is None  # 0.0 -> unknown, not bogus 0 °C


def test_hp_mode_sensor_maps_menu340():
    _stub_sensor_platform_modules()
    from custom_components.elco_remocon.sensor import SENSORS, HP_MODE_MAP
    desc = {x.key: x for x in SENSORS}["hp_mode"]
    d = RemoconData(hp_mode=9)
    assert desc.exists_fn(d) is True
    assert desc.value_fn(d) == "defrost"
    d2 = RemoconData(hp_mode=4)
    assert desc.value_fn(d2) == "heating_booster"
    assert HP_MODE_MAP[3] == "heating"


def test_energy_values_carry_over_between_metering_polls():
    """Between the 15-min metering fetches, energy sensors keep last values."""
    client = make_client()
    client._flat_api = True
    items = flat_items_from_fixture()

    def fake_request(method, path, **kwargs):
        if "consSequencesApi8" in path:
            return load_fixture()["consumption"]
        if "menuItems=" in path:
            return []
        if "dataItems" in path:
            return {"items": []}
        return _flat_response(items)

    with patch.object(client, "_request", side_effect=fake_request):
        d1 = client.get_data()
        assert d1.energy_consumed_total_kwh is not None
        d2 = client.get_data()  # within METERING_POLL_INTERVAL -> carried over
        assert d2.energy_consumed_total_kwh == d1.energy_consumed_total_kwh
