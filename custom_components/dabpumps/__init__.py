"""__init__.py: The DAB Pumps integration."""
from __future__ import annotations

import logging
import json
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.translation import async_get_translations

from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
)

from .coordinator import (
    get_dabpumpscoordinator,
    DabPumpsCoordinator
)

from .const import (
    STARTUP_MESSAGE,
    DOMAIN,
    API,
    COORDINATOR,
    CONF_INSTALL_ID,
    CONF_INSTALL_NAME,
)


_LOGGER = logging.getLogger(__name__)
_LOGGER.info(STARTUP_MESSAGE)


PLATFORMS: list[Platform] = [
    Platform.SENSOR
]


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the component."""
    hass.data[DOMAIN] = {
        API: {},        # key is username+hash(password)
        COORDINATOR: {} # key is install_id
    }

    for entry in hass.config_entries.async_entries(DOMAIN):
        if not isinstance(entry.unique_id, str):
            hass.config_entries.async_update_entry(
                entry, unique_id=str(entry.unique_id)
            )
    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up DAB Pumps from a config entry."""
    
    # Get properties from the config_entry
    username = config_entry.data[CONF_USERNAME]
    password = config_entry.data[CONF_PASSWORD]
    install_id = config_entry.data[CONF_INSTALL_ID]
    install_name = config_entry.data[CONF_INSTALL_NAME]
    options = config_entry.options

    _LOGGER.debug(f"Setup config entry for installation '{install_name}' ({install_id})")

    # Get an instance of the DabPumpsCoordinator for this install_id
    coordinator = get_dabpumpscoordinator(hass, config_entry)
    
    # Fetch initial data so we have data when entities subscribe
    #
    # If the refresh fails, async_config_entry_first_refresh will
    # raise ConfigEntryNotReady and setup will try again later
    #
    await coordinator.async_config_entry_first_refresh()
    
    # Forward to all platforms (sensor, switch, ...)
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    
    return True


async def async_update_options(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Update options."""
    await hass.config_entries.async_reload(config_entry.entry_id)


async def options_update_listener(hass: core.HomeAssistant, config_entry: config_entries.ConfigEntry):
    """Handle options update."""
    await hass.config_entries.async_reload(config_entry.entry_id)
