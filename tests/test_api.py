"""Tests for RemoconClient._get_raw's non-BSB/BSB endpoint fallback.

Remocon-Net splits systems onto two endpoint families depending on whether
they're BSB-bus (e.g. Aerotop/Aquatop heat pumps) or not (e.g. older
boilers), and each family 500s if you call the other one's endpoint. These
tests cover the probe/fallback/cache/self-heal behavior in _get_raw without
making any real HTTP requests.
"""
from unittest.mock import patch

import pytest

from custom_components.elco_remocon.api import (
    RemoconClient,
    RemoconConnectionError,
    RemoconDataError,
)

GOOD_RESPONSE = {"ok": True, "data": {"plantData": {}, "zoneData": {}}}


def make_client() -> RemoconClient:
    return RemoconClient(email="e@example.com", password="pw", gateway_id="GW123", zone="1")


def test_first_prefix_succeeds_makes_one_request():
    client = make_client()
    with patch.object(client, "_request", return_value=GOOD_RESPONSE) as mock_request:
        client._get_raw()

    assert mock_request.call_count == 1
    called_path = mock_request.call_args[0][1]
    assert called_path.startswith("/R2/PlantHome/GetData/")
    assert client._planthome_prefix == "/R2/PlantHome"


def test_falls_back_to_bsb_and_caches_it():
    client = make_client()
    with patch.object(
        client,
        "_request",
        side_effect=[RemoconConnectionError("500"), GOOD_RESPONSE],
    ) as mock_request:
        client._get_raw()

    assert mock_request.call_count == 2
    first_path = mock_request.call_args_list[0][0][1]
    second_path = mock_request.call_args_list[1][0][1]
    assert first_path.startswith("/R2/PlantHome/GetData/")
    assert second_path.startswith("/R2/PlantHomeBsb/GetData/")
    assert client._planthome_prefix == "/R2/PlantHomeBsb"


def test_legacy_non_bsb_payload_keeps_legacy_mapping():
    client = make_client()
    legacy = {"ok": True, "data": {
        "plantData": {"outsideTemp": -2.0},
        "zoneData": {"chComfortTemp": {"value": 20.5}, "roomTemp": 19.0},
    }}
    with patch.object(client, "_request", return_value=legacy), patch.object(
        client, "_get_system_items", return_value={}
    ):
        data = client.get_data()

    assert data.comfort_temp == 20.5
    assert data.room_temp == 19.0
    assert data.outside_temp == -2.0
    assert client._flat_api is False


def test_both_prefixes_fail_raises_first_error_and_caches_nothing():
    client = make_client()
    first_error = RemoconConnectionError("500 non-bsb")
    second_error = RemoconConnectionError("500 bsb")
    flat_error = RemoconConnectionError("500 flat")
    with patch.object(
        client, "_request", side_effect=[first_error, second_error, flat_error]
    ) as request:
        with pytest.raises(RemoconConnectionError) as exc_info:
            client._get_raw()

    assert request.call_count == 3
    assert request.call_args.args[1] == "/R2/PlantHome/GetData/GW123"
    assert exc_info.value is flat_error
    assert client._planthome_prefix is None
    assert client.is_flat_api is False


def test_cached_prefix_is_used_on_subsequent_call_without_reprobing():
    client = make_client()
    client._planthome_prefix = "/R2/PlantHomeBsb"
    with patch.object(client, "_request", return_value=GOOD_RESPONSE) as mock_request:
        client._get_raw()

    assert mock_request.call_count == 1
    called_path = mock_request.call_args[0][1]
    assert called_path.startswith("/R2/PlantHomeBsb/GetData/")


def test_cached_prefix_failing_clears_cache_for_next_call():
    client = make_client()
    client._planthome_prefix = "/R2/PlantHomeBsb"
    with patch.object(client, "_request", side_effect=RemoconConnectionError("500 again")):
        with pytest.raises(RemoconConnectionError):
            client._get_raw()

    assert client._planthome_prefix is None


def test_cached_prefix_failure_probes_alternate_legacy_prefix():
    client = make_client()
    client._planthome_prefix = "/R2/PlantHomeBsb"
    with patch.object(
        client,
        "_request",
        side_effect=[RemoconConnectionError("cached prefix failed"), GOOD_RESPONSE],
    ) as request:
        client._get_raw()

    assert request.call_count == 2
    assert request.call_args_list[0].args[1].startswith("/R2/PlantHomeBsb/")
    assert request.call_args_list[1].args[1].startswith("/R2/PlantHome/")
    assert client._planthome_prefix == "/R2/PlantHome"


def flat_response():
    return {"ok": True, "data": {"items": [
        {"id": "ZoneComfortTemp", "value": "21.5", "min": 5, "max": 30, "step": 0.5},
        {"id": "ZoneEconomyTemp", "value": "18"},
        {"id": "ZoneMeasuredTemp", "value": "20.25"},
        {"id": "ZoneDesiredTemp", "value": "22"},
        {"id": "ZoneMode", "value": "3", "optTexts": ["Off", "Manual", "Schedule"]},
        {"id": "ZoneHeatRequest", "value": "1"},
        {"id": "OutsideTemp", "value": "-4.5"},
        {"id": "DhwStorageTemperature", "value": "48"},
        {"id": "DhwTimeProgComfortTemp", "value": "50"},
        {"id": "DhwTimeProgEconomyTemp", "value": "40"},
        {"id": "DhwMode", "value": "1"},
        {"id": "IsHeatingPumpOn", "value": "true"},
    ]}}


