from __future__ import annotations

import json
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_ENTITY_ID, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import entity_registry, selector
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN, CONF_ZONES, CONF_ROOM_NAME, CONF_ROOM_ID, CONF_REPEATS, DEFAULT_ROOMS


async def get_available_zones(hass):
    """Получить список доступных зон с отладкой."""
    try:
        # Пробуем получить areas через area registry
        from homeassistant.helpers import area_registry
        ar = area_registry.async_get(hass)
        areas = ar.async_list_areas()
        available_zones = [area.name for area in areas if area.name]
        
        # Отладочная информация
        print(f"[VacuumZones DEBUG] Получено areas: {len(areas)}")
        print(f"[VacuumZones DEBUG] Названия areas: {[area.name for area in areas]}")
        print(f"[VacuumZones DEBUG] Доступные зоны: {available_zones}")
        
        if not available_zones:
            print(f"[VacuumZones DEBUG] Список зон пустой, используем DEFAULT_ROOMS")
            return DEFAULT_ROOMS
            
        return available_zones
        
    except Exception as e:
        # Если не удалось получить areas, используем стандартные комнаты
        print(f"[VacuumZones DEBUG] Ошибка получения areas: {e}")
        print(f"[VacuumZones DEBUG] Используем DEFAULT_ROOMS")
        return DEFAULT_ROOMS


class VacuumZonesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Vacuum Zones."""

    VERSION = 1

    def __init__(self):
        self.data = {}
        self.zones = {}
        self.room_info = None

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            # Проверяем, что сущность пылесоса существует
            entity_reg = entity_registry.async_get(self.hass)
            if not entity_reg.async_get(user_input[CONF_ENTITY_ID]):
                errors[CONF_ENTITY_ID] = "entity_not_found"
            else:
                self.data[CONF_ENTITY_ID] = user_input[CONF_ENTITY_ID]
                
                # Получаем информацию о комнатах из атрибута пылеса
                vacuum_state = self.hass.states.get(user_input[CONF_ENTITY_ID])
                if vacuum_state:
                    # Получаем атрибут vacuum_extend.room_info напрямую
                    room_info_str = vacuum_state.attributes.get("vacuum_extend.room_info")
                    if room_info_str:
                        try:
                            self.room_info = json.loads(room_info_str)
                            print(f"[VacuumZones DEBUG] Получена информация о комнатах: {self.room_info}")
                        except (json.JSONDecodeError, TypeError) as e:
                            print(f"[VacuumZones DEBUG] Ошибка парсинга room_info: {e}")
                            self.room_info = None
                    else:
                        print(f"[VacuumZones DEBUG] vacuum_extend.room_info не найден")
                        self.room_info = None
                
                print(f"[VacuumZones DEBUG] Переходим к async_step_add_zone")
                return await self.async_step_add_zone()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="vacuum")
                ),
            }),
            errors=errors,
        )


    async def async_step_add_zone(self, user_input=None) -> FlowResult:
        """Handle adding a new zone."""
        errors = {}

        if user_input is not None:
            zone_name = user_input[CONF_NAME]
            zone_id = zone_name.lower().replace(" ", "_")
            
            # Проверяем уникальность ID зоны
            if zone_id in self.data.get(CONF_ZONES, {}):
                errors[CONF_NAME] = "zone_exists"
            else:
                # Инициализируем CONF_ZONES если его нет
                if CONF_ZONES not in self.data:
                    self.data[CONF_ZONES] = {}
                
                self.data[CONF_ZONES][zone_id] = {
                    CONF_NAME: zone_name,
                    CONF_ROOM_ID: user_input.get(CONF_ROOM_ID, ""),
                    CONF_REPEATS: user_input.get(CONF_REPEATS, 1)
                }
                
                # После добавления зоны завершаем конфигурацию
                return self.async_create_entry(
                    title=f"Vacuum Zones - {self.data[CONF_ENTITY_ID]}",
                    data=self.data,
                )

        # Получаем список всех зон из Home Assistant areas
        print(f"[VacuumZones DEBUG] Получаем доступные зоны...")
        available_zones = await get_available_zones(self.hass)
        print(f"[VacuumZones DEBUG] Получены зоны: {available_zones}")
        
        # Исключаем уже добавленные зоны
        existing_zones = self.data.get(CONF_ZONES, {}).keys()
        available_zones = [zone for zone in available_zones if zone.lower().replace(" ", "_") not in existing_zones]
        print(f"[VacuumZones DEBUG] Доступные зоны после фильтрации: {available_zones}")

        print(f"[VacuumZones DEBUG] Показываем форму add_zone")
        return self.async_show_form(
            step_id="add_zone",
            data_schema=vol.Schema({
                vol.Required(CONF_NAME): vol.In(available_zones) if available_zones else str,
                vol.Optional(CONF_ROOM_ID, default=""): str,
                vol.Optional(CONF_REPEATS, default=1): vol.All(int, vol.Range(min=1, max=10)),
            }),
            errors=errors,
        )


    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return VacuumZonesOptionsFlowHandler(config_entry)


class VacuumZonesOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Vacuum Zones."""

    def __init__(self, config_entry):
        self.config_entry = config_entry
        self.data = dict(config_entry.data)
        self.zones = dict(self.data.get(CONF_ZONES, {}))

    async def async_step_init(self, user_input=None) -> FlowResult:
        """Handle options flow."""
        if user_input is not None:
            if user_input.get("add_zone"):
                return await self.async_step_add_zone()
            elif user_input.get("edit_zone"):
                zone_id = user_input["zone_to_edit"]
                # Сохраняем zone_id для редактирования
                self._edit_zone_id = zone_id
                # Показываем форму редактирования сразу
                zone_config = self.zones[zone_id]
                return self.async_show_form(
                    step_id="edit_zone",
                    data_schema=vol.Schema({
                        vol.Required(CONF_REPEATS, default=zone_config.get(CONF_REPEATS, 1)): vol.All(int, vol.Range(min=1, max=10)),
                    }),
                    description_placeholders={
                        "zone_name": zone_config.get(CONF_NAME, zone_id),
                        "room_id": zone_config.get(CONF_ROOM_ID, "N/A"),
                    },
                )
            elif user_input.get("delete_zone"):
                zone_id = user_input["zone_to_delete"]
                del self.zones[zone_id]
                return await self.async_step_init()
            elif user_input.get("finish"):
                self.data[CONF_ZONES] = self.zones
                return self.async_create_entry(title="", data=self.data)

        # Показываем список текущих зон
        zones_list = []
        for zone_id, zone_config in self.zones.items():
            zones_list.append(f"{zone_config.get(CONF_NAME, zone_id)} (ID: {zone_config.get(CONF_ROOM_ID, 'N/A')}, Повторений: {zone_config.get(CONF_REPEATS, 1)})")

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional("add_zone", default=False): bool,
                vol.Optional("zone_to_edit"): vol.In([zone_id for zone_id in self.zones.keys()]) if self.zones else None,
                vol.Optional("edit_zone", default=False): bool,
                vol.Optional("zone_to_delete"): vol.In([zone_id for zone_id in self.zones.keys()]) if self.zones else None,
                vol.Optional("delete_zone", default=False): bool,
                vol.Optional("finish", default=True): bool,
            }),
            description_placeholders={
                "entity_id": self.data[CONF_ENTITY_ID],
                "zones_list": "\n".join(zones_list) if zones_list else "Нет настроенных зон",
            },
        )

    async def async_step_add_zone(self, user_input=None) -> FlowResult:
        """Handle adding a new zone."""
        errors = {}

        if user_input is not None:
            zone_name = user_input[CONF_NAME]
            zone_id = zone_name.lower().replace(" ", "_")
            
            if zone_id in self.zones:
                errors[CONF_NAME] = "zone_exists"
            else:
                self.zones[zone_id] = {
                    CONF_NAME: zone_name,
                    CONF_ROOM_ID: user_input.get(CONF_ROOM_ID, ""),
                    CONF_REPEATS: user_input.get(CONF_REPEATS, 1)
                }
                return await self.async_step_init()

        # Получаем список всех зон из Home Assistant areas
        available_zones = await get_available_zones(self.hass)

        return self.async_show_form(
            step_id="add_zone",
            data_schema=vol.Schema({
                vol.Required(CONF_NAME): vol.In(available_zones),
                vol.Optional(CONF_ROOM_ID, default=""): str,
                vol.Optional(CONF_REPEATS, default=1): vol.All(int, vol.Range(min=1, max=10)),
            }),
            errors=errors,
        )

    async def async_step_edit_zone(self, user_input=None) -> FlowResult:
        """Handle editing an existing zone."""
        errors = {}
        
        # Обрабатываем результат формы редактирования
        if user_input is not None and CONF_REPEATS in user_input:
            zone_config = self.zones[self._edit_zone_id]
            zone_config[CONF_REPEATS] = user_input.get(CONF_REPEATS, 1)
            delattr(self, "_edit_zone_id")
            return await self.async_step_init()
        
        # Показываем форму редактирования
        if hasattr(self, "_edit_zone_id"):
            zone_config = self.zones[self._edit_zone_id]
            return self.async_show_form(
                step_id="edit_zone",
                data_schema=vol.Schema({
                    vol.Required(CONF_REPEATS, default=zone_config.get(CONF_REPEATS, 1)): vol.All(int, vol.Range(min=1, max=10)),
                }),
                description_placeholders={
                    "zone_name": zone_config.get(CONF_NAME, self._edit_zone_id),
                    "room_id": zone_config.get(CONF_ROOM_ID, "N/A"),
                },
                errors=errors,
            )

