"""Tests for flat R2 write controls."""

from unittest.mock import patch

import pytest

from custom_components.elco_remocon.api import RemoconClient, RemoconDataError


ITEMS = {
    "DhwTimeProgComfortTemp": {"id": "DhwTimeProgComfortTemp", "value": 50, "min": 35, "max": 65, "step": 1},
    "DhwTimeProgEconomyTemp": {"id": "DhwTimeProgEconomyTemp", "value": 40, "min": 30, "max": 55, "step": 1},
    "DhwMode": {"id": "DhwMode", "value": 1},
    "DhwStorageTemperature": {"id": "DhwStorageTemperature", "value": 48},
    "IsDhwBoost": {"id": "IsDhwBoost", "value": 0},
    "ZoneComfortTemp": {"id": "ZoneComfortTemp", "value": 21, "min": 5, "max": 35, "step": 0.5},
    "ZoneEconomyTemp": {"id": "ZoneEconomyTemp", "value": 18, "min": 5, "max": 35, "step": 0.5},
    "ZoneMode": {"id": "ZoneMode", "value": 3},
    "PlantMode": {"id": "PlantMode", "value": 1},
    "IsQuite": {"id": "IsQuite", "value": 0},
}


def make_client() -> RemoconClient:
    client = RemoconClient("e@example.com", "pw", "GW123")
    client._flat_api = True
    return client


def assert_save_call(request, endpoint: str, item_id: str, value):
    assert request.call_args.args[0] == "POST"
    assert request.call_args.args[1] == endpoint
    payload = request.call_args.kwargs["json"]
    request_key = "itemId" if endpoint.endswith("/PlantDhw/Save/GW123") else "id"
    changed = next(item for item in payload["requestItems"] if item[request_key] == item_id)
    assert changed["value"] == value
    assert {item["id"] for item in payload["prevDataItems"]} >= {
        item_id,
        "DhwStorageTemperature",
    }
    if endpoint.endswith("/PlantDhw/Save/GW123"):
        assert len(payload["requestItems"]) == 5
        assert {item["itemId"] for item in payload["requestItems"]} == {
            "DhwTimeProgComfortTemp",
            "DhwTimeProgEconomyTemp",
            "DhwMode",
            "DhwStorageTemperature",
            "IsDhwBoost",
        }
        assert payload["features"]["gatewayId"] == "GW123"
        assert all(item["gatewayId"] == "GW123" for item in payload["prevDataItems"])


def test_dhw_comfort_temperature_writes_and_preserves_siblings():
    client = make_client()
    with patch.object(client, "_get_flat_raw", return_value=ITEMS), patch.object(
        client, "_confirm_write"
    ), patch.object(client, "_request") as request:
        client.set_dhw_comfort_temperature(52)

    assert_save_call(request, "/R2/PlantDhw/Save/GW123", "DhwTimeProgComfortTemp", 52)


def test_dhw_reduced_temperature_writes_expected_endpoint():
    client = make_client()
    with patch.object(client, "_get_flat_raw", return_value=ITEMS), patch.object(
        client, "_confirm_write"
    ), patch.object(client, "_request") as request:
        client.set_dhw_reduced_temperature(42)

    assert_save_call(request, "/R2/PlantDhw/Save/GW123", "DhwTimeProgEconomyTemp", 42)


def test_mode_and_switch_writes_use_flat_endpoints():
    client = make_client()
    with patch.object(client, "_get_flat_raw", return_value=ITEMS), patch.object(
        client, "_confirm_write"
    ), patch.object(client, "_request") as request:
        client.set_dhw_mode(2)
        assert_save_call(request, "/R2/PlantDhw/Save/GW123", "DhwMode", 2)

        client.set_dhw_boost(True)
        assert_save_call(request, "/R2/PlantDhw/Save/GW123", "IsDhwBoost", 1)

        client.set_zone_mode(2)
        assert_save_call(request, "/R2/PlantZone/Save/GW123", "ZoneMode", 2)

        client.set_quiet_mode(True)
        assert_save_call(request, "/R2/PlantMenu/Save/GW123", "IsQuite", 1)


def test_zone_temperature_and_plant_mode_writes():
    client = make_client()
    with patch.object(client, "_get_flat_raw", return_value=ITEMS), patch.object(
        client, "_confirm_write"
    ), patch.object(client, "_request") as request:
        client.set_zone_comfort_temperature(21.5)
        assert_save_call(request, "/R2/PlantZone/Save/GW123", "ZoneComfortTemp", 21.5)

        client.set_zone_reduced_temperature(18.5)
        assert_save_call(request, "/R2/PlantZone/Save/GW123", "ZoneEconomyTemp", 18.5)

        client.set_plant_mode(3)
        assert_save_call(request, "/R2/PlantMenu/Submit/GW123", "PlantMode", 3)


@pytest.mark.parametrize(
    ("method", "value"),
    [
        ("set_dhw_comfort_temperature", 34),
        ("set_dhw_comfort_temperature", 50.5),
        ("set_dhw_reduced_temperature", 56),
        ("set_zone_mode", 1),
        ("set_dhw_mode", 3),
        ("set_plant_mode", 4),
    ],
)
def test_write_values_are_validated(method, value):
    client = make_client()
    with patch.object(client, "_get_flat_raw", return_value=ITEMS), patch.object(
        client, "_confirm_write"
    ), patch.object(client, "_request"):
        with pytest.raises(RemoconDataError):
            getattr(client, method)(value)


def test_write_confirmation_uses_uncached_reread():
    client = make_client()
    response = {
        "ok": True,
        "data": {"items": [{"id": "DhwMode", "value": "2"}]},
    }
    with patch.object(client, "_request", return_value=response) as request:
        client._confirm_write("DhwMode", 2, max_retries=1)

    assert request.call_count == 1
    assert request.call_args.kwargs["json"]["useCache"] is False
