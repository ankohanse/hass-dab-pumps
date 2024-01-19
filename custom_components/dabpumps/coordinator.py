import logging
import async_timeout

from datetime import timedelta
from typing import Any

from homeassistant.components.diagnostics import REDACTED
from homeassistant.components.diagnostics.util import async_redact_data
from homeassistant.components.light import LightEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)


from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
)
from .api import (
    DabPumpsApiFactory,
    DabPumpsApi,
    DabPumpsApiAuthError,
    DabPumpsApiError,
)

from .const import (
    DOMAIN,
    NAME,
    COORDINATOR,
    DEFAULT_POLLING_INTERVAL,
    CONF_INSTALL_ID,
    CONF_INSTALL_NAME,
    CONF_OPTIONS,
    CONF_POLLING_INTERVAL,
    BINARY_SENSOR_VALUES_ON,
    BINARY_SENSOR_VALUES_OFF,
    BINARY_SENSOR_VALUES_ALL,
    DIAGNOSTICS_REDACT,
)


_LOGGER = logging.getLogger(__name__)


class DabPumpsCoordinatorFactory:
    
    @staticmethod
    def create(hass: HomeAssistant, config_entry: ConfigEntry):
        """
        Get existing Coordinator for a config entry, or create a new one if it does not yet exist
        """
    
        # Get properties from the config_entry
        username = config_entry.data[CONF_USERNAME]
        password = config_entry.data[CONF_PASSWORD]
        install_id = config_entry.data[CONF_INSTALL_ID]
        install_name = config_entry.data[CONF_INSTALL_NAME]
        options = config_entry.options
        
        if not COORDINATOR in hass.data[DOMAIN]:
            hass.data[DOMAIN][COORDINATOR] = {}
            
        # already created?
        coordinator = hass.data[DOMAIN][COORDINATOR].get(install_id, None)
        if not coordinator:
            # Get an instance of the DabPumpsApi for these credentials
            # This instance may be shared with other coordinators that use the same credentials
            api = DabPumpsApiFactory.create(hass, username, password)
        
            # Get an instance of our coordinator. This is unique to this install_id
            coordinator = DabPumpsCoordinator(hass, api, install_id, options)
            hass.data[DOMAIN][COORDINATOR][install_id] = coordinator
            
        return coordinator


class DabPumpsCoordinator(DataUpdateCoordinator):
    """My custom coordinator."""
    
    def __init__(self, hass, dabpumps_api, install_id, options):
        """Initialize my coordinator."""
        
        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name=NAME,
            # Polling interval. Will only be polled if there are subscribers.
            update_interval=timedelta(seconds=options.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL)),
            update_method=self._async_update_data,
        )
        self._dabpumps_api = dabpumps_api
        self._install_id = install_id
        self._options = options
        self._string_map = {}
    
    
    @property
    def string_map(self):
        return self._dabpumps_api.string_map
        
    
    async def _async_update_data(self):
        """Fetch data from API.
        
        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        _LOGGER.debug(f"Update data")

        try:
            # Note: asyncio.TimeoutError and aiohttp.ClientError are already
            # handled by the data update coordinator.
            async with async_timeout.timeout(60):
                (device_map, config_map, status_map) = await self._dabpumps_api.async_detect_install_statusses(self._install_id)
                
                _LOGGER.debug(f"device_map: {device_map}")
                _LOGGER.debug(f"config_map: {config_map}")
                _LOGGER.debug(f"status_map: {status_map}")
                return (device_map, config_map, status_map)

        except DabPumpsApiAuthError as err:
            # Raising ConfigEntryAuthFailed will cancel future updates
            # and start a config flow with SOURCE_REAUTH (async_step_reauth)
            raise ConfigEntryAuthFailed from err
            
        except DabPumpsApiError as err:
            raise UpdateFailed(f"Error communicating with API: {err}")
    
    
    def get_diagnostics(self) -> dict[str, Any]:
        return {
            "install_id": self._install_id,
            "api": async_redact_data(self._dabpumps_api.get_diagnostics(), DIAGNOSTICS_REDACT),
        },
    
