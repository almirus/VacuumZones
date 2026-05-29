"""Порядок уборки комнат (MIOT room_attrs / room[])."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME

from .const import (
    CONF_CLEAN_MODE,
    CONF_CLEAN_TIMES,
    CONF_FAN_LEVEL,
    CONF_MOP_MODE,
    CONF_ON,
    CONF_ROOM_ID,
    CONF_ROOM_ORDER,
    CONF_WATER_LEVEL,
    DOMAIN,
)
from .entry_data import get_entity_id, get_zones_from_entry, iter_zone_configs


def get_config_entry_for_vacuum(hass, vacuum_entity_id: str) -> ConfigEntry | None:
    """Найти config entry Vacuum Zones по entity_id пылесоса."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if get_entity_id(entry) == vacuum_entity_id:
            return entry
    return None


def get_ordered_zone_ids(entry: ConfigEntry, zones: dict[str, dict] | None = None) -> list[str]:
    """zone_id в порядке уборки; новые зоны — в конец."""
    zones = zones if zones is not None else get_zones_from_entry(entry)
    saved: list[str] = list(entry.data.get(CONF_ROOM_ORDER, []))
    ordered = [zid for zid in saved if zid in zones]
    for zid in zones:
        if zid not in ordered:
            ordered.append(zid)
    return ordered


def order_vacuums_by_zone(entry: ConfigEntry, vacuums: list) -> list:
    """Отсортировать ZoneVacuum по порядку зон из config entry."""
    zones = get_zones_from_entry(entry)
    zone_order = get_ordered_zone_ids(entry, zones)
    by_zone = {v.zone_id: v for v in vacuums}
    ordered = [by_zone[zid] for zid in zone_order if zid in by_zone]
    for v in vacuums:
        if v not in ordered:
            ordered.append(v)
    return ordered


def build_room_attr(cfg: dict[str, Any], zone_id: str) -> dict[str, Any] | None:
    """Один элемент room_attrs для MIOT set-room-clean-configs."""
    try:
        room_id = int(cfg.get(CONF_ROOM_ID, 0) or 0)
    except (TypeError, ValueError):
        return None
    if not room_id:
        return None
    return {
        "id": room_id,
        "room_name": cfg.get(CONF_NAME, zone_id),
        "fan_level": int(cfg.get(CONF_FAN_LEVEL, 2)),
        "water_level": int(cfg.get(CONF_WATER_LEVEL, 1)),
        "clean_mode": int(cfg.get(CONF_CLEAN_MODE, 1)),
        "clean_times": int(cfg.get(CONF_CLEAN_TIMES, 1)),
        "mop_mode": int(cfg.get(CONF_MOP_MODE, 0)),
        "on": bool(cfg.get(CONF_ON, True)),
    }


def _zone_enabled(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get(CONF_ON, True))


def build_ordered_room_attrs(entry: ConfigEntry) -> list[dict[str, Any]]:
    """Все включённые комнаты в порядке уборки для siid=2 aiid=10."""
    zones = get_zones_from_entry(entry)
    result: list[dict[str, Any]] = []
    for zone_id in get_ordered_zone_ids(entry, zones):
        cfg = zones[zone_id]
        if not _zone_enabled(cfg):
            continue
        item = build_room_attr(cfg, zone_id)
        if item:
            result.append(item)
    return result


def build_ordered_room_ids(entry: ConfigEntry) -> list[int]:
    """ID комнат Xiaomi в порядке уборки (только включённые зоны)."""
    zones = get_zones_from_entry(entry)
    room_ids: list[int] = []
    for zone_id in get_ordered_zone_ids(entry, zones):
        cfg = zones[zone_id]
        if not _zone_enabled(cfg):
            continue
        try:
            rid = int(cfg.get(CONF_ROOM_ID, 0) or 0)
        except (TypeError, ValueError):
            continue
        if rid:
            room_ids.append(rid)
    return unique_preserve_order(room_ids)


def unique_preserve_order(room_ids: list[int]) -> list[int]:
    seen: set[int] = set()
    ordered: list[int] = []
    for rid in room_ids:
        if rid not in seen:
            seen.add(rid)
            ordered.append(rid)
    return ordered
