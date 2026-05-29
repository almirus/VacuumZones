"""Config flow v2: one entry per vacuum, zones as subentries (HA 2025.2+)."""

from __future__ import annotations

import json
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntry,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_ENTITY_ID, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import entity_registry
from homeassistant.helpers.selector import selector

from .const import (
    DOMAIN,
    CONF_ROOM_ID,
    CLOUD_ROOMS_WAIT_MSG,
    DEFAULT_ROOMS,
    CONF_CLEAN_TIMES,
    CONF_FAN_LEVEL,
    CONF_WATER_LEVEL,
    CONF_CLEAN_MODE,
    CONF_MOP_MODE,
    CONF_ON,
    VALUE_TO_LABEL,
    PARAM_TO_NAME,
    SUBENTRY_ZONE,
)
from .entry_data import (
    get_vacuum_title,
    iter_zone_subentries,
    zone_id_from_name,
)


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


def _xiaomi_room_options(room_info: dict | None) -> list[dict[str, str]]:
    """Список комнат из vacuum_extend.room_info для combobox ID."""
    if not room_info or not isinstance(room_info, dict):
        return []
    room_attrs = room_info.get("room_attrs", [])
    options: list[dict[str, str]] = []
    for row in room_attrs[1:]:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            rid, rname = row[0], row[1]
            options.append(
                {"label": f"{rname} (ID: {rid})", "value": str(rid)}
            )
    return options


def _room_id_field(
    room_options: list[dict[str, str]], defaults: dict[str, Any] | None = None
) -> dict:
    """ID комнаты Xiaomi — combobox из облака или текст, если списка нет."""
    defaults = defaults or {}
    default_rid = str(defaults.get(CONF_ROOM_ID, "") or "")
    if room_options:
        values = [o["value"] for o in room_options]
        if default_rid not in values:
            default_rid = values[0]
        return {
            vol.Required(CONF_ROOM_ID, default=default_rid): selector(
                {
                    "select": {
                        "options": room_options,
                        "mode": "dropdown",
                    }
                }
            )
        }
    return {vol.Optional(CONF_ROOM_ID, default=default_rid): str}


def _zone_name_field(
    available_zones: list[str], defaults: dict[str, Any] | None = None
) -> dict:
    """Комната из HA (area) — выпадающий список."""
    defaults = defaults or {}
    default_name = defaults.get(CONF_NAME)
    if available_zones:
        if default_name not in available_zones:
            default_name = available_zones[0]
        return {
            vol.Required(CONF_NAME, default=default_name): selector(
                {
                    "select": {
                        "options": [{"label": z, "value": z} for z in available_zones],
                        "mode": "dropdown",
                    }
                }
            )
        }
    return {vol.Required(CONF_NAME, default=default_name or ""): str}


def _zone_data_schema(
    available_zones: list[str],
    room_options: list[dict[str, str]],
    defaults: dict[str, Any] | None = None,
) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            **_zone_name_field(available_zones, defaults),
            **_room_id_field(room_options, defaults),
            vol.Required(
                CONF_CLEAN_TIMES, default=str(defaults.get(CONF_CLEAN_TIMES, 1))
            ): selector(
                {
                    "select": {
                        "options": [
                            {"label": lbl, "value": val}
                            for val, lbl in VALUE_TO_LABEL[CONF_CLEAN_TIMES].items()
                        ],
                        "mode": "dropdown",
                    }
                }
            ),
            vol.Optional(
                CONF_FAN_LEVEL, default=str(defaults.get(CONF_FAN_LEVEL, 2))
            ): selector(
                {
                    "select": {
                        "options": [
                            {"label": lbl, "value": val}
                            for val, lbl in VALUE_TO_LABEL[CONF_FAN_LEVEL].items()
                        ],
                        "mode": "dropdown",
                    }
                }
            ),
            vol.Optional(
                CONF_WATER_LEVEL, default=str(defaults.get(CONF_WATER_LEVEL, 1))
            ): selector(
                {
                    "select": {
                        "options": [
                            {"label": lbl, "value": val}
                            for val, lbl in VALUE_TO_LABEL[CONF_WATER_LEVEL].items()
                        ],
                        "mode": "dropdown",
                    }
                }
            ),
            vol.Optional(
                CONF_CLEAN_MODE, default=str(defaults.get(CONF_CLEAN_MODE, 1))
            ): selector(
                {
                    "select": {
                        "options": [
                            {"label": lbl, "value": val}
                            for val, lbl in VALUE_TO_LABEL[CONF_CLEAN_MODE].items()
                        ],
                        "mode": "dropdown",
                    }
                }
            ),
            vol.Optional(
                CONF_MOP_MODE, default=str(defaults.get(CONF_MOP_MODE, 0))
            ): selector(
                {
                    "select": {
                        "options": [
                            {"label": lbl, "value": val}
                            for val, lbl in VALUE_TO_LABEL[CONF_MOP_MODE].items()
                        ],
                        "mode": "dropdown",
                    }
                }
            ),
            vol.Optional(CONF_ON, default=defaults.get(CONF_ON, True)): bool,
        }
    )


