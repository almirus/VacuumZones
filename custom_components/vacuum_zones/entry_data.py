"""Helpers for config entry / subentry data."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_ENTITY_ID

from .const import CONF_ZONES, DOMAIN, SUBENTRY_ZONE


def get_entity_id(entry: ConfigEntry) -> str:
    """Return parent vacuum entity_id from config entry."""
    return entry.data[CONF_ENTITY_ID]


def zone_id_from_name(name: str) -> str:
    """Stable zone id from display name."""
    return name.lower().replace(" ", "_")


def iter_zone_subentries(entry: ConfigEntry):
    """Yield zone subentries."""
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_ZONE:
            yield subentry


def get_zone_subentry(entry: ConfigEntry, zone_id: str) -> ConfigSubentry | None:
    """Find zone subentry by zone_id (unique_id or subentry_id)."""
    for subentry in iter_zone_subentries(entry):
        sid = subentry.unique_id or subentry.subentry_id
        if sid == zone_id:
            return subentry
    return None


def get_zones_from_entry(entry: ConfigEntry) -> dict[str, dict]:
    """Build zones dict {zone_id: config} from subentries (v2) or entry.data (v1)."""
    return {zone_id: zone_data for zone_id, zone_data, _ in iter_zone_configs(entry)}


def iter_zone_configs(entry: ConfigEntry):
    """Yield (zone_id, zone_data, config_subentry_id) for each zone."""
    if entry.subentries:
        for subentry in iter_zone_subentries(entry):
            zone_id = subentry.unique_id or subentry.subentry_id
            yield zone_id, dict(subentry.data), subentry.subentry_id
        return
    for zone_id, zone_data in entry.data.get(CONF_ZONES, {}).items():
        yield zone_id, dict(zone_data), None


def async_add_zone_entities(async_add_entities, entities, subentry_id: str | None) -> None:
    """Register entities under config subentry when supported."""
    if subentry_id:
        async_add_entities(entities, config_subentry_id=subentry_id)
    else:
        async_add_entities(entities)


def get_vacuum_title(hass, entity_id: str) -> str:
    """Friendly title for parent config entry."""
    if state := hass.states.get(entity_id):
        if state.name:
            return state.name
    return entity_id
