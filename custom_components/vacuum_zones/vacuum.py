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
import asyncio
import json
import logging
import time
import yaml

from .clean_batch import VacuumCleanBatch, async_get_or_create_batch
from .entry_data import (
    get_entity_id,
    iter_zone_configs,
    async_add_zone_entities,
)
from .room_order import (
    format_room_ids,
    format_room_order,
    parse_room_rows_from_room_info,
)
from .log_trace import trace, trace_action
from .zone_config import prepare_zone_config
from .const import (
    DOMAIN,
    CONF_ROOM_ID,
    CONF_CLEAN_TIMES,
    CONF_FAN_LEVEL,
    CONF_WATER_LEVEL,
    CONF_CLEAN_MODE,
    CONF_MOP_MODE,
    CONF_ON,
    MIOT_DELAY_AFTER_SET_ROOMS,
    MIOT_DELAY_BEFORE_SET_ROOMS,
    MIOT_ROOM_INFO_MAX_WAIT,
    MIOT_ROOM_INFO_POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


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

def _miot_action_params(json_payload: str) -> list[str]:
    """params для xiaomi_miot.call_action — список с JSON-строкой."""
    return [json_payload]


def _log_miot_action(
    _level: int,
    msg: str,
    entity_id: str,
    siid: int,
    aiid: int,
    **extra,
) -> None:
    parts = [f"{entity_id} siid={siid} aiid={aiid}"]
    for key, val in extra.items():
        parts.append(f"{key}={val}")
    trace(_LOGGER, "%s | %s", msg, " ".join(parts))


def _expected_enabled_room_ids(room_attrs: list[dict]) -> list[int]:
    return [int(r["id"]) for r in room_attrs if r.get("on", True)]


def _vacuum_room_info(hass, entity_id: str):
    state = hass.states.get(entity_id)
    if not state:
        return None
    val = state.attributes.get("vacuum_extend.room_info")
    if val is not None:
        return val
    for key, attr_val in state.attributes.items():
        if key.endswith("room_info") and attr_val is not None:
            return attr_val
    return None


async def _async_ensure_room_info(hass, entity_id: str):
    """Дождаться vacuum_extend.room_info (облако xiaomi_miot отдаёт с задержкой)."""
    info = _vacuum_room_info(hass, entity_id)
    if parse_room_rows_from_room_info(info):
        trace(
            _LOGGER,
            "%s: vacuum_extend.room_info уже в состоянии",
            entity_id,
        )
        return info

    deadline = time.monotonic() + MIOT_ROOM_INFO_MAX_WAIT
    while time.monotonic() < deadline:
        await hass.services.async_call(
            "homeassistant",
            "update_entity",
            {ATTR_ENTITY_ID: entity_id},
            True,
        )
        await asyncio.sleep(MIOT_ROOM_INFO_POLL_INTERVAL)
        info = _vacuum_room_info(hass, entity_id)
        if parse_room_rows_from_room_info(info):
            trace(
                _LOGGER,
                "%s: vacuum_extend.room_info получен после опроса",
                entity_id,
            )
            return info

    _LOGGER.warning(
        "%s: vacuum_extend.room_info не появился за %s с — "
        "откройте карту в Mi Home, обновите пылесос в HA",
        entity_id,
        int(MIOT_ROOM_INFO_MAX_WAIT),
    )
    return _vacuum_room_info(hass, entity_id)


async def _async_miot_set_room_attrs(
    hass,
    entity_id: str,
    domain: str,
    room_attrs: list[dict],
    *,
    retries: int = 1,
) -> bool:
    """MIOT set-room-clean-configs: siid=2, aiid=10. Возвращает True при успехе."""
    if not room_attrs:
        _LOGGER.warning("set-room-clean-configs: пустой room_attrs для %s", entity_id)
        return False
    room_attrs_str = json.dumps(
        {"room_attrs": room_attrs}, ensure_ascii=False, separators=(",", ":")
    )
    action_data = {
        ATTR_ENTITY_ID: entity_id,
        "siid": 2,
        "aiid": 10,
        "params": _miot_action_params(room_attrs_str),
    }
    _log_miot_action(
        logging.INFO,
        "set-room-clean-configs",
        entity_id,
        2,
        10,
        order=format_room_order(room_attrs),
        ids=_expected_enabled_room_ids(room_attrs),
    )
    trace(_LOGGER, "set-room-clean-configs payload: %s", room_attrs_str)

    last_error: Exception | None = None
    trace_action(
        _LOGGER,
        "miot",
        entity=entity_id,
        domain=domain,
        service="call_action",
        data=action_data,
    )
    for attempt in range(1, retries + 1):
        try:
            await hass.services.async_call(
                domain, "call_action", action_data, True
            )
            _log_miot_action(
                logging.INFO,
                "set-room-clean-configs OK",
                entity_id,
                2,
                10,
                attempt=attempt,
            )
            return True
        except Exception as err:
            last_error = err
            err_text = str(err).lower()
            if attempt < retries and (
                "no response" in err_text or "timeout" in err_text
            ):
                _LOGGER.warning(
                    "set-room-clean-configs попытка %s/%s не удалась: %s",
                    attempt,
                    retries,
                    err,
                )
                await asyncio.sleep(2.0)
                continue
            _LOGGER.error(
                "set-room-clean-configs ошибка (попытка %s/%s): %s",
                attempt,
                retries,
                err,
            )
            break
    if last_error:
        _LOGGER.error(
            "set-room-clean-configs отклонён, комнаты=%s",
            format_room_order(room_attrs),
        )
    return False


async def _async_miot_start_custom_sweep(
    hass,
    entity_id: str,
    domain: str,
) -> bool:
    """Старт «Собственного режима» после aiid=10: siid=6 aiid=7 (параметры из room_attrs)."""
    try:
        _log_miot_action(
            logging.INFO, "start-custom-sweep", entity_id, 6, 7
        )
        custom_data = {
            ATTR_ENTITY_ID: entity_id,
            "siid": 6,
            "aiid": 7,
            "params": [],
        }
        trace_action(
            _LOGGER,
            "miot",
            entity=entity_id,
            domain=domain,
            service="call_action",
            data=custom_data,
        )
        await hass.services.async_call(domain, "call_action", custom_data, True)
        trace(_LOGGER, "Кастомная уборка запущена (siid=6 aiid=7) на %s", entity_id)
        return True
    except Exception as err:
        _LOGGER.error("start-custom-sweep (siid=6 aiid=7) ошибка: %s", err)
        return False


async def _async_miot_start_room_sweep(
    hass,
    entity_id: str,
    domain: str,
    room_ids: list[int],
) -> bool:
    """Старт по списку комнат: siid=2 aiid=13 (без кастомных room_attrs — только id)."""
    if not room_ids:
        return False
    room_str = json.dumps({"room": room_ids}, ensure_ascii=False, separators=(",", ":"))
    try:
        _log_miot_action(
            logging.INFO,
            "start-vacuum-room-sweep",
            entity_id,
            2,
            13,
            room=room_ids,
        )
        sweep_data = {
            ATTR_ENTITY_ID: entity_id,
            "siid": 2,
            "aiid": 13,
            "params": [room_str],
        }
        trace_action(
            _LOGGER,
            "miot",
            entity=entity_id,
            domain=domain,
            service="call_action",
            data=sweep_data,
        )
        await hass.services.async_call(
            domain,
            "call_action",
            sweep_data,
            True,
        )
        trace(
            _LOGGER,
            "Уборка по комнатам (aiid=13) запущена: %s",
            format_room_ids(room_ids),
        )
        return True
    except Exception as err:
        _LOGGER.error("start-vacuum-room-sweep (aiid=13) ошибка: %s", err)
        return False


async def _async_miot_start_custom_clean(
    hass,
    entity_id: str,
    domain: str,
    room_attrs: list[dict],
) -> bool:
    """MIOT S20+ собственный режим: aiid=10 (room_attrs) → siid=6 aiid=7."""
    trace(_LOGGER, "MIOT: подготовка для %s", entity_id)
    await _async_ensure_room_info(hass, entity_id)
    expected_ids = _expected_enabled_room_ids(room_attrs)
    trace(
        _LOGGER,
        "MIOT: room_attrs (%s шт.), включённые id=%s, порядок: %s",
        len(room_attrs),
        expected_ids,
        format_room_order(room_attrs),
    )

    await hass.services.async_call(
        "homeassistant",
        "update_entity",
        {ATTR_ENTITY_ID: entity_id},
        True,
    )
    await asyncio.sleep(MIOT_DELAY_BEFORE_SET_ROOMS)

    trace(_LOGGER, "MIOT: отправка set-room-clean-configs (aiid=10)")
    set_ok = await _async_miot_set_room_attrs(
        hass, entity_id, domain, room_attrs, retries=1
    )
    if not set_ok:
        _LOGGER.warning("MIOT: aiid=10 не удался — старт уборки всё равно продолжим")
    await asyncio.sleep(MIOT_DELAY_AFTER_SET_ROOMS)

    if not expected_ids:
        _LOGGER.warning("MIOT: нет включённых комнат для уборки")
        return False

    trace(
        _LOGGER,
        "MIOT: старт собственного режима siid=6 aiid=7 (комнаты: %s)",
        format_room_ids(expected_ids, room_attrs=room_attrs),
    )
    started = await _async_miot_start_custom_sweep(hass, entity_id, domain)
    if started:
        trace(_LOGGER, "MIOT: уборка запущена (aiid=7)")
    else:
        _LOGGER.error("MIOT: не удалось запустить siid=6 aiid=7")
    return started


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


async def _async_return_parent_to_dock(
    hass, entity_id: str, domain: str | None
) -> bool:
    """Возврат на базу: vacuum.return_to_base, для xiaomi_miot — MIOT-действия."""
    try:
        await hass.services.async_call(
            VACUUM_DOMAIN,
            "return_to_base",
            {ATTR_ENTITY_ID: entity_id},
            True,
        )
        trace(_LOGGER, "return_to_base OK через %s", entity_id)
        return True
    except Exception as err:
        trace(_LOGGER, "return_to_base недоступен для %s: %s", entity_id, err)

    if domain != "xiaomi_miot":
        return False

    for siid, aiid, label in (
        (6, 15, "stop-and-gocharge"),
        (3, 1, "start-charge"),
    ):
        try:
            _log_miot_action(
                logging.INFO, label, entity_id, siid, aiid
            )
            await hass.services.async_call(
                domain,
                "call_action",
                {
                    ATTR_ENTITY_ID: entity_id,
                    "siid": siid,
                    "aiid": aiid,
                    "params": [],
                },
                True,
            )
            trace(_LOGGER, "Возврат на базу (%s) OK: siid=%s aiid=%s", label, siid, aiid)
            return True
        except Exception as err:
            _LOGGER.warning(
                "Возврат на базу %s (siid=%s aiid=%s) ошибка: %s",
                label,
                siid,
                aiid,
                err,
            )
    return False


class _VacuumActivityMixin:
    """HA 2026.1+: activity через _attr_activity (не cached_property базового класса)."""

    @property
    def activity(self):
        if VACUUM_USE_ACTIVITY:
            return getattr(self, "_attr_activity", STATE_IDLE)
        return None


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
    batch: VacuumCleanBatch | None,
    new_state: State,
    context: Context,
) -> None:
    """Сброс статусов зон и очередь (только не-MIOT) при смене состояния пылесоса."""
    state_key = _parent_vacuum_state_key(new_state)
    trace(
        _LOGGER,
        "Пылесос %s: состояние %s",
        new_state.entity_id if new_state else "?",
        state_key,
    )

    if state_key in ("returning", "docked") and batch:
        batch.clear_active()

    for entity in entities:
        if state_key in ("returning", "docked"):
            if _get_vacuum_activity_key(entity) in ("cleaning", "paused"):
                _write_vacuum_activity(entity, STATE_IDLE)
                trace(_LOGGER, "Зона %s: статус сброшен в idle", entity.name)

    if not queue or state_key not in ("returning", "docked"):
        return

    prev: ZoneVacuum = queue.pop(0)
    trace(_LOGGER, "Очередь: завершена зона %s", prev.name)
    await prev.internal_stop()

    if not queue:
        trace(_LOGGER, "Очередь уборки пуста")
        return

    next_: ZoneVacuum = queue[0]
    trace(_LOGGER, "Очередь: следующая зона %s", next_.name)
    await next_.internal_start(context)


