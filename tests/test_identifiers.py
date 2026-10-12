"""Tests for gateway and zone identifier stability."""

from types import SimpleNamespace

from custom_components.elco_remocon.identifiers import (
    config_entry_unique_id,
    entity_unique_id,
    has_gateway_zone_entry,
)


def test_zone_one_entity_ids_remain_unchanged():
    assert (
        entity_unique_id("GW123", "1", "outside_temp")
        == "GW123_outside_temp"
    )
    assert (
        entity_unique_id("GW123", "1", "heating_active")
        == "GW123_heating_active"
    )


def test_second_zone_entity_ids_are_distinct():
    assert (
        entity_unique_id("GW123", "2", "outside_temp")
        == "GW123_zone_2_outside_temp"
    )
    assert (
        entity_unique_id("GW123", "2", "heating_active")
        == "GW123_zone_2_heating_active"
    )
    assert entity_unique_id("GW123", "1", "outside_temp") != entity_unique_id(
        "GW123", "2", "outside_temp"
    )


def test_config_entry_ids_are_zone_specific():
    assert config_entry_unique_id("GW123", "1") == "GW123_zone_1"
    assert config_entry_unique_id("GW123", "2") == "GW123_zone_2"


def test_existing_legacy_entry_blocks_only_its_gateway_zone():
    legacy_entry = SimpleNamespace(unique_id="GW123", data={"gateway_id": "GW123"})

    assert has_gateway_zone_entry([legacy_entry], "GW123", "1")
    assert not has_gateway_zone_entry([legacy_entry], "GW123", "2")
    assert not has_gateway_zone_entry([legacy_entry], "OTHER", "1")


def test_existing_zone_specific_entry_blocks_same_zone():
    entry = SimpleNamespace(
        unique_id="GW123_zone_2",
        data={"gateway_id": "GW123", "zone": "2"},
    )

    assert has_gateway_zone_entry([entry], "GW123", "2")
    assert not has_gateway_zone_entry([entry], "GW123", "1")
