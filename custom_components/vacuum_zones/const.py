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

# Базовые переводы (fallback для случаев, когда переводы недоступны)
VALUE_TO_LABEL = {
    CONF_FAN_LEVEL: {
        "1": "1 — Бесшумный",
        "2": "2 — Стандартный",
        "3": "3 — Интенсивный",
        "4": "4 — Турбо",
    },
    CONF_WATER_LEVEL: {
        "0": "0 — Выкл",
        "1": "1",
        "2": "2",
        "3": "3",
    },
    CONF_CLEAN_MODE: {
        "1": "1 — Уборка пыли",
        "3": "3 — Пыль + влажная",
        "4": "4 — Пыль перед влажной",
        "2": "2 — Влажная",
    },
    CONF_CLEAN_TIMES: {
        "1": "1",
        "2": "2",
    },
    CONF_MOP_MODE: {
        "0": "0",
        "1": "1",
    },
}

PARAM_TO_NAME = {
    CONF_FAN_LEVEL: "Уровень всасывания",
    CONF_WATER_LEVEL: "Уровень воды",
    CONF_CLEAN_MODE: "Режим уборки",
    CONF_CLEAN_TIMES: "Повторов уборки",
    CONF_MOP_MODE: "Режим мытья пола",
    CONF_ON: "Комната включена",
    CONF_ROOM_ID: "ID комнаты",
}

# Автоматически генерируем PARAMS из VALUE_TO_LABEL
PARAMS = {param: list(values.keys()) for param, values in VALUE_TO_LABEL.items()}

# Порядок отображения параметров
PARAM_ORDER = {
    CONF_ZONES: "0",
    CONF_CLEAN_TIMES: "1",
    CONF_MOP_MODE: "2", 
    CONF_CLEAN_MODE: "3",
    CONF_FAN_LEVEL: "4",
    CONF_WATER_LEVEL: "5",
}

# Задержка перед выполнением уборки для сбора всех запусков (в секундах)
DELAY_BEFORE_CLEAN = 5


