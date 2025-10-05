"""Constants for Vacuum Zones integration."""

from homeassistant.helpers import area_registry

DOMAIN = "vacuum_zones"

CONF_ZONES = "zones"
CONF_ROOM_NAME = "room_name"
CONF_ROOM_ID = "room_id"


# Дополнительные параметры комнаты
CONF_CLEAN_TIMES = "clean_times"
CONF_FAN_LEVEL = "fan_level"
CONF_WATER_LEVEL = "water_level"
CONF_CLEAN_MODE = "clean_mode"
CONF_MOP_MODE = "mop_mode"
CONF_ON = "on"

# Стандартные комнаты для fallback
DEFAULT_ROOMS = [
    "Гостиная", "Спальня", "Кухня", "Ванная", "Прихожая", 
    "Детская", "Кабинет", "Балкон", "Коридор", "Кладовка"
]


async def get_available_zones(hass):
    """Получить список доступных зон из Home Assistant areas."""
    try:
        ar = area_registry.async_get(hass)
        areas = ar.async_list_areas()
        available_zones = [area.name for area in areas if area.name]
        print(f"[VacuumZones DEBUG] Получено areas: {len(areas)}")
        print(f"[VacuumZones DEBUG] Названия areas: {[area.name for area in areas]}")
        print(f"[VacuumZones DEBUG] Доступные зоны: {available_zones}")
        
        if not available_zones:
            print(f"[VacuumZones DEBUG] Используем DEFAULT_ROOMS")
            return DEFAULT_ROOMS
            
        return available_zones
    except Exception as e:
        print(f"[VacuumZones DEBUG] Ошибка получения areas: {e}")
        print(f"[VacuumZones DEBUG] Используем DEFAULT_ROOMS")
        return DEFAULT_ROOMS
