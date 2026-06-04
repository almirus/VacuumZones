import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.const import CONF_ENTITY_ID, CONF_SEQUENCE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.config_entries import ConfigEntry, ConfigSubentryData

from .compat import SUPPORT_SUBENTRIES
from .const import DOMAIN, CONF_ZONES, SUBENTRY_ZONE
from .entry_data import get_vacuum_title

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_ENTITY_ID): cv.entity_id,
                vol.Required(CONF_ZONES): {
                    cv.string: vol.Schema(
                        {
                            vol.Optional("name"): str,
                            vol.Optional("room"): vol.Any(list, int),
                            vol.Optional("zone"): list,
                            vol.Optional("repeats"): int,
                            vol.Optional("goto"): list,
                            vol.Optional(CONF_SEQUENCE): cv.SCRIPT_SCHEMA,
                        },
                        extra=vol.ALLOW_EXTRA,
                    )
                },
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Vacuum Zones component."""
    if DOMAIN in config:
        hass.async_create_task(
            async_load_platform(hass, "vacuum", DOMAIN, config[DOMAIN], config)
        )
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate v1 (one entry per zone) to v2 (subentries per vacuum)."""
    if not SUPPORT_SUBENTRIES or entry.version >= 2:
        return True

    entity_id = entry.data.get(CONF_ENTITY_ID)
    if not entity_id:
        return False

    related = sorted(
        (
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.version < 2 and e.data.get(CONF_ENTITY_ID) == entity_id
        ),
        key=lambda e: e.entry_id,
    )
    if entry.entry_id != related[0].entry_id:
        return True

    zones: dict = {}
    for old_entry in related:
        zones.update(old_entry.data.get(CONF_ZONES, {}))

    subentries: list[ConfigSubentryData] = []
    for zone_id, zone_data in zones.items():
        zone_name = zone_data.get("name", zone_id)
        subentries.append(
            ConfigSubentryData(
                data=dict(zone_data),
                subentry_type=SUBENTRY_ZONE,
                title=zone_name,
                unique_id=zone_id,
            )
        )

    title = get_vacuum_title(hass, entity_id)
    hass.config_entries.async_update_entry(
        entry,
        title=title,
        data={CONF_ENTITY_ID: entity_id},
        version=2,
        unique_id=entity_id,
        subentries=subentries,
    )

    for other in related[1:]:
        await hass.config_entries.async_remove(other.entry_id)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Vacuum Zones from a config entry."""
    await hass.config_entries.async_forward_entry_setups(
        entry, ["vacuum", "select", "switch"]
    )

    async def _update_listener(hass: HomeAssistant, updated_entry: ConfigEntry) -> None:
        if updated_entry.options:
            hass.config_entries.async_update_entry(
                updated_entry, data=dict(updated_entry.options), options={}
            )
        await hass.config_entries.async_reload(updated_entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    from .clean_batch import async_remove_batch

    async_remove_batch(hass, entry)
    return await hass.config_entries.async_unload_platforms(
        entry, ["vacuum", "select", "switch"]
    )
