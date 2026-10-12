"""Stable identifiers for gateways, zones, and entities."""

from collections.abc import Iterable
from typing import Any

from .const import CONF_GATEWAY_ID, CONF_ZONE, DEFAULT_ZONE


def config_entry_unique_id(gateway_id: str, zone: str) -> str:
    """Return a unique config entry ID for a gateway zone."""
    return f"{gateway_id}_zone_{zone}"


def entity_unique_id(gateway_id: str, zone: str, key: str) -> str:
    """Return a zone-aware entity ID while preserving zone 1 IDs."""
    if zone == DEFAULT_ZONE:
        return f"{gateway_id}_{key}"
    return f"{gateway_id}_zone_{zone}_{key}"


def has_gateway_zone_entry(entries: Iterable[Any], gateway_id: str, zone: str) -> bool:
    """Check entries for a matching gateway and zone, including legacy entries."""
    return any(
        entry.data.get(CONF_GATEWAY_ID) == gateway_id
        and entry.data.get(CONF_ZONE, DEFAULT_ZONE) == zone
        for entry in entries
    )
