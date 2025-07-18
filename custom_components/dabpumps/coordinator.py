import asyncio
import logging

from collections import namedtuple
from datetime import datetime, timedelta
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
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

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
    STORE_KEY_CACHE,
    CACHE_WRITE_PERIOD,
)

from .store import (
    DabPumpsStore,
)


_LOGGER = logging.getLogger(__name__)


class DabPumpsCoordinatorFetch(Enum):
    """Fetch methods"""
    WEB = 0     # slower, contains new data
    CACHE = 1   # faster, but old data

    def __str__(self):
        return self.name
    
    def __repr__(self):
        return self.name


class DabPumpsCoordinatorFetchOrder():
    """Fetch orders"""

    # On config, we try to fetch new data from web (slower)
    # No retries; if all login methods fail, we want to know immediately
    CONFIG: Final = ( DabPumpsCoordinatorFetch.WEB, )   # Deliberate trailing comma to force create a tuple

    # On first fetch, we try to fetch old data from cache (faster) and 
    # fallback to fetch new data from web (slower and with two retries)
    # This allows for a faster startup of the integration
    INIT: Final = ( DabPumpsCoordinatorFetch.CACHE, DabPumpsCoordinatorFetch.WEB, DabPumpsCoordinatorFetch.WEB, DabPumpsCoordinatorFetch.WEB, )

    # On next fetches, we try to fetch new data from web (slower). 
    # No retries, next fetch will be 20 or 30 seconds later anyway. 
    # Also no need to read cached data; the api already contains these values.
    # Entities will display "unknown" once existing data gets too old.
    NEXT: Final = ( DabPumpsCoordinatorFetch.WEB, )   # Deliberate trailing comma to force create a tuple

    # On change, we try to write the changed data to web (slower) with two retries
    CHANGE: Final = ( DabPumpsCoordinatorFetch.WEB, DabPumpsCoordinatorFetch.WEB, DabPumpsCoordinatorFetch.WEB, )


