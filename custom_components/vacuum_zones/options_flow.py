"""Options flow: порядок уборки комнат (родительская запись)."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import selector

from .const import CONF_ROOM_ORDER, MAX_ROOM_POSITIONS
from .entry_data import get_entity_id, iter_zone_configs
from .room_order import get_ordered_zone_ids

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
        zones = {zid: cfg for zid, cfg, _ in iter_zone_configs(entry)}
        zone_options = [
            {"label": str(cfg.get("name", zid)), "value": zid}
            for zid, cfg in zones.items()
        ]
        current_order = get_ordered_zone_ids(entry, zones)

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
                        "vacuum": get_entity_id(entry),
                        "order_list": " → ".join(
                            str(zones[z].get("name", z))
                            for z in current_order
                            if z in zones
                        )
                        or "—",
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
                "vacuum": get_entity_id(entry),
                "order_list": " → ".join(
                    str(zones[z].get("name", z)) for z in current_order if z in zones
                )
                or "—",
            },
        )
