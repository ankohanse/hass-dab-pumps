import asyncio
import async_timeout
import json
import logging
import re

from collections import namedtuple
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Final

from homeassistant.components.diagnostics import REDACTED
from homeassistant.components.diagnostics.util import async_redact_data
from homeassistant.components.light import LightEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.core import HomeAssistant
from homeassistant.core import async_get_hass
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry
from homeassistant.helpers import entity_registry
from homeassistant.helpers.device_registry import DeviceRegistry
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_LANGUAGE,
)

from aiodabpumps import (
    DabPumpsApi,
    DabPumpsApiError,
    DabPumpsApiAuthError,
    DabPumpsApiRightsError,
    DabPumpsApiHistoryItem,
    DabPumpsApiHistoryDetail,
    DabPumpsInstall,
    DabPumpsDevice, 
    DabPumpsConfig, 
    DabPumpsParams, 
    DabPumpsStatus, 
    DabPumpsRet,
) 


from .api import (
    DabPumpsApiFactory,
)

from .const import (
    DOMAIN,
    NAME,
    COORDINATOR,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_LANGUAGE,
    LANGUAGE_MAP,
    LANGUAGE_AUTO,
    LANGUAGE_AUTO_FALLBACK,
    CONF_INSTALL_ID,
    CONF_INSTALL_NAME,
    CONF_OPTIONS,
    CONF_POLLING_INTERVAL,
    DIAGNOSTICS_REDACT,
    COORDINATOR_RETRY_ATTEMPTS,
    COORDINATOR_RETRY_DELAY,
    COORDINATOR_TIMEOUT,
    COORDINATOR_CACHE_WRITE_PERIOD,
)


_LOGGER = logging.getLogger(__name__)



# Define fetch orders:
# - On first fetch, we try to fetch old data from cache (faster) and fallback to fetch new data from web (slower)
# - On next fetches, we try to fetch new data from web (slower) and fallback to fetch old data from cache
#
# This allows for a faster startup of the integration
#

class DabPumpsCoordinatorFetch(Enum):
    WEB = 0
    CACHE = 1

class DabPumpsCoordinatorFetchOrder():
    CONFIG: Final = ( DabPumpsCoordinatorFetch.WEB, )   # Deliberate trailing comma to force create a tuple
    INIT: Final = ( DabPumpsCoordinatorFetch.CACHE, DabPumpsCoordinatorFetch.WEB )
    NEXT: Final = ( DabPumpsCoordinatorFetch.WEB, DabPumpsCoordinatorFetch.CACHE )


class DabPumpsCoordinatorFactory:
    
    @staticmethod
    def create(hass: HomeAssistant, config_entry: ConfigEntry):
        """
        Get existing Coordinator for a config entry, or create a new one if it does not yet exist
        """
    
        # Get properties from the config_entry
        configs = config_entry.data
        options = config_entry.options

        username = configs[CONF_USERNAME]
        password = configs[CONF_PASSWORD]
        install_id = configs[CONF_INSTALL_ID]
        
        # Sanity check
        if not DOMAIN in hass.data:
            hass.data[DOMAIN] = {}
        if not COORDINATOR in hass.data[DOMAIN]:
            hass.data[DOMAIN][COORDINATOR] = {}
            
        # already created?
        coordinator = hass.data[DOMAIN][COORDINATOR].get(install_id, None)
        if coordinator:
            # Verify that config and options are still the same (== and != do a recursive dict compare)
            if coordinator.configs != configs or coordinator.options != options:
                # Not the same; force recreate of the coordinator
                _LOGGER.debug(f"Settings have changed; force use of new coordinator")
                coordinator = None

        if not coordinator:
            _LOGGER.debug(f"Create coordinator")

            # Get an instance of the DabPumpsApi for these credentials
            # This instance may be shared with other coordinators that use the same credentials
            api = DabPumpsApiFactory.create(hass, username, password)
        
            # Get an instance of our coordinator. This is unique to this install_id
            coordinator = DabPumpsCoordinator(hass, api, configs, options)

            hass.data[DOMAIN][COORDINATOR][install_id] = coordinator
        else:
            _LOGGER.debug(f"Reuse coordinator")
            
        return coordinator

    @staticmethod
    def create_temp(username: str, password: str):
        """
        Get temporary Coordinator for a given username+password.
        This coordinator will only provide limited functionality
        """
    
        # Get properties from the config_entry
        hass = async_get_hass()
        configs = {}
        options = {}
        
        # Get a temporary instance of the DabPumpsApi for these credentials
        api = DabPumpsApiFactory.create_temp(hass, username, password)
        
        # Get an instance of our coordinator. This is unique to this install_id
        _LOGGER.debug(f"create temp coordinator")
        coordinator = DabPumpsCoordinator(hass, api, configs, options)
        return coordinator
    

