"""Options flow: порядок уборки комнат (родительская запись)."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import selector

from .const import CONF_ON, CONF_ROOM_ID, CONF_ROOM_ORDER, MAX_ROOM_POSITIONS
from .entry_data import get_entity_id
from .room_order import (
    get_ordered_zone_ids,
    get_zones_with_cloud,
    parse_room_name_map,
)

POSITION_KEY = "room_position_{}"


def _position_field(index: int) -> str:
    return POSITION_KEY.format(index)


def _build_position_schema(
    zones: dict,
    zone_options: list[dict],
    current_order: list[str],
) -> vol.Schema:
    """По одному выпадающему списку на каждую позицию уборки."""
    fields: dict = {}
    zone_ids = list(zones.keys())
    for index in range(1, len(zones) + 1):
        key = _position_field(index)
        default = (
            current_order[index - 1]
            if index - 1 < len(current_order)
            else zone_ids[index - 1]
        )
        fields[vol.Required(key, default=default)] = selector(
            {
                "select": {
                    "options": zone_options,
                    "mode": "dropdown",
                }
            }
        )
    return vol.Schema(fields)


def _cloud_room_names(hass, entity_id: str) -> dict[int, str]:
    """id → room_name из vacuum_extend.room_info пылесоса."""
    state = hass.states.get(entity_id)
    if not state:
        return {}
    room_info = state.attributes.get("vacuum_extend.room_info")
    if room_info is None:
        for key, val in state.attributes.items():
            if key.endswith("room_info") and val is not None:
                room_info = val
                break
    return parse_room_name_map(room_info)


def _zone_select_options(
    zones: dict, name_by_rid: dict[int, str]
) -> list[dict[str, str]]:
    """Подписи combobox: имя из облака, иначе имя зоны HA."""
    options: list[dict[str, str]] = []
    for zid, cfg in zones.items():
        ha_name = str(cfg.get("name", zid))
        rid = 0
        try:
            rid = int(cfg.get(CONF_ROOM_ID, 0) or 0)
        except (TypeError, ValueError):
            pass
        cloud = name_by_rid.get(rid) if rid else None
        if cloud:
            label = f"{cloud} (ID: {rid})" if rid else str(cloud)
        elif rid:
            label = f"{ha_name} (ID: {rid})"
        else:
            label = ha_name
        if not bool(cfg.get(CONF_ON, True)):
            label = f"{label} (выкл.)"
        options.append({"label": label, "value": zid})
    return options


def _format_order_list(
    zones: dict, zone_order: list[str], name_by_rid: dict[int, str]
) -> str:
    parts: list[str] = []
    for zid in zone_order:
        if zid not in zones:
            continue
        cfg = zones[zid]
        rid = 0
        try:
            rid = int(cfg.get(CONF_ROOM_ID, 0) or 0)
        except (TypeError, ValueError):
            pass
        name = name_by_rid.get(rid) if rid else None
        parts.append(str(name or cfg.get("name", zid)))
    return " → ".join(parts) if parts else "—"


def _order_from_positions(user_input: dict, zones: dict) -> tuple[list[str], dict[str, str]]:
    """Собрать порядок zone_id из полей позиций; ошибки при дубликатах."""
    errors: dict[str, str] = {}
    new_order: list[str] = []
    seen: set[str] = set()
    for index in range(1, len(zones) + 1):
        key = _position_field(index)
        zid = user_input.get(key)
        if not zid or zid not in zones:
            continue
        if zid in seen:
            errors[key] = "duplicate"
        else:
            new_order.append(zid)
            seen.add(zid)
    for zid in zones:
        if zid not in new_order:
            new_order.append(zid)
    return new_order, errors


class VacuumZonesOptionsFlowHandler(config_entries.OptionsFlow):
    """Порядок уборки зон на записи пылесоса."""

    async def async_step_init(self, user_input=None) -> FlowResult:
        entry = self.config_entry
        vacuum_entity = get_entity_id(entry)
        await self.hass.services.async_call(
            "homeassistant",
            "update_entity",
            {ATTR_ENTITY_ID: vacuum_entity},
            True,
        )
        name_by_rid = _cloud_room_names(self.hass, vacuum_entity)
        zones = get_zones_with_cloud(self.hass, entry)
        zone_options = _zone_select_options(zones, name_by_rid)
        current_order = get_ordered_zone_ids(entry, zones)
        order_list = _format_order_list(zones, current_order, name_by_rid)

        if user_input is not None:
            new_order, errors = _order_from_positions(user_input, zones)
            if errors:
                return self.async_show_form(
                    step_id="init",
                    data_schema=_build_position_schema(
                        zones, zone_options, current_order
                    ),
                    errors=errors,
                    description_placeholders={
                        "vacuum": vacuum_entity,
                        "order_list": order_list,
                    },
                )
            data = dict(entry.data)
            data[CONF_ROOM_ORDER] = new_order
            self.hass.config_entries.async_update_entry(entry, data=data)
            await self.hass.config_entries.async_reload(entry.entry_id)
            return self.async_create_entry(title="", data={})

        if not zone_options:
            return self.async_abort(reason="no_zones")

        if len(zones) > MAX_ROOM_POSITIONS:
            return self.async_abort(reason="too_many_zones")

        return self.async_show_form(
            step_id="init",
            data_schema=_build_position_schema(zones, zone_options, current_order),
            description_placeholders={
                "vacuum": vacuum_entity,
                "order_list": order_list,
            },
        )
