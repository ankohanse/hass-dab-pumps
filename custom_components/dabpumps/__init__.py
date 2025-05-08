"""__init__.py: The DAB Pumps integration."""
from __future__ import annotations

import logging
import json
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigType
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.translation import async_get_translations

from .coordinator import (
    DabPumpsCoordinatorFactory,
    DabPumpsCoordinator
)

from .const import (
    DOMAIN,
    PLATFORMS,
    CONF_INSTALL_ID,
    CONF_INSTALL_NAME,
)


_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the component."""

    for entry in hass.config_entries.async_entries(DOMAIN):
        if not isinstance(entry.unique_id, str):
            hass.config_entries.async_update_entry(
                entry, unique_id=str(entry.unique_id)
            )
    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up DAB Pumps from a config entry."""
    
    # Assign the HA configured log level of this module to the aiodabpumps module
    log_level: int = _LOGGER.getEffectiveLevel()
    lib_logger: logging.Logger = logging.getLogger("aiodabpumps")
    lib_logger.setLevel(log_level)

    _LOGGER.info(f"Logging at {logging.getLevelName(log_level)}")

    # Get properties from the config_entry
    install_id: str = config_entry.data[CONF_INSTALL_ID]
    install_name: str = config_entry.data[CONF_INSTALL_NAME]

    _LOGGER.info(f"Setup config entry for installation '{install_name}' ({install_id})")
    
    # Get an instance of the DabPumpsCoordinator for this install_id
    coordinator: DabPumpsCoordinator = DabPumpsCoordinatorFactory.create(hass, config_entry)
    
    # Fetch initial data so we have data when entities subscribe
    #
    # If the refresh fails, async_config_entry_first_refresh will
    # raise ConfigEntryNotReady and setup will try again later
    #
    await coordinator.async_config_entry_first_refresh()
    
    # Create devices
    await coordinator.async_create_devices(config_entry)
    
    # Create entities for all platforms (sensor, switch, ...)
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    # Cleanup entities and devices
    await coordinator.async_cleanup_entities(config_entry)
    await coordinator.async_cleanup_devices(config_entry)
    
    # Reload entry when it is updated
    config_entry.async_on_unload(config_entry.add_update_listener(_async_update_listener))
    
    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    success = await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)
    return success


async def _async_update_listener(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Fired after update of Config Options."""

    _LOGGER.debug(f"Detect update of config options {config_entry.options}")
    await hass.config_entries.async_reload(config_entry.entry_id)