class DabPumpsCoordinator(DataUpdateCoordinator):
    """My custom coordinator."""

    def __init__(self, hass: HomeAssistant, api: DabPumpsApi, configs: dict[str,Any], options: dict[str,Any]):
        """
        Initialize my coordinator.
        """
        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name=NAME,
            # Polling interval. Will only be polled if there are subscribers.
            update_interval=timedelta(seconds=options.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL)),
            update_method=self._async_update_data,
        )

        self._api: DabPumpsApi = api
        self._configs: dict[str,Any] = configs
        self._options: dict[str,Any] = options

        self._install_id = configs.get(CONF_INSTALL_ID, None)
        self._install_name = configs.get(CONF_INSTALL_NAME, None)

        self._fetch_order = DabPumpsCoordinatorFetchOrder.INIT

        # Keep track of entity and device ids during init so we can cleanup unused ids later
        self._valid_unique_ids: dict[Platform, list[str]] = {}
        self._valid_device_ids: list[tuple[str,str]] = []

        # counters for diagnostics
        self._diag_retries: dict[int, int] = { n: 0 for n in range(COORDINATOR_RETRY_ATTEMPTS) }
        self._diag_durations: dict[int, int] = { n: 0 for n in range(10) }
        self._diag_fetch: dict[str, int] = { n.name: 0 for n in DabPumpsCoordinatorFetch }
        self._diag_api_counters: dict[str, int] = {}
        self._diag_api_history: list[DabPumpsApiHistoryItem] = []
        self._diag_api_details: dict[str, DabPumpsApiHistoryDetail] = {}
        self._diag_api_data: dict[str, Any] = {}

        self._api.set_diagnostics(self._diag_api_handler)

        # Persisted cached data in case communication to DAB Pumps fails
        self._hass: HomeAssistant = hass
        self._store_key: str = self._install_id
        self._store: DabPumpsCoordinatorStore = DabPumpsCoordinatorStore(hass, self._store_key)
        self._cache: DabPumpsCoordinatorCache = DabPumpsCoordinatorCache(self._store)
        

    @staticmethod
    def system_language() -> str:
        """
        Get HASS system language as set under Settings->System->General.
        Unless that language is not allowed in DConnect DAB LANGUAGE_MAP, in that case fallback to DEFAULT_LANGUAGE
        """
        hass = async_get_hass()
        return hass.config.language.split('-', 1)[0] # split from 'en-GB' to just 'en'


    @property
    def configs(self) -> dict[str,Any]:
        return self._configs
    

    @property
    def options(self) ->dict[str,Any]:
        return self._options
    

    @property
    def install_id(self) -> str:
        return self._install_id
    

    @property
    def install_name(self) -> str:
        return self._install_name
    

    @property
    def string_map(self) -> dict[str, str]:
        return self._api.string_map


    @property
    def user_role(self) -> str:
        return self._api.user_role[0] # only use the first character
    

    @property
    def language(self) -> str:
        lang = self._options.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
        if lang == LANGUAGE_AUTO:
            system_lang = DabPumpsCoordinator.system_language()
            lang = system_lang if system_lang in LANGUAGE_MAP else LANGUAGE_AUTO_FALLBACK
    
        return lang
    

    def create_id(self, *args):
        return self._api.create_id(*args)


    def set_valid_unique_ids(self, platform: Platform, ids: list[str]):
        self._valid_unique_ids[platform] = ids


    async def async_create_devices(self, config_entry: ConfigEntry):
        """
        Add all detected devices to the hass device_registry
        """

        _LOGGER.info(f"Create devices for installation '{self._install_name}' ({self._install_id})")
        dr: DeviceRegistry = device_registry.async_get(self.hass)
        valid_ids: list[tuple[str,str]] = []

        for device in self._api.device_map.values():
            _LOGGER.debug(f"Create device {device.serial} ({device.name})")

            dr.async_get_or_create(
                config_entry_id = config_entry.entry_id,
                identifiers = {(DOMAIN, device.serial)},
                connections = {(CONNECTION_NETWORK_MAC, device.mac_address)} if device.mac_address else None,
                name = device.name,
                manufacturer =  device.vendor,
                model = device.product,
                serial_number = device.serial,
                hw_version = device.hw_version,
                sw_version = device.sw_version,
            )
            valid_ids.append( (DOMAIN, device.serial) )

        # Remember valid device ids so we can do a cleanup of invalid ones later
        self._valid_device_ids = valid_ids


    async def async_cleanup_devices(self, config_entry: ConfigEntry):
        """
        cleanup all devices that are no longer in use
        """
        _LOGGER.info(f"Cleanup devices")

        dr = device_registry.async_get(self.hass)
        known_devices = device_registry.async_entries_for_config_entry(dr, config_entry.entry_id)

        for device in known_devices:
            if all(id not in self._valid_device_ids for id in device.identifiers):
                _LOGGER.info(f"Remove obsolete device {next(iter(device.identifiers))}")
                dr.async_remove_device(device.id)


    async def async_cleanup_entities(self, config_entry: ConfigEntry):
        """
        cleanup all entities that are no longer in use
        """
        _LOGGER.info(f"Cleanup entities")

        er = entity_registry.async_get(self.hass)
        known_entities = entity_registry.async_entries_for_config_entry(er, config_entry.entry_id)

        for entity in known_entities:
            # Note that platform and domain are mixed up in entity_registry
            valid_unique_ids = self._valid_unique_ids.get(entity.domain, [])

            if entity.unique_id not in valid_unique_ids:
                _LOGGER.info(f"Remove obsolete entity {entity.entity_id} ({entity.unique_id})")
                er.async_remove(entity.entity_id)


    async def async_config_flow_data(self):
        """
        Fetch installation data from API.
        """
        _LOGGER.debug(f"Config flow data")
        self._fetch_order = DabPumpsCoordinatorFetchOrder.CONFIG

        await self._async_detect_install_list()  
        
        #_LOGGER.debug(f"install_map: {self._api.install_map}")
        return (self._api.install_map)


    async def _async_update_data(self):
        """
        Fetch sensor data from API.
        
        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        _LOGGER.debug(f"Update data")

        # Make sure our cache is available
        await self._cache.async_read()

        # Fetch the actual data
        # Note: asyncio.TimeoutError and aiohttp.ClientError are already
        # handled by the data update coordinator.
        await self._async_detect_data()

        # If this was the first fetch, then make sure all next ones use the correct fetch order (web or cache)
        self._fetch_order = DabPumpsCoordinatorFetchOrder.NEXT

        # Periodically persist the cache
        await self._cache._async_write()

        #_LOGGER.debug(f"device_map: {self._api.device_map}")
        #_LOGGER.debug(f"config_map: {self._api.config_map}")
        #_LOGGER.debug(f"status_map: {self._api.status_map}")
        return (self._api.device_map, self._api.config_map, self._api.status_map)
    
    
    async def async_modify_data(self, object_id: str, entity_id: str, code: str|None = None, value: Any|None = None):
        """
        Set an entity param via the API.
        """
        status = self._api.status_map.get(object_id)
        if not status:
            # Not found
            return False

        # update the remote value
        return await self._async_change_device_status(status, code=code, value=value)

    
    async def _async_detect_install_list(self):
        error = None
        ts_start = datetime.now()

        # Only try once during config instead of using COORDINATOR_RETRY_ATTEMPTS.
        # If all login methods fail, we want to know immediately.
        retries = 0
        for retry in range(0, retries+1):
            try:
                # Fetch the list of installations
                await self._async_detect_installations()
                
                # Keep track of how many retries were needed and duration
                self._update_statistics(retries = retry, duration = datetime.now()-ts_start)
                return True;
            
            except Exception as ex:
                error = str(ex)
            
            # Log off, end session and retry if possible
            await self._api.async_logout();  
            
            if retry < retries:
                _LOGGER.info(f"Retry {retry+1} in {COORDINATOR_RETRY_DELAY} seconds. {error}")
                await asyncio.sleep(COORDINATOR_RETRY_DELAY)
            
        if error:
            _LOGGER.warning(error)
        
        # Keep track of how many retries were needed and duration
        self._update_statistics(retries = retry, duration = datetime.now()-ts_start)
        return False
    
        
    async def _async_detect_data(self):
        warnings = []
        error = None
        ts_start = datetime.now()

        for retry in range(0, COORDINATOR_RETRY_ATTEMPTS+1):
            try:
                # Once a day, attempt to refresh
                # - list of translations
                await self._async_detect_strings()

                # Once an hour, attempt to refresh
                # - list of installations (just for diagnostics)
                # - installation details and devices
                # - additional device details
                # - device configurations
                await self._async_detect_installations(ignore_exception=True)
                await self._async_detect_install_details()
                await self._async_detect_devices_details()
                await self._async_detect_devices_configs()

                # Always fetch device statusses
                await self._async_detect_devices_statusses()

                # Keep track of how many retries were needed and duration
                self._update_statistics(retries = retry, duration = datetime.now()-ts_start)
                return True
            
            except Exception as ex:
                error = str(ex)
            
            # Log off, end session and retry if possible
            await self._api.async_logout();  
            
            if retry < COORDINATOR_RETRY_ATTEMPTS:
                _LOGGER.info(f"Retry {retry+1} in {COORDINATOR_RETRY_DELAY} seconds. {error}")
                await asyncio.sleep(COORDINATOR_RETRY_DELAY)
            
        if error:
            _LOGGER.warning(error)
        
        # Keep track of how many retries were needed and duration
        self._update_statistics(retries = retry, duration = datetime.now()-ts_start)
        return False
    
        
    async def _async_change_device_status(self, status: DabPumpsStatus, code: str|None = None, value: Any|None = None):
        error = None
        ts_start = datetime.now()

        for retry in range(0, COORDINATOR_RETRY_ATTEMPTS+1):
            try:
                # Attempt to change the device status via the API
                await self._api.async_login()
                await self._api.async_change_device_status(status.serial, status.key, code=code, value=value)

                # Keep track of how many retries were needed and duration
                self._update_statistics(retries = retry, duration = datetime.now()-ts_start)
                return True
            
            except Exception as ex:
                error = str(ex)
            
            # Log off, end session and retry if possible
            await self._api.async_logout();  
            
            if retry < COORDINATOR_RETRY_ATTEMPTS:
                _LOGGER.info(f"Retry {retry+1} in {COORDINATOR_RETRY_DELAY} seconds. {error}")
                await asyncio.sleep(COORDINATOR_RETRY_DELAY)
            
        if error:
            _LOGGER.warning(error)
        
        # Keep track of how many retries were needed and duration
        self._update_statistics(retries = retry, duration = datetime.now()-ts_start)
        return False


    async def _async_detect_install_details(self):
        """
        Attempt to refresh installation details and devices when the cached one expires (once a day)
        """
        if (datetime.now() - self._api.device_map_ts).total_seconds() < 3600:
            # Not yet expired
            return
        
        context = f"installation {self._install_id}"

        for fetch_method in self._fetch_order:
            try:
                match fetch_method:
                    case DabPumpsCoordinatorFetch.WEB:
                        await self._api.async_login()

                        raw = await self._api.async_fetch_install_details(self._install_id, ret=DabPumpsRet.RAW)
                        self._cache[context] = raw

                    case DabPumpsCoordinatorFetch.CACHE:
                        raw = self._cache[context]
                        await self._api.async_fetch_install_details(self._install_id, raw=raw, ret=DabPumpsRet.NONE)
                
                # Keep track of how often the successfull fetch is from Web or is from Cache
                self._update_statistics(fetch = fetch_method)

                # If no exception was thrown, then the fetch method succeeded.
                # Result is in self._api.device_map.
                return

            except Exception as e:
                if len(self._api.device_map) > 0:
                    # Ignore problems if this is just a periodic refresh
                    return
                else:
                    # Try next fetch_method while remembering original exception
                    if fetch_method == DabPumpsCoordinatorFetch.WEB:
                        ex = e

        if ex:
            # All fetch methods failed.
            # Force retry in calling function by raising original exception
            raise ex from None


    async def _async_detect_devices_details(self):
        """
        Attempt to refresh device details (once a day)
        """
        if (datetime.now() - self._api.device_detail_ts).total_seconds() < 3600:
            # Not yet expired
            return
        
        for device in self._api.device_map.values():
            await self._async_detect_device_details(device.serial)


    async def _async_detect_device_details(self, device_serial: str):
        """
        Attempt to refresh device details for a specific device
        """
        context = f"device {device_serial}"

        for fetch_method in self._fetch_order:
            try:
                match fetch_method:
                    case DabPumpsCoordinatorFetch.WEB:
                        await self._api.async_login()

                        raw = await self._api.async_fetch_device_details(device_serial, ret=DabPumpsRet.RAW)
                        self._cache[context] = raw

                    case DabPumpsCoordinatorFetch.CACHE:
                        raw = self._cache[context]
                        await self._api.async_fetch_device_details(device_serial, raw=raw, ret=DabPumpsRet.NONE)
                
                # Keep track of how often the successfull fetch is from Web or is from Cache
                self._update_statistics(fetch = fetch_method)

                # If no exception was thrown, then the fetch method succeeded.
                # Result is in self._api.device_map.
                return

            except Exception as e:
                if device_serial in self._api.device_map:
                    # Ignore problems if this is just a periodic refresh
                    return
                else:
                    # Try next fetch_method while remembering original exception
                    if fetch_method == DabPumpsCoordinatorFetch.WEB:
                        ex = e

        if ex:
            # All fetch methods failed.
            # Force retry in calling function by raising original exception
            raise ex from None


    async def _async_detect_devices_configs(self):
        """
        Attempt to refresh device configurations (once a day)
        """
        if (datetime.now() - self._api.config_map_ts).total_seconds() < 3600:
            # Not yet expired
            return
        
        # Compose set of config_id's (duplicates automatically removed)
        config_ids = { device.config_id for device in self._api.device_map.values() }

        for config_id in config_ids:
            await self._async_detect_device_configs(config_id)

    
    async def _async_detect_device_configs(self, config_id: str):
        """
        Attempt to refresh device configurations for a specific config id
        """
        context = f"configuration {config_id}"

        for fetch_method in self._fetch_order:
            try:
                match fetch_method:
                    case DabPumpsCoordinatorFetch.WEB:
                        await self._api.async_login()

                        raw = await self._api.async_fetch_device_config(config_id, ret=DabPumpsRet.RAW)
                        self._cache[context] = raw

                    case DabPumpsCoordinatorFetch.CACHE:
                        raw = self._cache[context]
                        await self._api.async_fetch_device_config(config_id, raw=raw, ret=DabPumpsRet.NONE)
                
                # Keep track of how often the successfull fetch is from Web or is from Cache
                self._update_statistics(fetch = fetch_method)

                # If no exception was thrown, then the fetch method succeeded.
                # Result is in self._api.config_map.
                return

            except Exception as e:
                if config_id in self._api.config_map:
                    # Ignore problems if this is just a periodic refresh
                    return
                else:
                    # Try next fetch_method while remembering original exception
                    if fetch_method == DabPumpsCoordinatorFetch.WEB:
                        ex = e

        if ex:
            # All fetch methods failed.
            # Force retry in calling function by raising original exception
            raise ex from None


    async def _async_detect_devices_statusses(self):
        """
        Fetch device statusses (always)
        """
        if (datetime.now() - self._api.status_map_ts).total_seconds() < 0:
            # Not yet expired
            return
        
        for device in self._api.device_map.values():
            await self._async_detect_device_statusses(device.serial)

        
    async def _async_detect_device_statusses(self, device_serial: str):
        """
        Fetch device statusses for a specific device
        """
        context = f"statusses {device_serial}"

        for fetch_method in self._fetch_order:
            try:
                match fetch_method:
                    case DabPumpsCoordinatorFetch.WEB:
                        await self._api.async_login()

                        raw = await self._api.async_fetch_device_statusses(device_serial, ret=DabPumpsRet.RAW)
                        self._cache[context] = raw

                    case DabPumpsCoordinatorFetch.CACHE:
                        raw = self._cache[context]
                        await self._api.async_fetch_device_statusses(device_serial, raw=raw, ret=DabPumpsRet.NONE)
                
                # Keep track of how often the successfull fetch is from Web or is from Cache
                self._update_statistics(fetch = fetch_method)

                # If no exception was thrown, then the fetch method succeeded.
                # Result is in self._api.status_map.
                return

            except Exception as e:
                if any(status.serial==device_serial for status in self._api.status_map.values()):
                    # Ignore problems if this is just a periodic refresh
                    return
                else:
                    # Try next fetch_method while remembering original exception
                    if fetch_method == DabPumpsCoordinatorFetch.WEB:
                        ex = e

        if ex:
            # All fetch methods failed.
            # Force retry in calling function by raising original exception
            raise ex from None


    async def _async_detect_strings(self):
        """
        Attempt to refresh the list of translations (once a day)
        """
        if (datetime.now() - self._api.string_map_ts).total_seconds() < 86400:
            # Not yet expired
            return
        
        context = f"localization_{self.language}"

        for fetch_method in self._fetch_order:
            try:
                match fetch_method:
                    case DabPumpsCoordinatorFetch.WEB:
                        await self._api.async_login()

                        raw = await self._api.async_fetch_strings(self.language, ret=DabPumpsRet.RAW)
                        self._cache[context] = raw

                    case DabPumpsCoordinatorFetch.CACHE:
                        raw = self._cache[context]
                        await self._api.async_fetch_strings(self.language, raw=raw, ret=DabPumpsRet.NONE)
                
                # Keep track of how often the successfull fetch is from Web or is from Cache
                self._update_statistics(fetch = fetch_method)

                # If no exception was thrown, then the fetch method succeeded.
                # Result is in self._api.string_map.
                return

            except Exception as e:
                if len(self._api.string_map) > 0:
                    # Ignore problems if this is just a periodic refresh
                    return
                else:
                    # Try next fetch_method while remembering original exception
                    if fetch_method == DabPumpsCoordinatorFetch.WEB:
                        ex = e

        if ex:
            # All fetch methods failed.
            # Force retry in calling function by raising original exception
            raise ex from None


    async def _async_detect_installations(self, ignore_exception=False):
        """
        Attempt to refresh the list of installations (once a day, just for diagnostocs)
        """
        if (datetime.now() - self._api.install_map_ts).total_seconds() < 3600:
            # Not yet expired
            return
        
        context = f"installation list"

        for fetch_method in self._fetch_order:
            try:
                match fetch_method:
                    case DabPumpsCoordinatorFetch.WEB:
                        await self._api.async_login()

                        raw = await self._api.async_fetch_install_list(ret=DabPumpsRet.RAW)
                        self._cache[context] = raw

                    case DabPumpsCoordinatorFetch.CACHE:
                        raw = self._cache[context]
                        await self._api.async_fetch_install_list(raw=raw, ret=DabPumpsRet.NONE)
                
                # Keep track of how often the successfull fetch is from Web or is from Cache
                self._update_statistics(fetch = fetch_method)

                # If no exception was thrown, then the fetch method succeeded.
                # Result is in self._api.install_map.
                return

            except Exception as e:
                if ignore_exception:
                    # Ignore problems
                    return
                else:
                    # Try next fetch_method while remembering original exception
                    if fetch_method == DabPumpsCoordinatorFetch.WEB:
                        ex = e

        if ex:
            # All fetch methods failed.
            # Force retry in calling function by raising original exception
            raise ex from None


    async def async_get_diagnostics(self) -> dict[str, Any]:
        install_map = { k: v._asdict() for k,v in self._api.install_map.items() }
        device_map = { k: v._asdict() for k,v in self._api.device_map.items() }
        config_map = { k: v._asdict() for k,v in self._api.config_map.items() }
        status_map = { k: v._asdict() for k,v in self._api.status_map.items() }
        
        for cmk,cmv in self._api.config_map.items():
            config_map[cmk]['meta_params'] = { k: v._asdict() for k,v in cmv.meta_params.items() }
            
        retries_total = sum(self._diag_retries.values()) or 1
        retries_counter = dict(sorted(self._diag_retries.items()))
        retries_percent = { key: round(100.0 * n / retries_total, 2) for key,n in retries_counter.items() }

        durations_total = sum(self._diag_durations.values()) or 1
        durations_counter = dict(sorted(self._diag_durations.items()))
        durations_percent = { key: round(100.0 * n / durations_total, 2) for key, n in durations_counter.items() }

        fetch_total = sum(self._diag_fetch.values()) or 1
        fetch_counter = dict(sorted(self._diag_fetch.items()))
        fetch_percent = { key: round(100.0 * n / fetch_total, 2) for key, n in fetch_counter.items() }

        api_calls_total = sum([ n for key, n in self._diag_api_counters.items() ]) or 1
        api_calls_counter = { key: n for key, n in self._diag_api_counters.items() }
        api_calls_percent = { key: round(100.0 * n / api_calls_total, 2) for key, n in self._diag_api_counters.items() }

        return {
            "diagnostics_ts": datetime.now(),
            "diagnostics": {
                "retries": {
                    "counter": retries_counter,
                    "percent": retries_percent,
                },
                "durations": {
                    "counter": durations_counter,
                    "percent": durations_percent,
                },
                "fetch": {
                    "counter": fetch_counter,
                    "percent": fetch_percent,
                },
            },
            "data": {
                "install_id": self._install_id,
                "install_map_ts": self._api.install_map_ts,
                "install_map": install_map,
                "device_map_ts": self._api.device_map_ts,
                "device_detail_ts": self._api.device_detail_ts,
                "device_map": device_map,
                "config_map_ts": self._api.config_map_ts,
                "config_map": config_map,
                "status_map_ts": self._api.status_map_ts,
                "status_map": status_map,
                "string_map_ts": self._api.string_map_ts,
                "string_map_lang": self._api.string_map_lang,
                "string_map": self._api.string_map,
                "user_role_ts": self._api.user_role_ts,
                "user_role": self._api.user_role
            },
            "cache": self._cache,
            "api": {
                "data": self._diag_api_data,
                "calls": {
                    "counter": api_calls_counter,
                    "percent": api_calls_percent,
                },                
                "history": async_redact_data(self._diag_api_history, DIAGNOSTICS_REDACT),
                "details": async_redact_data(self._diag_api_details, DIAGNOSTICS_REDACT),
            }
        }
    

    def _update_statistics(self, retries: int|None = None, duration: timedelta|None = None, fetch: DabPumpsCoordinatorFetch|None = None):
        if retries is not None:
            if retries in self._diag_retries:
                self._diag_retries[retries] += 1
            else:
                self._diag_retries[retries] = 1
            
        if duration is not None:
            duration = round(duration.total_seconds(), 0)
            if duration not in self._diag_durations:
                self._diag_durations[duration] = 1
            else:
                self._diag_durations[duration] += 1

        if fetch is not None:
            if fetch.name not in self._diag_fetch:
                self._diag_fetch[fetch.name] = 1
            else:
                self._diag_fetch[fetch.name] += 1


    def _diag_api_handler(self, context, item:DabPumpsApiHistoryItem, detail:DabPumpsApiHistoryDetail, data:dict):
        """Handle diagnostics updates from the api"""

        # Call counters
        if context in self._diag_api_counters:
            self._diag_api_counters[context] += 1
        else:
            self._diag_api_counters[context] = 1

        # Call history
        self._diag_api_history.append(item)
        while len(self._diag_api_history) > 64:
            self._diag_api_history.pop(0)

        # Call details
        self._diag_api_details[context] = detail

        # Api data
        self._diag_api_data = self._diag_api_data | data


class DabPumpsDataError(Exception):
    """Exception to indicate generic data failure."""    


class DabPumpsCoordinatorStore(Store[dict]):
    
    _STORAGE_VERSION_MAJOR = 1
    _STORAGE_VERSION_MINOR = 0
    _STORAGE_KEY = DOMAIN + ".coordinator"
    
    def __init__(self, hass, store_key):
        super().__init__(
            hass, 
            key=self._STORAGE_KEY, 
            version=self._STORAGE_VERSION_MAJOR, 
            minor_version=self._STORAGE_VERSION_MINOR
        )
        self._store_key = store_key

    
    async def _async_migrate_func(self, old_major_version, old_minor_version, old_data):
        """Migrate the history store data"""

        if old_major_version <= 1:
            # version 1 is the current version. No migrate needed
            data = old_data

        return data
    

    async def async_load(self):
        """Load the persisted coordinator storage file and return the data specific for this coordinator instance"""
        if not self._store_key:
            return {}

        data = await super().async_load() or {}
        data_self = data.get(self._store_key, {})
        return data_self
    

    async def async_save(self, data_self):
        """Save the data specific for this coordinator instance into the persisted coordinator storage file"""
        if not self._store_key:
            return

        data = await super().async_load() or {}
        data[self._store_key] = data_self
        await super().async_save(data)


class DabPumpsCoordinatorCache(dict[str,Any]):

    def __init__(self, store: DabPumpsCoordinatorStore):
        super().__init__({})

        self._store = store
        self._last_read = datetime.min
        self._last_write = datetime.min


    def __setitem__(self, key, val):
        val["ts"] = datetime.now(timezone.utc)
        super().__setitem__(key, val)
        

    def __getitem__(self, key):
        _LOGGER.debug(f"Try fetch from cache: {key}")
        return super().__getitem__(key)
        

    async def async_read(self):
        """
        Read the persisted cache (if needed)
        """
        if self._last_read > datetime.min:
            return 

        _LOGGER.debug(f"Read persisted cache")
        data = await self._store.async_load() or {}
        super().clear()
        super().update( data.get("cache", {}) )

        # Set initial cache write timestamp
        self._last_read = datetime.now()
        self._last_write = datetime.now()


    async def _async_write(self):
        """
        Write the persisted cache
        """
        if len(self) == 0:
            return

        if (datetime.now() - self._last_write).total_seconds() < COORDINATOR_CACHE_WRITE_PERIOD:
            return        
            
        _LOGGER.debug(f"Write persisted cache")
        self._last_write = datetime.now()

        data = await self._store.async_load() or {}
        data["cache"] = { k:v for k,v in super().items() }
        await self._store.async_save(data)
        


    