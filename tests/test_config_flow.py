"""Tests for multi-zone config flow handling."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.elco_remocon.config_flow import ElcoRemoconConfigFlow


class _Hass:
    async def async_add_executor_job(self, function, *args):
        return function(*args)


class _Client:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def login(self):
        return None

    def get_data(self):
        return {}


def _flow(entries):
    flow = ElcoRemoconConfigFlow()
    flow.hass = _Hass()
    flow.entries = entries
    return flow


def test_legacy_entry_blocks_duplicate_gateway_zone():
    legacy_entry = SimpleNamespace(
        unique_id="GW123", data={"gateway_id": "GW123"}
    )
    flow = _flow([legacy_entry])

    with patch("custom_components.elco_remocon.config_flow.RemoconClient", _Client):
        result = asyncio.run(
            flow.async_step_user(
                {
                    "email": "user@example.com",
                    "password": "secret",
                    "gateway_id": "GW123",
                    "zone": "1",
                }
            )
        )

    assert result == {"type": "abort", "reason": "already_configured"}


def test_new_zone_creates_zone_specific_entry():
    legacy_zone_one = SimpleNamespace(
        unique_id="GW123", data={"gateway_id": "GW123"}
    )
    flow = _flow([legacy_zone_one])

    with patch("custom_components.elco_remocon.config_flow.RemoconClient", _Client):
        result = asyncio.run(
            flow.async_step_user(
                {
                    "email": "user@example.com",
                    "password": "secret",
                    "gateway_id": "GW123",
                    "zone": "2",
                }
            )
        )

    assert result["type"] == "create_entry"
    assert result["unique_id"] == "GW123_zone_2"
    assert result["title"] == "Elco GW123 (Zone 2)"
