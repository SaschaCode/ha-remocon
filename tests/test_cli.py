"""Tests for standalone CLI protocol detection and flat-R2 controls."""

from unittest.mock import patch

import pytest

import remocon


LEGACY_RESPONSE = {
    "ok": True,
    "data": {
        "plantData": {
            "outsideTemp": 4,
            "dhwStorageTemp": 48,
            "dhwEnabled": 1,
            "heatPumpOn": 1,
            "dhwComfortTemp": {"value": 50},
            "dhwReducedTemp": {"value": 40},
            "dhwMode": {"value": 1, "options": [{"value": 1, "text": "ON"}]},
        },
        "zoneData": {
            "roomTemp": 19,
            "desiredRoomTemp": 20,
            "chComfortTemp": {"value": 21},
            "chReducedTemp": {"value": 17},
            "mode": {"value": 1, "allowedOptionTexts": ["Protection", "Automatic", "Reduction", "Comfort"]},
        },
    },
}

FLAT_ITEMS = {
    "DhwTimeProgComfortTemp": {"id": "DhwTimeProgComfortTemp", "value": 50, "min": 35, "max": 65, "step": 1},
    "DhwTimeProgEconomyTemp": {"id": "DhwTimeProgEconomyTemp", "value": 40, "min": 30, "max": 55, "step": 1},
    "DhwMode": {"id": "DhwMode", "value": 1},
    "DhwStorageTemperature": {"id": "DhwStorageTemperature", "value": 48},
    "IsDhwBoost": {"id": "IsDhwBoost", "value": 0},
    "ZoneComfortTemp": {"id": "ZoneComfortTemp", "value": 21, "min": 5, "max": 35, "step": 0.5},
    "ZoneEconomyTemp": {"id": "ZoneEconomyTemp", "value": 18, "min": 5, "max": 25, "step": 0.5},
    "ZoneDesiredTemp": {"id": "ZoneDesiredTemp", "value": 21},
    "ZoneMeasuredTemp": {"id": "ZoneMeasuredTemp", "value": 20},
    "ZoneMode": {"id": "ZoneMode", "value": 3, "optTexts": ["OFF", "TIME_PROGRAM", "MANUAL_NIGHT", "MANUAL"]},
    "ZoneHeatRequest": {"id": "ZoneHeatRequest", "value": 1},
    "OutsideTemp": {"id": "OutsideTemp", "value": 4},
    "IsHeatingPumpOn": {"id": "IsHeatingPumpOn", "value": 1},
    "IsQuite": {"id": "IsQuite", "value": 0},
    "PlantMode": {"id": "PlantMode", "value": 1},
}


def make_client() -> remocon.RemoconClient:
    client = remocon.RemoconClient(remocon.Config("e@example.com", "pw", "GW123"))
    client._protocol = "flat"
    return client


def test_legacy_protocol_and_write_endpoint_remain_unchanged():
    client = remocon.RemoconClient(remocon.Config("e@example.com", "pw", "GW123"))
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return LEGACY_RESPONSE if "GetData" in path else {"ok": True}

    with patch.object(client, "_request", side_effect=request):
        data = client.get_full_data()
        client.set_zone_temperatures(comfort=22)

    assert client._protocol == "legacy"
    assert data.comfort_temperature == 21
    assert "protocol" not in data.to_dict()
    assert calls[0][1] == "/R2/PlantHome/GetData/GW123?umsys=si"
    write = next(call for call in calls if "bsbZones/GW123/1/temperatures" in call[1])
    assert write[2]["json"] == {
        "new": {"comf": 22, "econ": 17.0},
        "old": {"comf": 21.0, "econ": 17.0},
    }


def test_bsb_browser_fallback_preserves_the_original_cli_request():
    client = remocon.RemoconClient(remocon.Config("e@example.com", "pw", "GW123"))
    calls = []

    def request(method, path, **kwargs):
        calls.append((path, kwargs))
        if path.endswith("/PlantHomeBsb/GetData/GW123"):
            return LEGACY_RESPONSE
        raise remocon.NetworkError("endpoint unavailable")

    with patch.object(client, "_request", side_effect=request):
        result = client.get_plant_data()

    assert "plantData" in result
    assert client._planthome_variant == "bsb_browser"
    browser_path, browser_call = calls[-1]
    assert browser_path == "/R2/PlantHomeBsb/GetData/GW123"
    assert browser_call["json"] == {
        "useCache": True,
        "zone": 1,
        "filter": {"progIds": None, "plant": True, "zone": True},
    }


