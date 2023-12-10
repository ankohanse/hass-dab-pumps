"""__init__.py: The DAB Pumps integration."""
from __future__ import annotations

import logging
import json

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (device_registry as dr, entity_registry)
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.translation import async_get_translations

from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
)

from .dabpumpsapi import DabPumpsApi
from .coordinator import DabPumpsCoordinator

from .const import (
    DOMAIN,
    HUB,
    COORDINATOR,
    PLATFORMS,
    STARTUP_MESSAGE
)


_LOGGER = logging.getLogger(__name__)
_LOGGER.info(STARTUP_MESSAGE)


async def async_setup(hass, config):
    """Set up DAB Pumps components."""
    hass.data.setdefault(DOMAIN, {})

    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up DAB Pumps from a config entry."""
    
    # Store an instance of the DabPumpsApi instance in hass.data[domain]
    options = config_entry.data.get('options', {})
    username = config_entry.data[CONF_USERNAME]
    password = config_entry.data[CONF_PASSWORD]

    dabpumps_api = DabPumpsApi(username, password)
    coordinator = DabPumpsCoordinator(hass, dabpumps_api, options)

    hass.data[DOMAIN][HUB] = dabpumps_api
    hass.data[DOMAIN][COORDINATOR] = coordinator

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
