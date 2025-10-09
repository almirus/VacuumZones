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
    await hass.config_entries.async_forward_entry_setups(entry, ["vacuum", "select", "switch"])

    async def _update_listener(hass: HomeAssistant, updated_entry: ConfigEntry) -> None:
        # Если опции заполнены — переносим их в data, чтобы платформа читала актуальные значения
        if updated_entry.options:
            # Обновляем запись: переносим options -> data и очищаем options
            hass.config_entries.async_update_entry(
                updated_entry, data=dict(updated_entry.options), options={}
            )
        # Перезагружаем платформу, чтобы обновить service_data
        await hass.config_entries.async_reload(updated_entry.entry_id)

    # Регистрируем слушатель обновлений
    entry.async_on_unload(entry.add_update_listener(_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_forward_entry_unload(entry, "vacuum")