def test_legacy_mode_and_dhw_writes_keep_original_endpoints():
    client = remocon.RemoconClient(remocon.Config("e@example.com", "pw", "GW123"))
    calls = []

    def request(method, path, **kwargs):
        calls.append((path, kwargs))
        return LEGACY_RESPONSE if "GetData" in path else {"ok": True}

    with patch.object(client, "_request", side_effect=request):
        client.set_zone_mode("comfort")
        client.set_dhw_temperature(comfort=52)
        client.set_dhw_mode("off")

    mode_path, mode_call = next(call for call in calls if "/bsbZones/" in call[0] and "/mode?" in call[0])
    dhw_temp_path, dhw_temp_call = next(call for call in calls if call[0].endswith("/dhwTemp"))
    dhw_mode_path, dhw_mode_call = next(call for call in calls if call[0].endswith("/dhwMode"))
    assert mode_path == "/api/v2/remote/bsbZones/GW123/1/mode?isCooling=false"
    assert mode_call["json"] == {"new": 3, "old": 1}
    assert dhw_temp_path == "/api/v2/remote/bsbPlantData/GW123/dhwTemp"
    assert dhw_temp_call["json"] == {
        "new": {"comf": 52, "econ": 40.0},
        "old": {"comf": 50.0, "econ": 40.0},
    }
    assert dhw_mode_path == "/api/v2/remote/bsbPlantData/GW123/dhwMode"
    assert dhw_mode_call["json"] == {"new": 0}


def test_flat_protocol_is_detected_from_flat_items_response():
    client = remocon.RemoconClient(remocon.Config("e@example.com", "pw", "GW123"))
    flat_response = {"ok": True, "data": {"items": [FLAT_ITEMS["ZoneComfortTemp"]]}}

    def request(method, path, **kwargs):
        if path.endswith("/GetData/GW123?umsys=si"):
            return flat_response
        return {"ok": True, "data": {"items": list(FLAT_ITEMS.values())}}

    with patch.object(client, "_request", side_effect=request):
        result = client.get_plant_data()

    assert client.is_flat_api
    assert result["ZoneComfortTemp"]["value"] == 21


def test_flat_gateway_maps_status_and_only_adds_flat_json_fields():
    client = make_client()
    with patch.object(client, "_get_flat_raw", return_value=FLAT_ITEMS):
        data = client.get_full_data()

    assert data.protocol == "flat"
    assert data.comfort_temperature == 21
    assert data.operation_mode == "MANUAL"
    assert data.dhw_mode == "timed"
    assert data.plant_mode == "winter"
    assert data.quiet_mode is False
    assert data.dhw_boost is False
    assert data.to_dict()["protocol"] == "flat"


def test_flat_dhw_save_matches_web_app_contract():
    client = make_client()
    calls = []

    def request(method, path, **kwargs):
        calls.append((path, kwargs))
        return {"ok": True}

    with patch.object(client, "_get_flat_raw", return_value=FLAT_ITEMS), \
            patch.object(client, "_confirm_write"), \
            patch.object(client, "_request", side_effect=request):
        client.set_dhw_temperature(comfort=52)

    path, kwargs = next(call for call in calls if call[0].endswith("/PlantDhw/Save/GW123"))
    payload = kwargs["json"]
    assert len(payload["requestItems"]) == 5
    assert {item["itemId"] for item in payload["requestItems"]} == set(remocon.DHW_WRITE_ITEM_IDS)
    changed = next(item for item in payload["requestItems"] if item["itemId"] == "DhwTimeProgComfortTemp")
    assert changed["value"] == 52
    assert len(payload["prevDataItems"]) == 5
    assert payload["features"]["gatewayId"] == "GW123"


def test_flat_quiet_mode_uses_advanced_settings_contract():
    client = make_client()
    advanced = {
        "IsQuite": {"id": "IsQuite", "zone": 0, "kind": 3, "value": 0},
        "QuietModeStart": {"id": "QuietModeStart", "zone": 0, "kind": 1, "value": 5632},
        "QuietModeEnd": {"id": "QuietModeEnd", "zone": 0, "kind": 1, "value": 1536},
    }
    calls = []

    def request(method, path, **kwargs):
        calls.append((path, kwargs))
        if path.endswith("/PlantAdvancedSettings/Refresh/GW123"):
            return {"ok": True, "data": {"dataItems": list(advanced.values())}}
        return {"ok": True}

    with patch.object(client, "_confirm_write"), patch.object(
        client, "_request", side_effect=request
    ):
        client.set_quiet_mode(True)

    path, kwargs = calls[-1]
    assert path == "/R2/PlantAdvancedSettings/Save/GW123"
    assert kwargs["headers"]["X-Requested-With"] == "XMLHttpRequest"
    payload = kwargs["json"]
    assert {item["itemId"]: item["value"] for item in payload["requestItems"]} == {
        "IsQuite": 1, "QuietModeStart": 5632, "QuietModeEnd": 1536,
    }
    assert payload["features"]["gatewayId"] == "GW123"


