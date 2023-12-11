from datetime import timedelta
import logging

import async_timeout

from homeassistant.components.light import LightEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .dabpumpsapi import (
    DabPumpsApi,
    DabPumpsApiAuthError,
    DabPumpsApiError,
)
    
from .const import (
    DOMAIN,
    NAME,
    COORDINATOR,
    DEFAULT_POLLING_INTERVAL,
)

_LOGGER: logging.Logger = logging.getLogger(__package__)


class DabPumpsCoordinator(DataUpdateCoordinator):
    """My custom coordinator."""
    
    def __init__(self, hass, dabpumps_api, options):
        """Initialize my coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name=NAME,
            # Polling interval. Will only be polled if there are subscribers.
            update_interval=timedelta(seconds=options.get('polling_interval', DEFAULT_POLLING_INTERVAL)),
            update_method=self._async_update_data,
        )
        self._dabpumps_api = dabpumps_api
        self._options = options
    
    
    async def _async_update_data(self):
        """Fetch data from API.
        
        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        try:
            # Note: asyncio.TimeoutError and aiohttp.ClientError are already
            # handled by the data update coordinator.
            async with async_timeout.timeout(60):
                (device_map, status_map) = await self._dabpumps_api.async_detect_device_statusses()
                return (device_map, status_map)

        except DabPumpsApiAuthError as err:
            # Raising ConfigEntryAuthFailed will cancel future updates
            # and start a config flow with SOURCE_REAUTH (async_step_reauth)
            raise ConfigEntryAuthFailed from err
            
        except DabPumpsApiError as err:
            raise UpdateFailed(f"Error communicating with API: {err}")