class DabPumpsCoordinatorFactory:
    """Factory to help create the Coordinator"""
    
    @staticmethod
    def create(hass: HomeAssistant, config_entry: ConfigEntry):
        """
        Get existing Coordinator for a config entry, or create a new one if it does not yet exist
        """
    
        # Get properties from the config_entry
        configs = config_entry.data
        options = config_entry.options

        username = configs.get(CONF_USERNAME, None)
        password = configs.get(CONF_PASSWORD, None)
        install_id = configs.get(CONF_INSTALL_ID, None)
        install_name = configs.get(CONF_INSTALL_NAME, None)
        
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
            _LOGGER.debug(f"Create coordinator for installation '{install_name}' ({install_id}) from account '{username}'")

            # Get an instance of the DabPumpsApi for these credentials
            # This instance may be shared with other coordinators that use the same credentials
            api = DabPumpsApiFactory.create(hass, username, password)
        
            # Get an instance of our coordinator. This is unique to this install_id
            coordinator = DabPumpsCoordinator(hass, api, configs, options)

            hass.data[DOMAIN][COORDINATOR][install_id] = coordinator
        else:
            _LOGGER.debug(f"Reuse coordinator for installation '{install_name}' ({install_id})")
            
        return coordinator


    @staticmethod
    def create_temp(username: str, password: str):
        """
        Get temporary Coordinator for a given username+password.
        This coordinator will only provide limited functionality
        """
    
        # Get properties from the config_entry
        hass = async_get_hass()
        configs = {
            CONF_USERNAME: username,
            CONF_PASSWORD: password,
        }
        options = {}
        
        # Get a temporary instance of the DabPumpsApi for these credentials
        api = DabPumpsApiFactory.create_temp(hass, username, password)
        
        # Get an instance of our coordinator. This is unique to this install_id
        _LOGGER.debug(f"create temp coordinator for account '{username}'")
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

        self._username = configs.get(CONF_USERNAME, None)
        self._install_id = configs.get(CONF_INSTALL_ID, None)
        self._install_name = configs.get(CONF_INSTALL_NAME, None)

        self._fetch_order = DabPumpsCoordinatorFetchOrder.INIT
        self._fetch_ts: dict[str, datetime] = {}

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
        self._cache: DabPumpsStore = DabPumpsStore(hass, STORE_KEY_CACHE, CACHE_WRITE_PERIOD)
        

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

        await self._async_detect_for_config()  
        
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
        await self._cache.async_write()

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

    
    async def _async_detect_for_config(self):
        ex_first = None
        ts_start = datetime.now()

        for retry,fetch_method in enumerate(self._fetch_order):
            try:
                fetch_history = self._fetch_order[slice(retry)]

                # Logout so we really force a subsequent login and not use an old token
                await self._async_logout(fetch_method)
                await self._async_login(fetch_method, fetch_history)
                
                # Fetch the list of installations
                await self._async_detect_installations(fetch_method)
                
                # Keep track of how many retries were needed and duration
                # Keep track of how often the successfull fetch is from Web or is from Cache
                self._update_statistics(retries = retry, duration = datetime.now()-ts_start, fetch=fetch_method)
                return True;
            
            except Exception as ex:
                _LOGGER.debug(str(ex))
                if not ex_first:
                    ex_first = ex

                await self._async_logout(fetch_method)
            
        # Keep track of how many retries were needed and duration
        self._update_statistics(retries = retry, duration = datetime.now()-ts_start)

        if ex_first:
            _LOGGER.warning(str(ex_first))
            raise ex_first from None
        
        return False
    
        
    async def _async_detect_data(self):
        warnings = []
        error = None
        ts_start = datetime.now()
        fetch_web_done = False

        for retry,fetch_method in enumerate(self._fetch_order):
            try:
                fetch_history = self._fetch_order[slice(retry)]

                # Check access token, if needed do a logout, wait and re-login
                await self._async_login(fetch_method, fetch_history)

                # Once a day, attempt to refresh
                # - list of translations
                await self._async_detect_strings(fetch_method)

                # Once an hour, attempt to refresh
                # - list of installations (just for diagnostics)
                # - installation details and devices
                # - additional device details
                # - device configurations
                await self._async_detect_installations(fetch_method, ignore_exception=True)
                await self._async_detect_install_details(fetch_method)
                await self._async_detect_devices_details(fetch_method)
                await self._async_detect_devices_configs(fetch_method)

                # Always fetch device statusses
                await self._async_detect_devices_statusses(fetch_method)

                # Keep track of how many retries were needed and duration
                # Keep track of how often the successfull fetch is from Web or is from Cache
                self._update_statistics(retries = retry, duration = datetime.now()-ts_start, fetch = fetch_method)
                return True
            
            except Exception as ex:
                error = str(ex)
                _LOGGER.debug(error)
                await self._async_logout(fetch_method)

        if error:
            _LOGGER.warning(error)
        
        # Keep track of how many retries were needed and duration
        self._update_statistics(retries = retry, duration = datetime.now()-ts_start)
        return False
    
        
    async def _async_change_device_status(self, status: DabPumpsStatus, code: str|None = None, value: Any|None = None):
        error = None
        ts_start = datetime.now()
        fetch_web_done = False

        for retry,fetch_method in enumerate(DabPumpsCoordinatorFetchOrder.CHANGE):
            
            if fetch_method != DabPumpsCoordinatorFetch.WEB:
                continue
            
            try:
                fetch_history = self._fetch_order[slice(retry)]

                # Check access token, if needed do a logout, wait and re-login
                await self._async_login(fetch_method, fetch_history)

                # Attempt to change the device status via the API
                await self._api.async_change_device_status(status.serial, status.key, code=code, value=value)

                # Keep track of how many retries were needed and duration
                # Keep track of how often the successfull fetch is from Web or is from Cache
                self._update_statistics(retries = retry, duration = datetime.now()-ts_start, fetch=fetch_method)
                return True
            
            except Exception as ex:
                error = str(ex)
                _LOGGER.debug(error)
                await self._async_logout(fetch_method)
            
        if error:
            _LOGGER.warning(error)
        
        # Keep track of how many retries were needed and duration
        self._update_statistics(retries = retry, duration = datetime.now()-ts_start)
        return False


    async def _async_login(self, fetch_method: DabPumpsCoordinatorFetch, fetch_history: list[DabPumpsCoordinatorFetch]):
        """
        Attempt to refresh login token when needed.
        Includes retry handling with waiting a moment before next try.
        """

        # Retry handling
        if len(fetch_history) > 0:
            if fetch_method == DabPumpsCoordinatorFetch.WEB and fetch_method in fetch_history:
                # Wait a bit before the next fetch from web
                _LOGGER.info(f"Retry from {str(fetch_method)} in {COORDINATOR_RETRY_DELAY} seconds.")
                await asyncio.sleep(COORDINATOR_RETRY_DELAY)
            else:
                _LOGGER.info(f"Retry from {str(fetch_method)} now")

        # Login if needed
        match fetch_method:
            case DabPumpsCoordinatorFetch.WEB:
                # Check if our access token is still valid and re-login if needed.
                await self._api.async_login()

            case DabPumpsCoordinatorFetch.CACHE:
                pass    # no login needed to access local cache

        # If no exception was thrown, then the login succeeded or token was still valid.


    async def _async_logout(self, fetch_method: DabPumpsCoordinatorFetch):
        """
        Logout
        """
        match fetch_method:
            case DabPumpsCoordinatorFetch.WEB:
                await self._api.async_logout()

            case DabPumpsCoordinatorFetch.CACHE:
                pass    # no logout needed


    async def _async_detect_install_details(self, fetch_method: DabPumpsCoordinatorFetch):
        """
        Attempt to refresh installation details and devices when the cached one expires (once an hour)
        """
        context = f"installation {self._install_id}"

        if (datetime.now() - self._fetch_ts.get(context, datetime.min)).total_seconds() < 3600:
            # Not yet expired
            return
        
        match fetch_method:
            case DabPumpsCoordinatorFetch.WEB:
                raw = await self._api.async_fetch_install_details(self._install_id, ret=DabPumpsRet.RAW)
                self._cache.set(context, raw)
                self._fetch_ts[context] = datetime.now()

            case DabPumpsCoordinatorFetch.CACHE:
                raw = self._cache.get(context, {})
                await self._api.async_fetch_install_details(self._install_id, raw=raw, ret=DabPumpsRet.NONE)

        # If no exception was thrown, then the fetch method succeeded.
        # Result is in self._api.device_map.


    async def _async_detect_devices_details(self, fetch_method: DabPumpsCoordinatorFetch):
        """
        Attempt to refresh device details
        """
        for device in self._api.device_map.values():
            await self._async_detect_device_details(device.serial, fetch_method)


    async def _async_detect_device_details(self, device_serial: str, fetch_method: DabPumpsCoordinatorFetch):
        """
        Attempt to refresh device details for a specific device (once an hour)
        """
        context = f"device {device_serial}"

        if (datetime.now() - self._fetch_ts.get(context, datetime.min)).total_seconds() < 3600:
            # Not yet expired
            return
        
        match fetch_method:
            case DabPumpsCoordinatorFetch.WEB:
                raw = await self._api.async_fetch_device_details(device_serial, ret=DabPumpsRet.RAW)
                self._cache.set(context, raw)
                self._fetch_ts[context] = datetime.now()

            case DabPumpsCoordinatorFetch.CACHE:
                raw = self._cache.get(context, {})
                await self._api.async_fetch_device_details(device_serial, raw=raw, ret=DabPumpsRet.NONE)

        # If no exception was thrown, then the fetch method succeeded.
        # Result is in self._api.device_map.


    async def _async_detect_devices_configs(self, fetch_method: DabPumpsCoordinatorFetch):
        """
        Attempt to refresh device configurations
        """

        # Compose set of config_id's (duplicates automatically removed)
        config_ids = { device.config_id for device in self._api.device_map.values() }

        for config_id in config_ids:
            await self._async_detect_device_configs(config_id, fetch_method)

    
    async def _async_detect_device_configs(self, config_id: str, fetch_method: DabPumpsCoordinatorFetch):
        """
        Attempt to refresh device configurations for a specific config id (once an hour)
        """
        context = f"configuration {config_id}"

        if (datetime.now() - self._fetch_ts.get(context, datetime.min)).total_seconds() < 3600:
            # Not yet expired
            return
        
        match fetch_method:
            case DabPumpsCoordinatorFetch.WEB:
                raw = await self._api.async_fetch_device_config(config_id, ret=DabPumpsRet.RAW)
                self._cache.set(context, raw)
                self._fetch_ts[context] = datetime.now()

            case DabPumpsCoordinatorFetch.CACHE:
                raw = self._cache.get(context, {})
                await self._api.async_fetch_device_config(config_id, raw=raw, ret=DabPumpsRet.NONE)
        
        # If no exception was thrown, then the fetch method succeeded.
        # Result is in self._api.config_map.


    async def _async_detect_devices_statusses(self, fetch_method: DabPumpsCoordinatorFetch):
        """
        Fetch device statusses
        """
        for device in self._api.device_map.values():
            await self._async_detect_device_statusses(device.serial, fetch_method)

        
    async def _async_detect_device_statusses(self, device_serial: str, fetch_method: DabPumpsCoordinatorFetch):
        """
        Fetch device statusses for a specific device (always)
        """
        context = f"statusses {device_serial}"

        match fetch_method:
            case DabPumpsCoordinatorFetch.WEB:
                raw = await self._api.async_fetch_device_statusses(device_serial, ret=DabPumpsRet.RAW)
                self._cache.set(context, raw)
                self._fetch_ts[context] = datetime.now()

            case DabPumpsCoordinatorFetch.CACHE:
                raw = self._cache.get(context, {})
                await self._api.async_fetch_device_statusses(device_serial, raw=raw, ret=DabPumpsRet.NONE)

        # If no exception was thrown, then the fetch method succeeded.
        # Result is in self._api.status_map.


    async def _async_detect_strings(self, fetch_method: DabPumpsCoordinatorFetch):
        """
        Attempt to refresh the list of translations (once a day)
        """
        context = f"localization_{self.language}"

        if (datetime.now() - self._fetch_ts.get(context, datetime.min)).total_seconds() < 86400:
            # Not yet expired
            return

        match fetch_method:
            case DabPumpsCoordinatorFetch.WEB:
                raw = await self._api.async_fetch_strings(self.language, ret=DabPumpsRet.RAW)
                self._cache.set(context, raw)
                self._fetch_ts[context] = datetime.now()

            case DabPumpsCoordinatorFetch.CACHE:
                raw = self._cache.get(context, {})
                await self._api.async_fetch_strings(self.language, raw=raw, ret=DabPumpsRet.NONE)
                
        # If no exception was thrown, then the fetch method succeeded.
        # Result is in self._api.string_map.


    async def _async_detect_installations(self, fetch_method: DabPumpsCoordinatorFetch, ignore_exception=False):
        """
        Attempt to refresh the list of installations (once an hour, just for diagnostocs)
        """
        context = f"installations {self._username.lower()}"

        if (datetime.now() - self._fetch_ts.get(context, datetime.min)).total_seconds() < 86400:
            # Not yet expired
            return
        
        try:
            match fetch_method:
                case DabPumpsCoordinatorFetch.WEB:
                    raw = await self._api.async_fetch_install_list(ret=DabPumpsRet.RAW)
                    self._cache.set(context, raw)
                    self._fetch_ts[context] = datetime.now()

                case DabPumpsCoordinatorFetch.CACHE:
                    raw = self._cache.get(context, {})
                    await self._api.async_fetch_install_list(raw=raw, ret=DabPumpsRet.NONE)

        except Exception as e:
            if not ignore_exception:
                raise e from None
                
        # If no exception was thrown, then the fetch method succeeded.
        # Result is in self._api.install_map.


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

        cache = { k: v for k,v in self._cache.items() }

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
                "user_role": self._api.user_role,
                "fetch_ts": self._fetch_ts,
            },
            "cache": cache,
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


