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

from .device import apartment_device_info
from .entry_data import (
    get_entity_id,
    get_zones_from_entry,
    iter_zone_configs,
    async_add_zone_entities,
)
from .room_order import (
    build_ordered_room_attrs,
    build_ordered_room_ids,
    build_room_attr,
    get_config_entry_for_vacuum,
)
from .zone_config import prepare_zone_config
from .const import (
    DOMAIN,
    APARTMENT_ZONE_ID,
    APARTMENT_ROOM_ORDER_HINT,
    ATTR_ROOM_ORDER_HINT,
    CONF_ROOM_ID,
    CONF_CLEAN_TIMES,
    CONF_FAN_LEVEL,
    CONF_WATER_LEVEL,
    CONF_CLEAN_MODE,
    CONF_MOP_MODE,
    CONF_ON,
    DEFAULT_APARTMENT_NAME,
)


try:
    from homeassistant.components.vacuum import VacuumActivity

    VACUUM_USE_ACTIVITY = True
    STATE_IDLE = VacuumActivity.IDLE
    STATE_PAUSED = VacuumActivity.PAUSED
    STATE_CLEANING = VacuumActivity.CLEANING
    STATE_RETURNING = VacuumActivity.RETURNING
    STATE_DOCKED = VacuumActivity.DOCKED
except ImportError:
    VACUUM_USE_ACTIVITY = False
    from homeassistant.components.vacuum import (
        STATE_CLEANING,
        STATE_RETURNING,
        STATE_DOCKED,
    )

async def _async_miot_set_room_attrs(
    hass,
    entity_id: str,
    domain: str,
    room_attrs: list[dict],
) -> None:
    """MIOT set-room-clean-configs: siid=2, aiid=10."""
    if not room_attrs:
        return
    room_attrs_str = json.dumps({"room_attrs": room_attrs}, ensure_ascii=False)
    await hass.services.async_call(
        domain,
        "call_action",
        {
            ATTR_ENTITY_ID: entity_id,
            "siid": 2,
            "aiid": 10,
            "params": room_attrs_str,
        },
        True,
    )


async def _async_miot_start_custom_clean(
    hass,
    entity_id: str,
    domain: str,
    room_attrs: list[dict],
) -> None:
    """MIOT S20+ собственный режим: aiid=10 (room_attrs) → aiid=7 (старт)."""
    await _async_miot_set_room_attrs(hass, entity_id, domain, room_attrs)
    await hass.services.async_call(
        domain,
        "call_action",
        {
            ATTR_ENTITY_ID: entity_id,
            "siid": 6,
            "aiid": 7,
            "params": [],
        },
        True,
    )


async def _async_miot_clean_rooms(
    hass,
    entity_id: str,
    domain: str,
    room_attrs: list[dict],
    room_ids: list[int],
) -> None:
    """MIOT одной комнаты: set-room-clean-configs + start-vacuum-room-sweep (aiid=13)."""
    await _async_miot_set_room_attrs(hass, entity_id, domain, room_attrs)
    if room_ids:
        room_str = json.dumps({"room": room_ids}, ensure_ascii=False)
        await hass.services.async_call(
            domain,
            "call_action",
            {
                ATTR_ENTITY_ID: entity_id,
                "siid": 2,
                "aiid": 13,
                "params": [room_str],
            },
            True,
        )


def _vacuum_state_key(state) -> str | None:
    """Нормализованный ключ состояния пылесоса HA."""
    if state is None:
        return None
    if hasattr(state, "value"):
        state = state.value
    key = str(state).lower()
    if key in ("unavailable", "unknown"):
        return None
    if key in ("idle", "cleaning", "paused", "returning", "docked", "error"):
        return key
    return None


def _vacuum_state_from_key(key: str):
    return {
        "idle": STATE_IDLE,
        "cleaning": STATE_CLEANING,
        "paused": STATE_PAUSED,
        "returning": STATE_RETURNING,
        "docked": STATE_DOCKED,
        "error": STATE_IDLE,
    }.get(key)


def _set_vacuum_activity(entity, value) -> None:
    """Установить состояние/activity виртуального пылесоса."""
    if VACUUM_USE_ACTIVITY:
        entity._attr_activity = value
    else:
        entity._attr_state = value


