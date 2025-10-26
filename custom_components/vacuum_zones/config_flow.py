from __future__ import annotations

import json
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_ENTITY_ID, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import entity_registry
from homeassistant.helpers.selector import selector
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_ZONES,
    CONF_ROOM_NAME,
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


async def get_available_zones(hass):
    """Получить список доступных зон с отладкой."""
    try:
        # Пробуем получить areas через area registry
        from homeassistant.helpers import area_registry
        ar = area_registry.async_get(hass)
        areas = ar.async_list_areas()
        available_zones = [area.name for area in areas if area.name]
        
        
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
                # Дополнительная проверка: исключаем виртуальные пылесосы из vacuum_zones
                entity_entry = entity_reg.async_get(user_input[CONF_ENTITY_ID])
                if entity_entry and entity_entry.platform == 'vacuum_zones':
                    errors[CONF_ENTITY_ID] = "virtual_vacuum_selected"
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
                    
                    return await self.async_step_add_zone()

        # Получаем список всех виртуальных пылесосов для исключения
        entity_reg = entity_registry.async_get(self.hass)
        virtual_vacuums = []
        for entity_id, entity in entity_reg.entities.items():
            if entity.domain == "vacuum" and entity.platform == 'vacuum_zones':
                virtual_vacuums.append(entity_id)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ENTITY_ID): selector({
                    "entity": {
                        "domain": "vacuum",
                        "exclude_entities": virtual_vacuums if virtual_vacuums else [],
                    }
                }),
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
                
                # Собираем конфиг комнаты с дополнительными параметрами
                self.data[CONF_ZONES][zone_id] = {
                    CONF_NAME: zone_name,
                    CONF_ROOM_ID: user_input.get(CONF_ROOM_ID, ""),
                    # clean_times = repeats (1..2)

                    CONF_CLEAN_TIMES: int(user_input.get(CONF_CLEAN_TIMES, 1)),
                    # Доп. параметры
                    CONF_FAN_LEVEL: int(user_input.get(CONF_FAN_LEVEL, 2)),
                    CONF_WATER_LEVEL: int(user_input.get(CONF_WATER_LEVEL, 1)),
                    CONF_CLEAN_MODE: int(user_input.get(CONF_CLEAN_MODE, 1)),
                    CONF_MOP_MODE: int(user_input.get(CONF_MOP_MODE, 0)),
                    CONF_ON: user_input.get(CONF_ON, True),
                }
                
                # После добавления зоны завершаем конфигурацию
                return self.async_create_entry(
                    title=f"{zone_name} - Виртуальный пылесос - {self.data[CONF_ENTITY_ID]}",
                    data=self.data,
                )

        # Получаем список всех зон из Home Assistant areas
        available_zones = await get_available_zones(self.hass)
        print(f"[VacuumZones DEBUG] Получены зоны: {available_zones}")
        
        # Исключаем уже добавленные зоны
        existing_zones = self.data.get(CONF_ZONES, {}).keys()
        available_zones = [zone for zone in available_zones if zone.lower().replace(" ", "_") not in existing_zones]
        print(f"[VacuumZones DEBUG] Доступные зоны после фильтрации: {available_zones}")

        # Подсказка из room_info (если доступна)
        rooms_hint = ""
        try:
            if self.room_info and isinstance(self.room_info, dict):
                room_attrs = self.room_info.get("room_attrs", [])
                if len(room_attrs) > 1:
                    lines = ["Доступные комнаты из облака Xiaomi:"]
                    for row in room_attrs[1:]:
                        if isinstance(row, (list, tuple)) and len(row) >= 2:
                            rid, rname = row[0], row[1]
                            lines.append(f"• ID: {rid}, Название: {rname}")
                    rooms_hint = "\n".join(lines)
        except Exception as e:
            print(f"[VacuumZones DEBUG] Ошибка формирования rooms_hint: {e}")

        return self.async_show_form(
            step_id="add_zone",
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, description="Название комнаты"): vol.In(available_zones) if available_zones else str,
                vol.Required(CONF_ROOM_ID, default="", description=PARAM_TO_NAME[CONF_ROOM_ID]): str,
                # Количество повторов уборки (1 или 2)
                vol.Required(CONF_CLEAN_TIMES, default="1", description=PARAM_TO_NAME[CONF_CLEAN_TIMES]): selector({
                    "select": {
                        "options": [{"label": lbl, "value": val} for val, lbl in VALUE_TO_LABEL[CONF_CLEAN_TIMES].items()],
                        "mode": "dropdown"
                    }
                }),
                # Уровень всасывания
                vol.Optional(CONF_FAN_LEVEL, default="2", description=PARAM_TO_NAME[CONF_FAN_LEVEL]): selector({
                    "select": {
                        "options": [{"label": lbl, "value": val} for val, lbl in VALUE_TO_LABEL[CONF_FAN_LEVEL].items()],
                        "mode": "dropdown"
                    }
                }),
                # Уровень воды
                vol.Optional(CONF_WATER_LEVEL, default="1", description=PARAM_TO_NAME[CONF_WATER_LEVEL]): selector({
                    "select": {
                        "options": [{"label": lbl, "value": val} for val, lbl in VALUE_TO_LABEL[CONF_WATER_LEVEL].items()],
                        "mode": "dropdown"
                    }
                }),
                # Режим уборки
                vol.Optional(CONF_CLEAN_MODE, default="1", description=PARAM_TO_NAME[CONF_CLEAN_MODE]): selector({
                    "select": {
                        "options": [{"label": lbl, "value": val} for val, lbl in VALUE_TO_LABEL[CONF_CLEAN_MODE].items()],
                        "mode": "dropdown"
                    }
                }),
                # Режим мытья пола (mop_mode): 0 или 1
                vol.Optional(CONF_MOP_MODE, default="0", description=PARAM_TO_NAME[CONF_MOP_MODE]): selector({
                    "select": {
                        "options": [{"label": lbl, "value": val} for val, lbl in VALUE_TO_LABEL[CONF_MOP_MODE].items()],
                        "mode": "dropdown"
                    }
                }),
                # Включена ли уборка в комнате
                vol.Optional(CONF_ON, default=True, description=PARAM_TO_NAME[CONF_ON]): bool,
            }),
            errors=errors,
            description_placeholders={"rooms_hint": rooms_hint},
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
                # Показываем форму редактирования сразу (без имени комнаты)
                zone_config = self.zones[zone_id]
                return self.async_show_form(
                    step_id="edit_zone",
                    data_schema=vol.Schema({
                        # clean_times (1/2)
                        vol.Required(CONF_CLEAN_TIMES, default=str(zone_config.get(CONF_CLEAN_TIMES, zone_config.get(CONF_CLEAN_TIMES, 1))), description=PARAM_TO_NAME[CONF_CLEAN_TIMES]): selector({
                            "select": {
                                "options": [{"label": lbl, "value": val} for val, lbl in VALUE_TO_LABEL[CONF_CLEAN_TIMES].items()],
                                "mode": "dropdown"
                            }
                        }),
                        # fan_level
                        vol.Optional(CONF_FAN_LEVEL, default=str(zone_config.get(CONF_FAN_LEVEL, 2)), description=PARAM_TO_NAME[CONF_FAN_LEVEL]): selector({
                            "select": {
                                "options": [{"label": lbl, "value": val} for val, lbl in VALUE_TO_LABEL[CONF_FAN_LEVEL].items()],
                                "mode": "dropdown"
                            }
                        }),
                        # water_level
                        vol.Optional(CONF_WATER_LEVEL, default=str(zone_config.get(CONF_WATER_LEVEL, 1)), description=PARAM_TO_NAME[CONF_WATER_LEVEL]): selector({
                            "select": {
                                "options": [{"label": lbl, "value": val} for val, lbl in VALUE_TO_LABEL[CONF_WATER_LEVEL].items()],
                                "mode": "dropdown"
                            }
                        }),
                        # clean_mode
                        vol.Optional(CONF_CLEAN_MODE, default=str(zone_config.get(CONF_CLEAN_MODE, 1)), description=PARAM_TO_NAME[CONF_CLEAN_MODE]): selector({
                            "select": {
                                "options": [{"label": lbl, "value": val} for val, lbl in VALUE_TO_LABEL[CONF_CLEAN_MODE].items()],
                                "mode": "dropdown"
                            }
                        }),
                        # mop_mode
                        vol.Optional(CONF_MOP_MODE, default=str(zone_config.get(CONF_MOP_MODE, 0)), description=PARAM_TO_NAME[CONF_MOP_MODE]): selector({
                            "select": {
                                "options": [{"label": lbl, "value": val} for val, lbl in VALUE_TO_LABEL[CONF_MOP_MODE].items()],
                                "mode": "dropdown"
                            }
                        }),
                        # on
                        vol.Optional(CONF_ON, default=bool(zone_config.get(CONF_ON, True)), description=PARAM_TO_NAME[CONF_ON]): bool,
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
            zones_list.append(f"{zone_config.get(CONF_NAME, zone_id)} (ID: {zone_config.get(CONF_ROOM_ID, 'N/A')}, Повторений: {zone_config.get(CONF_CLEAN_TIMES, 1)})")

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
                    CONF_CLEAN_TIMES: int(user_input.get(CONF_CLEAN_TIMES, 1)),
                    CONF_FAN_LEVEL: int(user_input.get(CONF_FAN_LEVEL, 2)),
                    CONF_WATER_LEVEL: int(user_input.get(CONF_WATER_LEVEL, 1)),
                    CONF_CLEAN_MODE: int(user_input.get(CONF_CLEAN_MODE, 1)),
                    CONF_MOP_MODE: int(user_input.get(CONF_MOP_MODE, 0)),
                    CONF_ON: bool(user_input.get(CONF_ON, True)),
                }
                return await self.async_step_init()

        # Получаем список всех зон из Home Assistant areas
        available_zones = await get_available_zones(self.hass)

        return self.async_show_form(
            step_id="add_zone",
            data_schema=vol.Schema({
                vol.Required(CONF_NAME): vol.In(available_zones),
                vol.Required(CONF_ROOM_ID, default=""): str,
                # Количество повторов уборки (1 или 2)
                vol.Required(CONF_CLEAN_TIMES, default="1"): selector({
                    "select": {
                        "options": [{"label": lbl, "value": val} for val, lbl in VALUE_TO_LABEL[CONF_CLEAN_TIMES].items()],
                        "mode": "dropdown"
                    }
                }),
                # Уровень всасывания
                vol.Optional(CONF_FAN_LEVEL, default="2"): selector({
                    "select": {
                        "options": [{"label": lbl, "value": val} for val, lbl in VALUE_TO_LABEL[CONF_FAN_LEVEL].items()],
                        "mode": "dropdown"
                    }
                }),
                # Уровень воды
                vol.Optional(CONF_WATER_LEVEL, default="1"): selector({
                    "select": {
                        "options": [{"label": lbl, "value": val} for val, lbl in VALUE_TO_LABEL[CONF_WATER_LEVEL].items()],
                        "mode": "dropdown"
                    }
                }),
                # Режим уборки
                vol.Optional(CONF_CLEAN_MODE, default="1"): selector({
                    "select": {
                        "options": [{"label": lbl, "value": val} for val, lbl in VALUE_TO_LABEL[CONF_CLEAN_MODE].items()],
                        "mode": "dropdown"
                    }
                }),
                # Режим мытья пола (mop_mode): 0 или 1
                vol.Optional(CONF_MOP_MODE, default="0"): selector({
                    "select": {
                        "options": [{"label": lbl, "value": val} for val, lbl in VALUE_TO_LABEL[CONF_MOP_MODE].items()],
                        "mode": "dropdown"
                    }
                }),
                # Включена ли уборка в комнате
                vol.Optional(CONF_ON, default=True): bool,
            }),
            errors=errors,
        )

    async def async_step_edit_zone(self, user_input=None) -> FlowResult:
        """Handle editing an existing zone."""
        errors = {}
        
        # Обрабатываем результат формы редактирования
        if user_input is not None and (CONF_CLEAN_TIMES in user_input or CONF_ON in user_input):
            zone_config = self.zones[self._edit_zone_id]
            # Обновляем значения (конвертируем строки в int)
            clean_times = int(user_input.get(CONF_CLEAN_TIMES, zone_config.get(CONF_CLEAN_TIMES, zone_config.get(CONF_CLEAN_TIMES, 1))))
            zone_config[CONF_CLEAN_TIMES] = clean_times
            zone_config[CONF_FAN_LEVEL] = int(user_input.get(CONF_FAN_LEVEL, zone_config.get(CONF_FAN_LEVEL, 2)))
            zone_config[CONF_WATER_LEVEL] = int(user_input.get(CONF_WATER_LEVEL, zone_config.get(CONF_WATER_LEVEL, 1)))
            zone_config[CONF_CLEAN_MODE] = int(user_input.get(CONF_CLEAN_MODE, zone_config.get(CONF_CLEAN_MODE, 1)))
            zone_config[CONF_MOP_MODE] = int(user_input.get(CONF_MOP_MODE, zone_config.get(CONF_MOP_MODE, 0)))
            zone_config[CONF_ON] = bool(user_input.get(CONF_ON, zone_config.get(CONF_ON, True)))
            delattr(self, "_edit_zone_id")
            return await self.async_step_init()
        
        # Если по какой-то причине пришли без _edit_zone_id — вернемся на init
        return await self.async_step_init()