def test_http_200_flat_response_is_detected_and_mapped():
    client = make_client()
    with patch.object(
        client,
        "_request",
        side_effect=[flat_response(), flat_response(), flat_response()],
    ) as request:
        data = client.get_data()

    assert request.call_count == 3
    assert request.call_args.args[1].startswith("/R2/PlantHome/GetData/")
    assert "?umsys=si" not in request.call_args.args[1]
    assert request.call_args.kwargs["json"]["features"]["hpSys"] is True
    assert data.comfort_temp == 21.5
    assert data.reduced_temp == 18
    assert data.room_temp == 20.25
    assert data.zone_mode == 3
    assert data.outside_temp == -4.5
    assert data.dhw_temp == 48
    assert data.dhw_boost_available is False
    assert data.heat_pump_on is True
    assert client._flat_api is True


def test_flat_protocol_is_cached_for_followup_polls():
    client = make_client()
    with patch.object(client, "_request", side_effect=[
        RemoconConnectionError("legacy 500"), RemoconConnectionError("BSB 500"),
        flat_response(), flat_response(), flat_response(), flat_response(),
    ]) as request:
        client.get_data()
        client.get_data()

    assert request.call_count == 6
    assert request.call_args_list[2].args[1] == "/R2/PlantHome/GetData/GW123"
    assert request.call_args_list[3].args[1] == "/R2/PlantHome/GetData/GW123"
    assert request.call_args_list[4].kwargs["json"]["filter"]["notEssentials"] is False
    assert request.call_args_list[5].kwargs["json"]["filter"]["notEssentials"] is True


def test_http_500_legacy_families_use_flat_fallback():
    client = make_client()
    legacy_error = RemoconConnectionError("500")
    with patch.object(
        client,
        "_request",
        side_effect=[legacy_error, legacy_error, flat_response(), flat_response()],
    ) as request:
        data = client.get_data()

    assert request.call_count == 4
    fallback_path = request.call_args_list[2].args[1]
    assert fallback_path == "/R2/PlantHome/GetData/GW123"
    payload = request.call_args_list[2].kwargs["json"]
    assert payload["features"]["gatewayId"] == "GW123"
    assert payload["features"]["zones"][0]["num"] == 1
    assert data.comfort_temp == 21.5


def test_flat_response_disables_unverified_writes():
    client = make_client()
    with patch.object(client, "_request", return_value=flat_response()) as request:
        with pytest.raises(RemoconDataError, match="Control is disabled"):
            client.set_zone_temperatures(comfort=22)

    # Only the read request was made; no control request escaped.
    assert request.call_count == 3


def test_flat_fallback_error_is_reported():
    client = make_client()
    error = RemoconConnectionError("flat endpoint unavailable")
    with patch.object(
        client,
        "_request",
        side_effect=[
            RemoconConnectionError("legacy 500"),
            RemoconConnectionError("BSB 500"),
            error,
        ],
    ):
        with pytest.raises(RemoconConnectionError) as exc_info:
            client.get_data()

    assert exc_info.value is error


def test_flat_fallback_requires_an_item_used_by_this_integration():
    client = make_client()
    irrelevant = {"ok": True, "data": {"items": [{"id": "UnrelatedItem", "value": 1}]}}
    with patch.object(client, "_request", side_effect=[
        RemoconConnectionError("legacy 500"),
        RemoconConnectionError("BSB 500"),
        irrelevant,
    ]):
        with pytest.raises(RemoconDataError, match="no items used"):
            client._get_raw()

    assert client.is_flat_api is False

    client = make_client()
    no_value = {"ok": True, "data": {"items": [{"id": "ZoneComfortTemp", "value": None}]}}
    with patch.object(client, "_request", return_value=no_value):
        with pytest.raises(RemoconDataError, match="no items used"):
            client._get_raw()


def test_flat_status_group_is_optional_and_supplies_heat_pump_state():
    client = make_client()
    client._flat_api = True
    essential = {"ok": True, "data": {"items": [
        {"id": "ZoneComfortTemp", "value": 21},
        {"id": "ZoneEconomyTemp", "value": 18},
    ]}}
    status = {"ok": True, "data": {"items": [
        {"id": "IsHeatingPumpOn", "value": 1},
        {"id": "ZoneHeatRequest", "value": 1},
        {"id": "PlantMode", "value": 1},
    ]}}
    with patch.object(client, "_request", side_effect=[essential, status]) as request:
        data = client.get_data()

    assert request.call_count == 2
    assert request.call_args_list[1].kwargs["json"]["filter"] == {
        "dhw": False,
        "notEssentials": True,
        "plant": False,
        "progId": 9,
        "zone": False,
    }
    assert data.heat_pump_on is True
    assert data.heating_active is True


@pytest.mark.parametrize("status_error", [
    RemoconConnectionError("status down"),
    RemoconDataError("invalid status response"),
])
def test_flat_status_group_error_does_not_reject_essential_data(status_error):
    client = make_client()
    client._flat_api = True
    essential = {"ok": True, "data": {"items": [
        {"id": "ZoneComfortTemp", "value": 21},
    ]}}
    with patch.object(
        client, "_request", side_effect=[essential, status_error]
    ):
        data = client.get_data()

    assert data.comfort_temp == 21
    assert data.heating_active is None
    assert data.cooling_active is None
    assert data.heat_pump_on is None


@pytest.mark.parametrize("response", [
    {"ok": False, "message": "rejected", "data": {"items": [{"id": "ZoneMode"}]}},
    {"ok": True, "data": {"items": []}},
    {"ok": True, "data": {"items": [None, {"value": 1}]}},
])
def test_invalid_flat_fallback_response_is_rejected(response):
    client = make_client()
    with patch.object(client, "_request", side_effect=[
        RemoconConnectionError("legacy 500"),
        RemoconConnectionError("BSB 500"),
        response,
    ]):
        with pytest.raises(RemoconDataError):
            client._get_raw()

    assert client.is_flat_api is False