def _get_vacuum_activity_key(entity) -> str | None:
    if VACUUM_USE_ACTIVITY:
        return _vacuum_state_key(getattr(entity, "_attr_activity", None))
    return _vacuum_state_key(getattr(entity, "_attr_state", None))


def _write_vacuum_activity(entity, value) -> None:
    _set_vacuum_activity(entity, value)
    entity.async_write_ha_state()


async def _async_stop_parent_vacuum(hass, entity_id: str) -> None:
    await hass.services.async_call(
        VACUUM_DOMAIN, "stop", {ATTR_ENTITY_ID: entity_id}, True
    )


class _VacuumActivityMixin:
    """HA 2026.1+: activity через _attr_activity (не cached_property базового класса)."""

    @property
    def activity(self):
        if VACUUM_USE_ACTIVITY:
            return getattr(self, "_attr_activity", STATE_IDLE)
        return None


def _sync_apartment_from_parent(apartment: "_ApartmentVacuumBase", parent_state) -> None:
    """Синхронизировать «Квартира» со статусом реального пылесоса."""
    key = _vacuum_state_key(parent_state)
    if not key:
        return
    target = _vacuum_state_from_key(key)
    if target is None or _get_vacuum_activity_key(apartment) == key:
        return
    _write_vacuum_activity(apartment, target)


def _parent_vacuum_state_key(new_state: State) -> str | None:
    """Ключ состояния родительского пылесоса (строка или enum)."""
    if not new_state:
        return None
    key = _vacuum_state_key(new_state.state)
    if key:
        return key
    return _vacuum_state_key(new_state.attributes.get("activity"))


async def _async_handle_parent_vacuum_state(
    entities: list,
    queue: list,
    new_state: State,
    context: Context,
) -> None:
    """Синхронизация «Квартира» и очередь зон при смене состояния пылесоса."""
    state_key = _parent_vacuum_state_key(new_state)

    for entity in entities:
        if isinstance(entity, _ApartmentVacuumBase):
            if state_key:
                _sync_apartment_from_parent(entity, state_key)
            continue
        if state_key in ("returning", "docked"):
            if _get_vacuum_activity_key(entity) in ("cleaning", "paused"):
                _write_vacuum_activity(entity, STATE_IDLE)
                print(f"[VacuumZones DEBUG] Сбросили статус для {entity.name}")

    if not queue or state_key not in ("returning", "docked"):
        return

    prev: ZoneVacuum = queue.pop(0)
    await prev.internal_stop()

    if not queue:
        return

    next_: ZoneVacuum = queue[0]
    await next_.internal_start(context)


async def async_setup_platform(hass, _, async_add_entities, discovery_info=None):
    """Set up platform from YAML configuration."""
    entity_id: str = discovery_info["entity_id"]
    queue: list[ZoneVacuum] = []
    entities = [
        ZoneVacuum(name, config, entity_id, queue)
        for name, config in discovery_info["zones"].items()
    ]
    if entities:
        entities.append(ApartmentVacuumYaml(entity_id, discovery_info["zones"]))
    async_add_entities(entities)

    async def state_changed_event_listener(event: Event):
        if entity_id != event.data.get(ATTR_ENTITY_ID):
            return
        new_state: State = event.data.get("new_state")
        if not new_state:
            return
        await _async_handle_parent_vacuum_state(
            entities, queue, new_state, event.context
        )

    hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed_event_listener)


async def async_setup_entry(hass, config_entry: ConfigEntry, async_add_entities):
    """Set up platform from config entry."""
    entity_id: str = get_entity_id(config_entry)
    queue: list[ZoneVacuum] = []
    entities: list[ZoneVacuum] = []

    for zone_id, zone_data, subentry_id in iter_zone_configs(config_entry):
        config = prepare_zone_config(zone_data)
        entity = ZoneVacuum(zone_id, config, entity_id, queue)
        entities.append(entity)
        async_add_zone_entities(async_add_entities, [entity], subentry_id)

    if entities:
        apartment = ApartmentVacuum(config_entry, entity_id)
        entities.append(apartment)
        async_add_entities([apartment])

    async def state_changed_event_listener(event: Event):
        if entity_id != event.data.get(ATTR_ENTITY_ID):
            return
        new_state: State = event.data.get("new_state")
        if not new_state:
            return
        await _async_handle_parent_vacuum_state(
            entities, queue, new_state, event.context
        )

    hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed_event_listener)


