"""Порядок уборки комнат (MIOT room_attrs / room[])."""

from __future__ import annotations

import json
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

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
from .log_trace import trace

# Зона только на карте пылесоса (нет subentry в HA)
CLOUD_ZONE_PREFIX = "__cloud_"


def cloud_zone_id(room_id: int) -> str:
    return f"{CLOUD_ZONE_PREFIX}{room_id}"


def _vacuum_room_info_attr(hass, entity_id: str):
    state = hass.states.get(entity_id)
    if not state:
        return None
    val = state.attributes.get("vacuum_extend.room_info")
    if val is not None:
        return val
    for key, attr_val in state.attributes.items():
        if key.endswith("room_info") and attr_val is not None:
            return attr_val
    return None


def get_zones_with_cloud(hass, entry: ConfigEntry) -> dict[str, dict]:
    """Зоны HA + комнаты из облака без отдельной зоны (например id=7, on=false)."""
    zones = dict(get_zones_from_entry(entry))
    room_info = _vacuum_room_info_attr(hass, get_entity_id(entry))
    name_by_rid = parse_room_name_map(room_info)
    if not name_by_rid:
        return zones
    device_rows = parse_room_rows_from_room_info(room_info)
    covered: set[int] = set()
    for cfg in zones.values():
        try:
            rid = int(cfg.get(CONF_ROOM_ID, 0) or 0)
        except (TypeError, ValueError):
            continue
        if rid:
            covered.add(rid)
    for rid, name in sorted(name_by_rid.items()):
        if rid in covered:
            continue
        row = device_rows.get(rid, {})
        zones[cloud_zone_id(rid)] = {
            CONF_ROOM_ID: rid,
            CONF_NAME: name,
            CONF_ON: bool(row.get(CONF_ON, False)),
            CONF_FAN_LEVEL: int(row.get(CONF_FAN_LEVEL, 2)),
            CONF_WATER_LEVEL: int(row.get(CONF_WATER_LEVEL, 1)),
            CONF_CLEAN_MODE: int(row.get(CONF_CLEAN_MODE, 1)),
            CONF_CLEAN_TIMES: int(row.get(CONF_CLEAN_TIMES, 1)),
            CONF_MOP_MODE: int(row.get(CONF_MOP_MODE, 0)),
        }
    return zones


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


def _normalize_room_info(room_info):
    if isinstance(room_info, str):
        try:
            return json.loads(room_info)
        except (json.JSONDecodeError, TypeError):
            return None
    return room_info if isinstance(room_info, dict) else None


def parse_room_rows_from_room_info(room_info) -> dict[int, dict[str, Any]]:
    """Все комнаты из vacuum_extend.room_info: id → параметры как на пылесосе."""
    room_info = _normalize_room_info(room_info)
    if not room_info:
        return {}
    rows = room_info.get("room_attrs", [])
    if len(rows) < 2:
        return {}
    header = rows[0]
    if not isinstance(header, (list, tuple)):
        return {}
    index: dict[str, int] = {str(col): i for i, col in enumerate(header)}
    if "id" not in index:
        return {}

    int_fields = (
        CONF_FAN_LEVEL,
        CONF_WATER_LEVEL,
        CONF_CLEAN_MODE,
        CONF_CLEAN_TIMES,
        CONF_MOP_MODE,
    )
    result: dict[int, dict[str, Any]] = {}
    for row in rows[1:]:
        if not isinstance(row, (list, tuple)) or len(row) <= index["id"]:
            continue
        try:
            rid = int(row[index["id"]])
        except (TypeError, ValueError):
            continue
        if not rid:
            continue
        item: dict[str, Any] = {"id": rid}
        if "room_name" in index and len(row) > index["room_name"]:
            item["room_name"] = str(row[index["room_name"]])
        for field in int_fields:
            if field in index and len(row) > index[field]:
                try:
                    item[field] = int(row[index[field]])
                except (TypeError, ValueError):
                    pass
        if CONF_ON in index and len(row) > index[CONF_ON]:
            on_val = row[index[CONF_ON]]
            item[CONF_ON] = on_val in (True, 1, "1", "true")
        result[rid] = item
    return result


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