def test_flat_zone_write_uses_v2_dataitems_payload():
    client = make_client()
    calls = []

    def request(method, path, **kwargs):
        calls.append((path, kwargs))
        if path.endswith("/get?umsys=si"):
            item_id = kwargs["json"]["items"][0]["id"]
            return {"items": [FLAT_ITEMS[item_id]]}
        return {"success": True}

    with patch.object(client, "_get_flat_raw", return_value=FLAT_ITEMS), \
            patch.object(client, "_confirm_v2_write"), patch.object(
                client, "_request", side_effect=request
            ):
        client.set_zone_temperatures(comfort=21.5)

    path, kwargs = next(call for call in calls if call[0].endswith("/set?umsys=si"))
    assert path == "/api/v2/remote/dataItems/GW123/set?umsys=si"
    payload = kwargs["json"]
    assert payload["features"]["gatewayId"] == "GW123"
    assert payload["items"] == [{
        "id": "ZoneComfortTemp", "prevValue": 21, "value": 21.5, "zone": 1,
    }]


def test_flat_plant_mode_uses_v2_dataitems_payload():
    client = make_client()
    calls = []

    def request(method, path, **kwargs):
        calls.append((path, kwargs))
        if path.endswith("/get?umsys=si"):
            return {"items": [{"id": "PlantMode", "value": 1}]}
        return {"success": True}

    with patch.object(client, "_confirm_v2_write"), patch.object(
        client, "_request", side_effect=request
    ):
        client.set_plant_mode("cooling")

    path, kwargs = next(call for call in calls if call[0].endswith("/set?umsys=si"))
    assert path == "/api/v2/remote/dataItems/GW123/set?umsys=si"
    assert kwargs["json"]["items"] == [{
        "id": "PlantMode", "prevValue": 1, "value": 3, "zone": 0,
    }]


def test_flat_dhw_boost_uses_plant_dhw_save():
    client = make_client()
    calls = []

    def request(method, path, **kwargs):
        calls.append((path, kwargs))
        return {"ok": True}

    with patch.object(client, "_get_flat_raw", return_value=FLAT_ITEMS), \
            patch.object(client, "_confirm_write"), \
            patch.object(client, "_request", side_effect=request):
        client.set_dhw_boost(True)

    path, kwargs = next(call for call in calls if call[0].endswith("/PlantDhw/Save/GW123"))
    changed = next(
        item for item in kwargs["json"]["requestItems"]
        if item["itemId"] == "IsDhwBoost"
    )
    assert changed["value"] == 1


def test_parser_keeps_legacy_modes_and_adds_flat_commands():
    parser = remocon.build_parser()
    assert parser.parse_args(["set-mode", "comfort"]).mode == "comfort"
    assert parser.parse_args(["set-mode", "time_program"]).mode == "time_program"
    assert parser.parse_args(["set-dhw-mode", "continuous"]).mode == "continuous"
    assert parser.parse_args(["set-quiet-mode", "on"]).command == "set-quiet-mode"
    assert parser.parse_args(["set-dhw-boost", "off"]).command == "set-dhw-boost"
    assert parser.parse_args(["set-plant-mode", "winter"]).command == "set-plant-mode"


def test_flat_temperature_validation_rejects_bad_values():
    client = make_client()
    with patch.object(client, "_get_flat_raw", return_value=FLAT_ITEMS), \
            patch.object(client, "_confirm_write"), patch.object(client, "_request"):
        with pytest.raises(remocon.DataError, match="increments"):
            client.set_dhw_temperature(comfort=50.5)


def test_flat_temperature_validation_rejects_non_finite_values():
    client = make_client()
    with pytest.raises(remocon.DataError, match="finite"):
        client._validate_temperature(float("nan"), 5, 35, 0.5, "Zone temperature")


def test_flat_zone_mode_rejects_unsupported_cooling_write():
    client = make_client()
    with pytest.raises(remocon.DataError, match="Cooling mode writes"):
        client.set_zone_mode("manual", is_cooling=True)