class ZoneVacuum(_VacuumActivityMixin, StateVacuumEntity):
    _attr_supported_features = VacuumEntityFeature.START | VacuumEntityFeature.STOP

    domain: str = None
    service: str = None
    script: Script = None
    room_clean_params: dict = None  # Параметры для уборки комнаты
    room_attrs_params: dict = None  # Параметры для сохранения настроек комнаты

    def __init__(self, name: str, config: dict, entity_id: str, queue: list):
        self.zone_id = name
        self._attr_name = config.pop("name", name)
        self.service_data: dict = config | {ATTR_ENTITY_ID: entity_id}
        self.queue = queue
        zone_slug = self.zone_id.lower().replace(" ", "_")
        self._attr_unique_id = f"{entity_id}_{zone_slug}"
        # Каждая зона должна быть отдельным устройством, иначе смена area применяется ко всем
        device_identifier = f"{entity_id}_{zone_slug}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_identifier)},
            name=f"Vacuum Zones - {name}",
            manufacturer="VacuumZones",
            model="Zone Controller",
        )
        _set_vacuum_activity(self, STATE_IDLE)

    @property
    def vacuum_entity_id(self) -> str:
        return self.service_data[ATTR_ENTITY_ID]

    def get_mi_room_id(self) -> int | None:
        """ID комнаты Xiaomi из room_clean_params."""
        if not self.room_clean_params:
            return None
        try:
            params_str = self.room_clean_params.get("params", [""])[0]
            room_data = json.loads(params_str)
            rooms = room_data.get("room", [])
            if rooms:
                return int(rooms[0])
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
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
            room_attrs_str = json.dumps(room_attrs_payload, ensure_ascii=False)
            room_attrs_data = {
                ATTR_ENTITY_ID: self.vacuum_entity_id,
                "siid": 2,
                "aiid": 10,
                "params": room_attrs_str,
            }
            
            # Сохраняем параметры для последующего использования
            self.room_attrs_params = room_attrs_data
            
            self.service_data = room_attrs_data
            # Вызываем сохранение параметров комнаты    
            await self.hass.services.async_call(
                            self.domain, self.service, self.service_data, True
                        )
            # Параметры для уборки комнаты - сохраняем в room_clean_params
            room_for_clean = {
                "room": [room_id_int]
            }            
            room_for_clean_str = json.dumps(room_for_clean, ensure_ascii=False)
            
            # Сохраняем параметры для последующего запуска уборки
            self.room_clean_params = {
                ATTR_ENTITY_ID: self.vacuum_entity_id,
                "siid": 2,
                "aiid": 13,
                "params": [room_for_clean_str],
            }
            
            self.service_data = self.room_clean_params
            

    async def internal_start(self, context: Context) -> None:
        _write_vacuum_activity(self, STATE_CLEANING)

        if self.script:
            await self.script.async_run(context=context)
  
        if self.service:
            try:
                    await self.hass.services.async_call(
                        self.domain, self.service, self.service_data, True
                    )
                    
            except Exception as e:
                print(f"[VacuumZones DEBUG] Ошибка вызова {self.domain}.{self.service}: {e}")

    async def internal_stop(self):
        _write_vacuum_activity(self, STATE_IDLE)

    async def async_start(self):
        if not self.room_clean_params:
            self.queue.append(self)
            parent = self.hass.states.get(self.vacuum_entity_id)
            if len(self.queue) > 1 or _parent_vacuum_state_key(parent) == "cleaning":
                _write_vacuum_activity(self, STATE_PAUSED)
                return
            await self.internal_start(self._context)
            return

        # MIOT: только своя комната
        vz_entry = get_config_entry_for_vacuum(self.hass, self.vacuum_entity_id)
        room_attr = None
        if vz_entry:
            zones = get_zones_from_entry(vz_entry)
            room_attr = build_room_attr(zones.get(self.zone_id, {}), self.zone_id)
        if not room_attr:
            await self.internal_start(self._context)
            return
        try:
            await _async_miot_clean_rooms(
                self.hass,
                self.vacuum_entity_id,
                self.domain,
                [room_attr],
                [room_attr["id"]],
            )
        except Exception as e:
            print(f"[VacuumZones DEBUG] Уборка {self.name}: {e}")
        _write_vacuum_activity(self, STATE_CLEANING)

    async def async_stop(self, **kwargs):
        if self in self.queue:
            self.queue.remove(self)
        for vacuum in self.queue:
            await vacuum.internal_stop()
        self.queue.clear()

        parent = self.hass.states.get(self.vacuum_entity_id)
        if _get_vacuum_activity_key(self) in ("cleaning", "paused") or (
            parent and _parent_vacuum_state_key(parent) == "cleaning"
        ):
            try:
                await _async_stop_parent_vacuum(self.hass, self.vacuum_entity_id)
            except Exception as e:
                print(f"[VacuumZones DEBUG] Остановка {self.name}: {e}")

        await self.internal_stop()