def _build_zone_config(user_input: dict[str, Any]) -> dict[str, Any]:
    zone_name = user_input[CONF_NAME]
    return {
        CONF_NAME: zone_name,
        CONF_ROOM_ID: user_input.get(CONF_ROOM_ID, ""),
        CONF_CLEAN_TIMES: int(user_input.get(CONF_CLEAN_TIMES, 1)),
        CONF_FAN_LEVEL: int(user_input.get(CONF_FAN_LEVEL, 2)),
        CONF_WATER_LEVEL: int(user_input.get(CONF_WATER_LEVEL, 1)),
        CONF_CLEAN_MODE: int(user_input.get(CONF_CLEAN_MODE, 1)),
        CONF_MOP_MODE: int(user_input.get(CONF_MOP_MODE, 0)),
        CONF_ON: user_input.get(CONF_ON, True),
    }


def _existing_zone_ids(entry: ConfigEntry) -> set[str]:
    return {
        subentry.unique_id or subentry.subentry_id
        for subentry in iter_zone_subentries(entry)
    }


def _cloud_form_hint(hass: HomeAssistant, room_options: list) -> str:
    """Подсказка только если список комнат из облака ещё не загружен."""
    if room_options:
        return ""
    lang = (hass.config.language or "en").split("-")[0]
    return CLOUD_ROOMS_WAIT_MSG.get(lang, CLOUD_ROOMS_WAIT_MSG["en"])


class VacuumZonesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """One config entry per vacuum."""

    VERSION = 2

    def __init__(self) -> None:
        self._room_info: dict | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        from .options_flow import VacuumZonesOptionsFlowHandler

        return VacuumZonesOptionsFlowHandler()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_ZONE: ZoneSubentryFlowHandler}

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
                    entity_id = user_input[CONF_ENTITY_ID]
                    await self.async_set_unique_id(entity_id)
                    self._abort_if_unique_id_configured()
                    title = get_vacuum_title(self.hass, entity_id)
                    return self.async_create_entry(
                        title=title,
                        data={CONF_ENTITY_ID: entity_id},
                    )

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


class ZoneSubentryFlowHandler(ConfigSubentryFlow):
    """Add or reconfigure a zone subentry."""

    def __init__(self) -> None:
        self._room_info: dict | None = None

    def _load_room_info(self) -> None:
        entity_id = self._get_entry().data[CONF_ENTITY_ID]
        vacuum_state = self.hass.states.get(entity_id)
        if not vacuum_state:
            return
        room_info_str = vacuum_state.attributes.get("vacuum_extend.room_info")
        if not room_info_str:
            return
        try:
            self._room_info = json.loads(room_info_str)
        except (json.JSONDecodeError, TypeError):
            self._room_info = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_zone_step(user_input, reconfigure=False)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_zone_step(user_input, reconfigure=True)

    async def _async_zone_step(
        self,
        user_input: dict[str, Any] | None,
        *,
        reconfigure: bool,
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_entry()

        if user_input is not None:
            zone_name = user_input[CONF_NAME]
            zone_id = zone_id_from_name(zone_name)

            if reconfigure:
                subentry = self._get_reconfigure_subentry()
                zone_id = subentry.unique_id or subentry.subentry_id
            elif zone_id in _existing_zone_ids(entry):
                errors[CONF_NAME] = "zone_exists"

            if not errors:
                zone_config = _build_zone_config(user_input)
                if reconfigure:
                    subentry = self._get_reconfigure_subentry()
                    self.hass.config_entries.async_update_subentry(
                        entry,
                        subentry,
                        data=zone_config,
                        title=zone_name,
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(reason="reconfigure_successful")
                return self.async_create_entry(
                    title=zone_name,
                    data=zone_config,
                    unique_id=zone_id,
                )

        available_zones = await get_available_zones(self.hass)
        defaults = None
        room_options: list[dict[str, str]] = []

        if reconfigure:
            subentry = self._get_reconfigure_subentry()
            defaults = dict(subentry.data)
            zone_name = defaults.get(CONF_NAME, subentry.title)
            available_zones = [zone_name] if zone_name else available_zones
            self._load_room_info()
            room_options = _xiaomi_room_options(self._room_info)
        else:
            existing = _existing_zone_ids(entry)
            available_zones = [
                z for z in available_zones if zone_id_from_name(z) not in existing
            ]
            self._load_room_info()
            room_options = _xiaomi_room_options(self._room_info)

        step_id = "reconfigure" if reconfigure else "user"
        return self.async_show_form(
            step_id=step_id,
            data_schema=_zone_data_schema(
                available_zones, room_options, defaults
            ),
            errors=errors,
            description_placeholders={
                "rooms_hint": _cloud_form_hint(self.hass, room_options),
            },
        )