async def async_setup_platform(hass, _, async_add_entities, discovery_info=None):
    """Set up platform from YAML configuration."""
    entity_id: str = discovery_info["entity_id"]
    yaml_zones = discovery_info["zones"]
    queue: list[ZoneVacuum] = []
    batch = async_get_or_create_batch(
        hass, vacuum_entity_id=entity_id, yaml_zones=yaml_zones
    )
    entities: list[ZoneVacuum] = []
    for name, config in yaml_zones.items():
        entity = ZoneVacuum(name, config, entity_id, queue, clean_batch=batch)
        batch.register_zone(entity)
        entities.append(entity)
    async_add_entities(entities)

    async def state_changed_event_listener(event: Event):
        if entity_id != event.data.get(ATTR_ENTITY_ID):
            return
        new_state: State = event.data.get("new_state")
        if not new_state:
            return
        await _async_handle_parent_vacuum_state(
            entities, queue, batch, new_state, event.context
        )

    hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed_event_listener)


async def async_setup_entry(hass, config_entry: ConfigEntry, async_add_entities):
    """Set up platform from config entry."""
    entity_id: str = get_entity_id(config_entry)
    queue: list[ZoneVacuum] = []
    batch = async_get_or_create_batch(hass, config_entry=config_entry)
    entities: list[ZoneVacuum] = []

    for zone_id, zone_data, subentry_id in iter_zone_configs(config_entry):
        config = prepare_zone_config(zone_data)
        entity = ZoneVacuum(
            zone_id,
            config,
            entity_id,
            queue,
            clean_batch=batch,
            config_entry=config_entry,
        )
        batch.register_zone(entity)
        entities.append(entity)
        async_add_zone_entities(async_add_entities, [entity], subentry_id)

    async def state_changed_event_listener(event: Event):
        if entity_id != event.data.get(ATTR_ENTITY_ID):
            return
        new_state: State = event.data.get("new_state")
        if not new_state:
            return
        await _async_handle_parent_vacuum_state(
            entities, queue, batch, new_state, event.context
        )

    hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed_event_listener)