def merge_room_attr_with_device(
    cfg: dict[str, Any],
    zone_id: str,
    device_row: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Параметры уборки из зоны HA; имя комнаты — с пылесоса при наличии; on — из HA."""
    ha_item = build_room_attr(cfg, zone_id)
    if not ha_item:
        return None
    item = dict(ha_item)
    if device_row and device_row.get("room_name"):
        item["room_name"] = device_row["room_name"]
    item["on"] = bool(cfg.get(CONF_ON, True))
    return item


def _zone_enabled(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get(CONF_ON, True))


def build_ordered_room_attrs(
    entry: ConfigEntry, *, include_disabled: bool = False
) -> list[dict[str, Any]]:
    """Комнаты в порядке уборки для siid=2 aiid=10 (только настройки зон HA)."""
    return _build_ordered_room_attrs_from_zones(
        entry,
        device_rooms=None,
        device_order=None,
        include_disabled=include_disabled,
    )


def build_ordered_room_attrs_for_vacuum(
    hass,
    entry: ConfigEntry,
    vacuum_entity_id: str,
    *,
    include_disabled: bool = False,
) -> list[dict[str, Any]]:
    """room_attrs как Mi Home: порядок/on из HA, остальное из vacuum_extend.room_info."""
    room_info = None
    state = hass.states.get(vacuum_entity_id)
    if state:
        room_info = state.attributes.get("vacuum_extend.room_info")
    device_rooms = parse_room_rows_from_room_info(room_info)
    if not device_rooms:
        _LOGGER.warning(
            "%s: нет vacuum_extend.room_info — параметры из зон HA; "
            "обновите пылесос в HA или откройте карту в Mi Home",
            vacuum_entity_id,
        )
    else:
        trace(
            _LOGGER,
            "%s: room_info с пылососа, комнат=%s",
            vacuum_entity_id,
            len(device_rooms),
        )
    device_order = parse_room_ids_order_from_room_info(room_info)
    zones = get_zones_with_cloud(hass, entry)
    return _build_ordered_room_attrs_from_zones(
        entry,
        zones=zones,
        device_rooms=device_rooms,
        device_order=device_order,
        include_disabled=include_disabled,
    )


def _build_ordered_room_attrs_from_zones(
    entry: ConfigEntry,
    *,
    zones: dict[str, dict] | None = None,
    device_rooms: dict[int, dict[str, Any]] | None,
    device_order: list[int] | None,
    include_disabled: bool,
) -> list[dict[str, Any]]:
    """Комнаты в порядке уборки для siid=2 aiid=10.

    include_disabled: полный список (on=false для выключенных), как в Mi Home.
    """
    zones = zones if zones is not None else get_zones_from_entry(entry)
    result: list[dict[str, Any]] = []
    seen: set[int] = set()

    def append_zone(cfg: dict[str, Any], zone_id: str, *, force_off: bool) -> None:
        try:
            rid = int(cfg.get(CONF_ROOM_ID, 0) or 0)
        except (TypeError, ValueError):
            return
        if not rid or rid in seen:
            return
        device_row = device_rooms.get(rid) if device_rooms else None
        item = merge_room_attr_with_device(cfg, zone_id, device_row)
        if not item:
            return
        if force_off:
            item["on"] = False
        result.append(item)
        seen.add(rid)

    for zone_id in get_ordered_zone_ids(entry, zones):
        cfg = zones[zone_id]
        if not include_disabled and not _zone_enabled(cfg):
            continue
        append_zone(cfg, zone_id, force_off=False)

    if include_disabled:
        for zone_id in get_ordered_zone_ids(entry, zones):
            cfg = zones[zone_id]
            if _zone_enabled(cfg):
                continue
            append_zone(cfg, zone_id, force_off=True)
        if device_rooms:
            tail_order = device_order or list(device_rooms.keys())
            for rid in tail_order:
                if rid in seen:
                    continue
                device_row = device_rooms.get(rid)
                if not device_row:
                    continue
                item = dict(device_row)
                item["on"] = False
                result.append(item)
                seen.add(rid)

    trace(
        _LOGGER,
        "room_attrs: %s комнат, include_disabled=%s, from_device=%s",
        len(result),
        include_disabled,
        bool(device_rooms),
    )
    return result


def zone_id_for_room_id(zones: dict[str, dict], room_id: int) -> str | None:
    """zone_id по CONF_ROOM_ID (включая __cloud_*)."""
    for zid, cfg in zones.items():
        try:
            if int(cfg.get(CONF_ROOM_ID, 0) or 0) == room_id:
                return zid
        except (TypeError, ValueError):
            continue
    cid = cloud_zone_id(room_id)
    return cid if cid in zones else None


def build_room_attrs_for_selected_zones(
    hass,
    entry: ConfigEntry,
    vacuum_entity_id: str,
    selected_zone_ids: list[str] | set[str],
) -> list[dict[str, Any]]:
    """Полный room_attrs в порядке интеграции; on=true только у выбранных зон."""
    selected = set(selected_zone_ids)
    full = build_ordered_room_attrs_for_vacuum(
        hass, entry, vacuum_entity_id, include_disabled=True
    )
    zones = get_zones_with_cloud(hass, entry)
    result: list[dict[str, Any]] = []
    for item in full:
        row = dict(item)
        zid = zone_id_for_room_id(zones, int(row["id"]))
        row["on"] = zid in selected if zid else False
        result.append(row)
    trace(
        _LOGGER,
        "room_attrs (выбрано %s): %s",
        len(selected),
        format_room_order([r for r in result if r.get("on")]),
    )
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


def format_room_order(room_attrs: list[dict]) -> str:
    """Краткая строка порядка: Имя(id) для логов."""
    parts: list[str] = []
    for row in room_attrs:
        name = row.get("room_name") or "?"
        rid = row.get("id", "?")
        if row.get("on", True):
            parts.append(f"{name}({rid})")
        else:
            parts.append(f"{name}({rid},off)")
    return " → ".join(parts) if parts else "—"


def parse_room_name_map(room_info) -> dict[int, str]:
    """id → room_name из vacuum_extend.room_info (облако/пылесос)."""
    return {
        rid: str(row["room_name"])
        for rid, row in parse_room_rows_from_room_info(room_info).items()
        if row.get("room_name")
    }


def name_map_from_room_attrs(room_attrs: list[dict]) -> dict[int, str]:
    """id → room_name из списка room_attrs HA."""
    out: dict[int, str] = {}
    for row in room_attrs:
        try:
            rid = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        out[rid] = str(row.get("room_name") or "?")
    return out


def format_room_ids(
    ids: list[int],
    *,
    name_map: dict[int, str] | None = None,
    room_attrs: list[dict] | None = None,
    room_info=None,
) -> str:
    """Порядок id; при name_map/room_info/room_attrs — «Имя(id)»."""
    labels = dict(name_map or {})
    if room_attrs:
        labels.update(name_map_from_room_attrs(room_attrs))
    if room_info is not None:
        labels.update(parse_room_name_map(room_info))
    if not ids:
        return "—"
    if not labels:
        return " → ".join(str(i) for i in ids)
    parts: list[str] = []
    for rid in ids:
        name = labels.get(rid)
        parts.append(f"{name}({rid})" if name else str(rid))
    return " → ".join(parts)


def parse_room_ids_order_from_room_info(room_info) -> list[int]:
    """Все id комнат в порядке строк vacuum_extend.room_info."""
    room_info = _normalize_room_info(room_info)
    if not room_info:
        return []
    rows = room_info.get("room_attrs", [])
    if len(rows) < 2:
        return []
    header = rows[0]
    if not isinstance(header, (list, tuple)):
        return []
    try:
        id_idx = header.index("id")
    except ValueError:
        return []
    result: list[int] = []
    for row in rows[1:]:
        if not isinstance(row, (list, tuple)) or len(row) <= id_idx:
            continue
        try:
            rid = int(row[id_idx])
        except (TypeError, ValueError):
            continue
        if rid:
            result.append(rid)
    return result


def parse_enabled_room_ids_from_room_info(room_info) -> list[int]:
    """Порядок включённых комнат из vacuum_extend.room_info пылесоса."""
    room_info = _normalize_room_info(room_info)
    if not room_info:
        return []
    rows = room_info.get("room_attrs", [])
    if len(rows) < 2:
        return []
    header = rows[0]
    if not isinstance(header, (list, tuple)):
        return []
    try:
        id_idx = header.index("id")
        on_idx = header.index(CONF_ON)
    except ValueError:
        return []
    result: list[int] = []
    for row in rows[1:]:
        if not isinstance(row, (list, tuple)) or len(row) <= max(id_idx, on_idx):
            continue
        try:
            rid = int(row[id_idx])
        except (TypeError, ValueError):
            continue
        on_val = row[on_idx]
        if on_val in (True, 1, "1", "true"):
            result.append(rid)
    return result


def unique_preserve_order(room_ids: list[int]) -> list[int]:
    seen: set[int] = set()
    ordered: list[int] = []
    for rid in room_ids:
        if rid not in seen:
            seen.add(rid)
            ordered.append(rid)
    return ordered
