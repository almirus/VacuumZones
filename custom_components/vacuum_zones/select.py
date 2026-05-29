from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from .entry_data import (
    get_entity_id,
    get_zone_subentry,
    iter_zone_configs,
    async_add_zone_entities,
)
from .const import (
    DOMAIN,
    CONF_ZONES,
    CONF_FAN_LEVEL,
    CONF_WATER_LEVEL,
    CONF_CLEAN_MODE,
    CONF_CLEAN_TIMES,
    CONF_MOP_MODE,
    VALUE_TO_LABEL,
    PARAM_TO_NAME,
    PARAMS,
    PARAM_ORDER,
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
        sorted_params = sorted(PARAMS.items(), key=lambda x: PARAM_ORDER.get(x[0], "9"))
        zone_entities: list[SelectEntity] = []
        for param, raw_options in sorted_params:
            labels = VALUE_TO_LABEL[param]
            options = list(labels.values())
            raw_value = str(cfg.get(param, raw_options[0]))
            value = labels.get(raw_value, options[0])
            zone_entities.append(
                ZoneParamSelect(
                    entry=entry,
                    zone_id=zone_id,
                    param=param,
                    options=options,
                    value=value,
                    device_identifier=device_identifier,
                    device_name=device_name,
                )
            )
        async_add_zone_entities(async_add_entities, zone_entities, subentry_id)


class ZoneParamSelect(SelectEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: ConfigEntry,
        zone_id: str,
        param: str,
        options: list[str],
        value: str,
        device_identifier: str,
        device_name: str,
    ) -> None:
        self._entry = entry
        self._zone_id = zone_id
        self._param = param
        order = PARAM_ORDER.get(param, "9")
        self._attr_name = PARAM_TO_NAME[param] 
        self._attr_options = options
        self._attr_current_option = value
        order = PARAM_ORDER.get(param, "9")
        self._attr_unique_id = f"{device_identifier}_{param}"
        # Используем entity_id для сортировки в UI
        self._attr_entity_id = f"select.{device_identifier}_{order}_{param}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_identifier)},
            name=device_name,
            manufacturer="VacuumZones",
            model="Zone Controller",
        )

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        self._attr_current_option = option
        self.async_write_ha_state()

        # Find raw value by label
        labels = VALUE_TO_LABEL[self._param]
        raw_option = next((k for k, v in labels.items() if v == option), None)
        if raw_option is None:
            return

        subentry = get_zone_subentry(self._entry, self._zone_id)
        if subentry:
            data = dict(subentry.data)
            data[self._param] = int(raw_option)
            self.hass.config_entries.async_update_subentry(
                self._entry, subentry, data=data
            )
        else:
            data = dict(self._entry.data)
            zones = dict(data.get(CONF_ZONES, {}))
            if self._zone_id not in zones:
                return
            zones[self._zone_id][self._param] = int(raw_option)
            data[CONF_ZONES] = zones
            self.hass.config_entries.async_update_entry(self._entry, data=data)
        await self.hass.config_entries.async_reload(self._entry.entry_id)