class _ApartmentVacuumBase(_VacuumActivityMixin, StateVacuumEntity):
    """Пылесос «Квартира»: уборка нескольких комнат по порядку из настроек."""

    _attr_supported_features = VacuumEntityFeature.START | VacuumEntityFeature.STOP

    def __init__(self, entity_id: str) -> None:
        self._vacuum_entity_id = entity_id
        self._attr_name = DEFAULT_APARTMENT_NAME
        self._attr_unique_id = f"{entity_id}_{APARTMENT_ZONE_ID}"
        self._attr_device_info = apartment_device_info(entity_id)
        self.domain = "xiaomi_miot"
        _set_vacuum_activity(self, STATE_IDLE)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {ATTR_ROOM_ORDER_HINT: APARTMENT_ROOM_ORDER_HINT}

    async def async_added_to_hass(self) -> None:
        reg_entry = entity_registry.async_get(self.hass).async_get(
            self._vacuum_entity_id
        )
        if reg_entry and reg_entry.platform == "xiaomi_miot":
            self.domain = "xiaomi_miot"
        parent = self.hass.states.get(self._vacuum_entity_id)
        if parent:
            state_key = _parent_vacuum_state_key(parent)
            if state_key:
                _sync_apartment_from_parent(self, state_key)

    async def async_start(self) -> None:
        room_attrs, _room_ids = self._ordered_rooms()
        if not room_attrs:
            return
        _write_vacuum_activity(self, STATE_CLEANING)
        try:
            await _async_miot_start_custom_clean(
                self.hass, self._vacuum_entity_id, self.domain, room_attrs
            )
        except Exception as e:
            print(f"[VacuumZones DEBUG] {DEFAULT_APARTMENT_NAME}: {e}")

    async def async_stop(self, **kwargs) -> None:
        try:
            await _async_stop_parent_vacuum(self.hass, self._vacuum_entity_id)
        except Exception as e:
            print(f"[VacuumZones DEBUG] Остановка {DEFAULT_APARTMENT_NAME}: {e}")
        _write_vacuum_activity(self, STATE_IDLE)

    def _ordered_rooms(self) -> tuple[list[dict], list[int]]:
        raise NotImplementedError


class ApartmentVacuum(_ApartmentVacuumBase):
    """Квартира для config entry: порядок из «Порядок уборки комнат»."""

    def __init__(self, config_entry: ConfigEntry, entity_id: str) -> None:
        super().__init__(entity_id)
        self._config_entry = config_entry

    def _ordered_rooms(self) -> tuple[list[dict], list[int]]:
        return (
            build_ordered_room_attrs(self._config_entry),
            build_ordered_room_ids(self._config_entry),
        )


class ApartmentVacuumYaml(_ApartmentVacuumBase):
    """Квартира для YAML: все зоны в порядке объявления."""

    def __init__(self, entity_id: str, zones: dict) -> None:
        super().__init__(entity_id)
        self._zones = zones

    def _ordered_rooms(self) -> tuple[list[dict], list[int]]:
        room_attrs: list[dict] = []
        room_ids: list[int] = []
        for zone_id, cfg in self._zones.items():
            if not bool(cfg.get(CONF_ON, True)):
                continue
            item = build_room_attr(cfg, zone_id)
            if item:
                room_attrs.append(item)
                room_ids.append(item["id"])
        return room_attrs, room_ids
