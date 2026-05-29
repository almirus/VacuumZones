from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN, CONF_ON, CONF_ZONES, PARAM_TO_NAME
from .entry_data import (
    get_entity_id,
    get_zone_subentry,
    iter_zone_configs,
    async_add_zone_entities,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entity_id: str = get_entity_id(entry)

    for zone_id, cfg, subentry_id in iter_zone_configs(entry):
        device_identifier = f"{entity_id}_{zone_id}"
        device_name = f"Vacuum Zones - {cfg.get('name', zone_id)}"
        async_add_zone_entities(
            async_add_entities,
            [
                ZoneOnSwitch(
                    entry=entry,
                    zone_id=zone_id,
                    is_on=bool(cfg.get(CONF_ON, True)),
                    device_identifier=device_identifier,
                    device_name=device_name,
                )
            ],
            subentry_id,
        )


class ZoneOnSwitch(SwitchEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = PARAM_TO_NAME[CONF_ON]  # Используем человекочитаемое название

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
        subentry = get_zone_subentry(self._entry, self._zone_id)
        if subentry:
            data = dict(subentry.data)
            data[CONF_ON] = self._attr_is_on
            self.hass.config_entries.async_update_subentry(
                self._entry, subentry, data=data
            )
        else:
            data = dict(self._entry.data)
            zones = dict(data.get(CONF_ZONES, {}))
            if self._zone_id not in zones:
                return
            zones[self._zone_id][CONF_ON] = self._attr_is_on
            data[CONF_ZONES] = zones
            self.hass.config_entries.async_update_entry(self._entry, data=data)
        await self.hass.config_entries.async_reload(self._entry.entry_id)
