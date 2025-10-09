from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumEntityFeature,
    DOMAIN as VACUUM_DOMAIN,
)
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.const import (
    CONF_SEQUENCE,
    CONF_NAME,
    STATE_IDLE,
    STATE_PAUSED,
    EVENT_STATE_CHANGED,
    ATTR_ENTITY_ID,
)
from homeassistant.core import Context, Event, State
from homeassistant.helpers import entity_registry
from homeassistant.helpers.script import Script
from homeassistant.config_entries import ConfigEntry
import json
import yaml

from .const import (
    DOMAIN,
    CONF_ZONES,
    CONF_ROOM_ID,
    CONF_CLEAN_TIMES,
    CONF_FAN_LEVEL,
    CONF_WATER_LEVEL,
    CONF_CLEAN_MODE,
    CONF_MOP_MODE,
    CONF_ON,
)


try:
    # trying to import new constants from VacuumActivity HA Core 2026.1
    from homeassistant.components.vacuum import VacuumActivity

    STATE_CLEANING = VacuumActivity.CLEANING
    STATE_RETURNING = VacuumActivity.RETURNING
    STATE_DOCKED = VacuumActivity.DOCKED
except ImportError:
    # if the new constants are unavailable, use the old ones
    from homeassistant.components.vacuum import (
        STATE_CLEANING,
        STATE_RETURNING,
        STATE_DOCKED,
    )


async def async_setup_platform(hass, _, async_add_entities, discovery_info=None):
    """Set up platform from YAML configuration."""
    entity_id: str = discovery_info["entity_id"]
    queue: list[ZoneVacuum] = []
    entities = [
        ZoneVacuum(name, config, entity_id, queue)
        for name, config in discovery_info["zones"].items()
    ]
    async_add_entities(entities)

    async def state_changed_event_listener(event: Event):
        if entity_id != event.data.get(ATTR_ENTITY_ID) or not queue:
            return

        new_state: State = event.data.get("new_state")
        if new_state.state not in (STATE_RETURNING, STATE_DOCKED):
            return

        prev: ZoneVacuum = queue.pop(0)
        await prev.internal_stop()

        if not queue:
            return

        next_: ZoneVacuum = queue[0]
        await next_.internal_start(event.context)

    hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed_event_listener)


async def async_setup_entry(hass, config_entry: ConfigEntry, async_add_entities):
    """Set up platform from config entry."""
    data = config_entry.data
    entity_id: str = data[ATTR_ENTITY_ID]
    queue: list[ZoneVacuum] = []
    
    # Парсим конфигурацию зон
    zones_config = {}
    for zone_id, zone_data in data[CONF_ZONES].items():
        config = dict(zone_data)
        
        # Парсим JSON строки если они есть
        if isinstance(config.get("zone"), str):
            try:
                config["zone"] = json.loads(config["zone"])
            except (json.JSONDecodeError, TypeError):
                pass
                
        if isinstance(config.get("goto"), str):
            try:
                config["goto"] = json.loads(config["goto"])
            except (json.JSONDecodeError, TypeError):
                pass
                
        if isinstance(config.get(CONF_SEQUENCE), str):
            try:
                config[CONF_SEQUENCE] = yaml.safe_load(config[CONF_SEQUENCE])
            except (yaml.YAMLError, TypeError):
                pass
        
        zones_config[zone_id] = config
    
    entities = [
        ZoneVacuum(name, config, entity_id, queue)
        for name, config in zones_config.items()
    ]
    async_add_entities(entities)

    async def state_changed_event_listener(event: Event):
        if entity_id != event.data.get(ATTR_ENTITY_ID) or not queue:
            return

        new_state: State = event.data.get("new_state")
        if new_state.state not in (STATE_RETURNING, STATE_DOCKED):
            return

        prev: ZoneVacuum = queue.pop(0)
        await prev.internal_stop()

        if not queue:
            return

        next_: ZoneVacuum = queue[0]
        await next_.internal_start(event.context)

    hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed_event_listener)


