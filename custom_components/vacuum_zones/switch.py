from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.const import ATTR_ENTITY_ID

from .const import DOMAIN, CONF_ZONES, CONF_ON, PARAM_TO_NAME


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = entry.data
    entity_id: str = data[ATTR_ENTITY_ID]
    zones = data.get(CONF_ZONES, {})

    entities: list[SwitchEntity] = []
    for zone_id, cfg in zones.items():
        device_identifier = f"{entity_id}_{zone_id}"
        device_name = f"Vacuum Zones - {cfg.get('name', zone_id)}"
        entities.append(
            ZoneOnSwitch(
                entry=entry,
                zone_id=zone_id,
                is_on=bool(cfg.get(CONF_ON, True)),
                device_identifier=device_identifier,
                device_name=device_name,
            )
        )

    async_add_entities(entities)


class ZoneOnSwitch(SwitchEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = PARAM_TO_NAME[CONF_ON]

    def __init__(
        self,
        entry: ConfigEntry,
        zone_id: str,
        is_on: bool,
        device_identifier: str,
        device_name: str,
    ) -> None:
        self._entry = entry
        self._zone_id = zone_id
        self._attr_is_on = is_on
        self._attr_unique_id = f"{device_identifier}_on"
        # Используем entity_id для сортировки в UI
        self._attr_entity_id = f"switch.{device_identifier}_0_on"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_identifier)},
            name=device_name,
            manufacturer="VacuumZones",
            model="Zone Controller",
        )

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the entity on."""
        self._attr_is_on = True
        self.async_write_ha_state()
        await self._persist()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the entity off."""
        self._attr_is_on = False
        self.async_write_ha_state()
        await self._persist()

    async def _persist(self) -> None:
        data = dict(self._entry.data)
        zones = data.get(CONF_ZONES, {})
        if self._zone_id in zones:
            zones[self._zone_id][CONF_ON] = self._attr_is_on
            data[CONF_ZONES] = zones
            self.hass.config_entries.async_update_entry(self._entry, data=data)
            await self.hass.config_entries.async_reload(self._entry.entry_id)
