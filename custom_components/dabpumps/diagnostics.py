"""Provides diagnostics for custom component."""

import logging

from copy import deepcopy
from typing import Any

from homeassistant.components.diagnostics import REDACTED
from homeassistant.components.diagnostics.util import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
)

from .const import (
    DOMAIN,
    NAME,
    COORDINATOR,
    CONF_INSTALL_ID,
    CONF_INSTALL_NAME,
    CONF_POLLING_INTERVAL,
    DIAGNOSTICS_REDACT,
)

from .coordinator import (
    DabPumpsCoordinatorFactory,
    DabPumpsCoordinator,
)


_LOGGER = logging.getLogger(__name__)


async def async_get_config_entry_diagnostics(hass: HomeAssistant, config_entry: ConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    install_id = config_entry.data[CONF_INSTALL_ID]
    install_name = config_entry.data[CONF_INSTALL_NAME]
    _LOGGER.info(f"Retrieve diagnostics for install {install_name} ({install_id})")
    
    coordinator: DabPumpsCoordinator = DabPumpsCoordinatorFactory.create(hass, config_entry)
    coordinator_data = await coordinator.async_get_diagnostics()

    return {
        "config": {
            "data": async_redact_data(config_entry.data, DIAGNOSTICS_REDACT),
            "options": async_redact_data(config_entry.options, DIAGNOSTICS_REDACT),
        },
        "coordinator": async_redact_data(coordinator_data, DIAGNOSTICS_REDACT),
    }