class ZoneVacuum(StateVacuumEntity):
    _attr_state = STATE_IDLE
    _attr_supported_features = VacuumEntityFeature.START | VacuumEntityFeature.STOP

    domain: str = None
    service: str = None
    script: Script = None

    def __init__(self, name: str, config: dict, entity_id: str, queue: list):
        self._attr_name = config.pop("name", name)
        self.service_data: dict = config | {ATTR_ENTITY_ID: entity_id}
        self.queue = queue
        # Добавляем уникальный идентификатор для возможности управления через UI
        zone_slug = name.lower().replace(" ", "_")
        self._attr_unique_id = f"{entity_id}_{zone_slug}"
        # Каждая зона должна быть отдельным устройством, иначе смена area применяется ко всем
        device_identifier = f"{entity_id}_{zone_slug}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_identifier)},
            name=f"Vacuum Zones - {name}",
            manufacturer="VacuumZones",
            model="Zone Controller",
        )

    @property
    def vacuum_entity_id(self) -> str:
        return self.service_data[ATTR_ENTITY_ID]

    @property
    def activity(self):  # HA 2026.1+
        """Return current activity using VacuumActivity enum when available.

        Сохраняет совместимость со старыми версиями HA, где enum отсутствует.
        """
        # Если константы являются строками (старые версии HA), просто не объявляем activity
        if isinstance(STATE_CLEANING, str):
            return None

        # На новых версиях константы уже являются VacuumActivity
        current = self._attr_state
        if current == STATE_CLEANING:
            return STATE_CLEANING
        if current == STATE_RETURNING:
            return STATE_RETURNING
        if current == STATE_DOCKED:
            return STATE_DOCKED
        return None

    async def async_added_to_hass(self):
        # init start script
        if sequence := self.service_data.pop(CONF_SEQUENCE, None):
            self.script = Script(self.hass, sequence, self.name, VACUUM_DOMAIN)

        # get entity domain
        # https://github.com/home-assistant/core/blob/dev/homeassistant/components/xiaomi_miio/services.yaml
        # https://github.com/Tasshack/dreame-vacuum/blob/master/custom_components/dreame_vacuum/services.yaml
        # https://github.com/humbertogontijo/homeassistant-roborock/blob/main/custom_components/roborock/services.yaml
        entry = entity_registry.async_get(self.hass).async_get(self.vacuum_entity_id)
        self.domain = entry.platform

        # migrate service field names
        if room := self.service_data.pop("room", None):
            self.service_data["segments"] = room
        if goto := self.service_data.pop("goto", None):
            self.service_data["x_coord"] = goto[0]
            self.service_data["y_coord"] = goto[1]
        print(f"[VacuumZones DEBUG] ",self.service_data)
        if "segments" in self.service_data:
            # "xiaomi_miio", "dreame_vacuum", "roborock"
            self.service = "vacuum_clean_segment"
        elif "zone" in self.service_data:
            # "xiaomi_miio", "dreame_vacuum", "roborock"
            if self.domain == "xiaomi_miio":
                self.service_data.setdefault("repeats", 1)
            self.service = "vacuum_clean_zone"
        elif "x_coord" in self.service_data and "y_coord" in self.service_data:
            # "xiaomi_miio", "roborock"
            self.service = "vacuum_goto"
        elif "clean_times" in self.service_data:
            # "NEW xiaomi_miio" — формируем параметры для call_action
            self.service = "call_action"
            # Вызов должен идти в домен xiaomi_miot
            self.domain = "xiaomi_miot"
            room_id_val = self.service_data.get(CONF_ROOM_ID)
            try:
                room_id_int = int(room_id_val) if room_id_val not in (None, "") else 0
            except (TypeError, ValueError):
                room_id_int = 0

            room_attrs_payload = {
                "room_attrs": [
                    {
                        "id": room_id_int,
                        "room_name": self._attr_name or self.service_data.get(CONF_NAME, ""),
                        "fan_level": int(self.service_data.get(CONF_FAN_LEVEL, 2)),
                        "water_level": int(self.service_data.get(CONF_WATER_LEVEL, 1)),
                        "clean_mode": int(self.service_data.get(CONF_CLEAN_MODE, 1)),
                        "clean_times": int(self.service_data.get(CONF_CLEAN_TIMES, 1)),
                        "mop_mode": int(self.service_data.get(CONF_MOP_MODE, 0)),
                        "on": bool(self.service_data.get(CONF_ON, True)),
                    }
                ]
            }
            json_str = json.dumps(room_attrs_payload, ensure_ascii=False)
            self.service_data = {
                ATTR_ENTITY_ID: self.vacuum_entity_id,
                "siid": 2,
                "aiid": 10,
                "params": json_str,
            }

    async def internal_start(self, context: Context) -> None:
        self._attr_state = STATE_CLEANING
        self.async_write_ha_state()

        if self.script:
            await self.script.async_run(context=context)
        print(f"[VacuumZones DEBUG] Вызываем {self.domain} {self.service}: {self.service_data}")
        if self.service:
            try:
                result = await self.hass.services.async_call(
                    self.domain, self.service, self.service_data, True
                )
                print(
                    f"[VacuumZones DEBUG] Ответ сервиса {self.domain}.{self.service}: {result}"
                )
            except Exception as e:
                print(
                    f"[VacuumZones DEBUG] Ошибка вызова {self.domain}.{self.service}: {e}"
                )

    async def internal_stop(self):
        self._attr_state = STATE_IDLE
        self.async_write_ha_state()

    async def async_start(self):
        self.queue.append(self)

        state = self.hass.states.get(self.vacuum_entity_id)
        if len(self.queue) > 1 or state == STATE_CLEANING:
            self._attr_state = STATE_PAUSED
            self.async_write_ha_state()
            return

        await self.internal_start(self._context)

    async def async_stop(self, **kwargs):
        for vacuum in self.queue:
            await vacuum.internal_stop()

        self.queue.clear()

        await self.internal_stop()