class ZoneVacuum(_VacuumActivityMixin, StateVacuumEntity):
    _attr_supported_features = (
        VacuumEntityFeature.START
        | VacuumEntityFeature.STOP
        | VacuumEntityFeature.RETURN_HOME
    )

    domain: str = None
    service: str = None
    script: Script = None
    room_clean_params: dict = None  # Параметры для уборки комнаты
    room_attrs_params: dict = None  # Параметры для сохранения настроек комнаты

    def __init__(
        self,
        name: str,
        config: dict,
        entity_id: str,
        queue: list,
        *,
        clean_batch: VacuumCleanBatch | None = None,
        config_entry: ConfigEntry | None = None,
    ):
        self.zone_id = name
        self._attr_name = config.pop("name", name)
        self.service_data: dict = config | {ATTR_ENTITY_ID: entity_id}
        self.queue = queue
        self._clean_batch = clean_batch
        self._config_entry = config_entry
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
                "params": _miot_action_params(room_attrs_str),
            }

            self.room_attrs_params = room_attrs_data
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
        trace(_LOGGER, "Зона %s: старт уборки", self._attr_name)
        _write_vacuum_activity(self, STATE_CLEANING)

        if self.script:
            await self.script.async_run(context=context)
  
        if self.service:
            trace_action(
                _LOGGER,
                "zone_start",
                entity=self.vacuum_entity_id,
                domain=self.domain,
                service=self.service,
                data=self.service_data,
                extra=f"zone={self._attr_name}",
            )
            try:
                await self.hass.services.async_call(
                    self.domain, self.service, self.service_data, True
                )
            except Exception as e:
                _LOGGER.error(
                    "Зона %s: ошибка %s.%s: %s",
                    self._attr_name,
                    self.domain,
                    self.service,
                    e,
                )

    async def internal_stop(self):
        trace(_LOGGER, "Зона %s: остановка (виртуальная)", self._attr_name)
        _write_vacuum_activity(self, STATE_IDLE)

    async def async_start(self):
        try:
            rid = int(self.service_data.get(CONF_ROOM_ID, 0) or 0)
        except (TypeError, ValueError):
            rid = 0
        trace_action(
            _LOGGER,
            "zone_async_start",
            entity=self.vacuum_entity_id,
            extra=(
                f"zone={self._attr_name} zone_id={self.zone_id} "
                f"room_id={rid or '?'} ha={getattr(self, 'entity_id', '?')} "
                f"miot={bool(self.room_clean_params)}"
            ),
        )
        if self._clean_batch and self.room_clean_params:
            await self._clean_batch.request_start(self)
            return

        if not self.room_clean_params:
            self.queue.append(self)
            parent = self.hass.states.get(self.vacuum_entity_id)
            if len(self.queue) > 1 or _parent_vacuum_state_key(parent) == "cleaning":
                trace(
                    _LOGGER,
                    "Зона %s: в очереди (позиция %s)",
                    self._attr_name,
                    len(self.queue),
                )
                _write_vacuum_activity(self, STATE_PAUSED)
                return
            await self.internal_start(self._context)
            return

        await self.internal_start(self._context)

    async def async_stop(self, **kwargs):
        trace(_LOGGER, "Зона %s: запрос стоп", self._attr_name)

        if self in self.queue:
            self.queue.remove(self)
        for vacuum in self.queue:
            await vacuum.internal_stop()
        self.queue.clear()

        if self._clean_batch and self.room_clean_params:
            await self._clean_batch.stop_cleaning()
            await self.internal_stop()
            return

        parent = self.hass.states.get(self.vacuum_entity_id)
        if _get_vacuum_activity_key(self) in ("cleaning", "paused") or (
            parent
            and _parent_vacuum_state_key(parent)
            in ("cleaning", "paused", "returning")
        ):
            try:
                await _async_stop_parent_vacuum(self.hass, self.vacuum_entity_id)
            except Exception as e:
                _LOGGER.error(
                    "Зона %s: ошибка vacuum.stop: %s", self._attr_name, e
                )

        await self.internal_stop()

    async def async_return_to_base(self, **kwargs) -> None:
        trace(_LOGGER, "Зона %s: запрос возврата на базу", self._attr_name)

        if self in self.queue:
            self.queue.remove(self)
        for vacuum in self.queue:
            await vacuum.internal_stop()
        self.queue.clear()

        if self._clean_batch:
            await self._clean_batch.return_to_dock()
            _write_vacuum_activity(self, STATE_RETURNING)
            return

        ok = await _async_return_parent_to_dock(
            self.hass, self.vacuum_entity_id, self.domain
        )
        if ok:
            _write_vacuum_activity(self, STATE_RETURNING)
        else:
            _LOGGER.error(
                "Зона %s: не удалось отправить возврат на базу", self._attr_name
            )
