"""Config flow v1: one config entry per zone (HA without subentries)."""

from __future__ import annotations

import json

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_ENTITY_ID, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import entity_registry
from homeassistant.helpers.selector import selector

from .const import (
    DOMAIN,
    CONF_ZONES,
    CONF_ROOM_ID,
    DEFAULT_ROOMS,
    CONF_CLEAN_TIMES,
    CONF_FAN_LEVEL,
    CONF_WATER_LEVEL,
    CONF_CLEAN_MODE,
    CONF_MOP_MODE,
    CONF_ON,
    VALUE_TO_LABEL,
    PARAM_TO_NAME,
)
from .config_flow_subentries import (
    _cloud_form_hint,
    _room_id_field,
    _xiaomi_room_options,
    _zone_name_field,
)
from .entry_data import get_vacuum_title, zone_id_from_name


async def get_available_zones(hass: HomeAssistant) -> list[str]:
    try:
        from homeassistant.helpers import area_registry

        ar = area_registry.async_get(hass)
        areas = ar.async_list_areas()
        available_zones = [area.name for area in areas if area.name]
        if available_zones:
            return available_zones
    except Exception as e:
        print(f"[VacuumZones DEBUG] Ошибка получения areas: {e}")
    return DEFAULT_ROOMS


class VacuumZonesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """One config entry per zone."""

    VERSION = 1

    def __init__(self) -> None:
        self.data: dict = {}
        self.room_info = None

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            entity_reg = entity_registry.async_get(self.hass)
            if not entity_reg.async_get(user_input[CONF_ENTITY_ID]):
                errors[CONF_ENTITY_ID] = "entity_not_found"
            else:
                entity_entry = entity_reg.async_get(user_input[CONF_ENTITY_ID])
                if entity_entry and entity_entry.platform == DOMAIN:
                    errors[CONF_ENTITY_ID] = "virtual_vacuum_selected"
                else:
                    self.data[CONF_ENTITY_ID] = user_input[CONF_ENTITY_ID]
                    vacuum_state = self.hass.states.get(user_input[CONF_ENTITY_ID])
                    if vacuum_state:
                        room_info_str = vacuum_state.attributes.get(
                            "vacuum_extend.room_info"
                        )
                        if room_info_str:
                            try:
                                self.room_info = json.loads(room_info_str)
                            except (json.JSONDecodeError, TypeError):
                                self.room_info = None
                    return await self.async_step_add_zone()

        entity_reg = entity_registry.async_get(self.hass)
        virtual_vacuums = [
            eid
            for eid, entity in entity_reg.entities.items()
            if entity.domain == "vacuum" and entity.platform == DOMAIN
        ]

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ENTITY_ID): selector(
                        {
                            "entity": {
                                "domain": "vacuum",
                                "exclude_entities": virtual_vacuums or [],
                            }
                        }
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_add_zone(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            zone_name = user_input[CONF_NAME]
            zone_id = zone_id_from_name(zone_name)

            if zone_id in self.data.get(CONF_ZONES, {}):
                errors[CONF_NAME] = "zone_exists"
            else:
                if CONF_ZONES not in self.data:
                    self.data[CONF_ZONES] = {}
                self.data[CONF_ZONES][zone_id] = {
                    CONF_NAME: zone_name,
                    CONF_ROOM_ID: user_input.get(CONF_ROOM_ID, ""),
                    CONF_CLEAN_TIMES: int(user_input.get(CONF_CLEAN_TIMES, 1)),
                    CONF_FAN_LEVEL: int(user_input.get(CONF_FAN_LEVEL, 2)),
                    CONF_WATER_LEVEL: int(user_input.get(CONF_WATER_LEVEL, 1)),
                    CONF_CLEAN_MODE: int(user_input.get(CONF_CLEAN_MODE, 1)),
                    CONF_MOP_MODE: int(user_input.get(CONF_MOP_MODE, 0)),
                    CONF_ON: user_input.get(CONF_ON, True),
                }
                title = f"{zone_name} — {get_vacuum_title(self.hass, self.data[CONF_ENTITY_ID])}"
                return self.async_create_entry(title=title, data=self.data)

        available_zones = await get_available_zones(self.hass)
        existing_zones = self.data.get(CONF_ZONES, {}).keys()
        available_zones = [
            z for z in available_zones if zone_id_from_name(z) not in existing_zones
        ]

        room_options = _xiaomi_room_options(self.room_info)
        return self.async_show_form(
            step_id="add_zone",
            data_schema=vol.Schema(
                {
                    **_zone_name_field(available_zones),
                    **_room_id_field(room_options),
                    vol.Required(CONF_CLEAN_TIMES, default="1"): selector(
                        {
                            "select": {
                                "options": [
                                    {"label": lbl, "value": val}
                                    for val, lbl in VALUE_TO_LABEL[
                                        CONF_CLEAN_TIMES
                                    ].items()
                                ],
                                "mode": "dropdown",
                            }
                        }
                    ),
                    vol.Optional(CONF_FAN_LEVEL, default="2"): selector(
                        {
                            "select": {
                                "options": [
                                    {"label": lbl, "value": val}
                                    for val, lbl in VALUE_TO_LABEL[
                                        CONF_FAN_LEVEL
                                    ].items()
                                ],
                                "mode": "dropdown",
                            }
                        }
                    ),
                    vol.Optional(CONF_WATER_LEVEL, default="1"): selector(
                        {
                            "select": {
                                "options": [
                                    {"label": lbl, "value": val}
                                    for val, lbl in VALUE_TO_LABEL[
                                        CONF_WATER_LEVEL
                                    ].items()
                                ],
                                "mode": "dropdown",
                            }
                        }
                    ),
                    vol.Optional(CONF_CLEAN_MODE, default="1"): selector(
                        {
                            "select": {
                                "options": [
                                    {"label": lbl, "value": val}
                                    for val, lbl in VALUE_TO_LABEL[
                                        CONF_CLEAN_MODE
                                    ].items()
                                ],
                                "mode": "dropdown",
                            }
                        }
                    ),
                    vol.Optional(CONF_MOP_MODE, default="0"): selector(
                        {
                            "select": {
                                "options": [
                                    {"label": lbl, "value": val}
                                    for val, lbl in VALUE_TO_LABEL[
                                        CONF_MOP_MODE
                                    ].items()
                                ],
                                "mode": "dropdown",
                            }
                        }
                    ),
                    vol.Optional(CONF_ON, default=True): bool,
                }
            ),
            errors=errors,
            description_placeholders={
                "rooms_hint": _cloud_form_hint(self.hass, room_options),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        from .options_flow import VacuumZonesOptionsFlowHandler

        return VacuumZonesOptionsFlowHandler()
