import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.const import CONF_ENTITY_ID, CONF_SEQUENCE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN, CONF_ZONES

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
    # Поддержка старого способа конфигурации через YAML
    if DOMAIN in config:
        hass.async_create_task(
            async_load_platform(hass, "vacuum", DOMAIN, config[DOMAIN], config)
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Vacuum Zones from a config entry."""
    await hass.config_entries.async_forward_entry_setups(entry, ["vacuum"])
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_forward_entry_unload(entry, "vacuum")
