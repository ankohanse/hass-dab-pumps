import asyncio
from dataclasses import asdict, fields, is_dataclass
import logging

from collections import namedtuple
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Final

from multidict import MultiDict

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
from homeassistant.helpers.json import json as json_helper
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
    DabPumpsHistoryItem,
    DabPumpsHistoryDetail,
    DabPumpsInstall,
    DabPumpsDevice, 
    DabPumpsConfig, 
    DabPumpsParams,
    DabPumpsStatus,
    DabPumpsUserRole,
    DabPumpsDictFactory,
) 


from .api import (
    DabPumpsApiFactory,
    DabPumpsApiWrap,
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
    COORDINATOR_RELOAD_DELAY,
    COORDINATOR_RELOAD_DELAY_MAX,
    STORE_KEY_CACHE,
    STORE_WRITE_PERIOD_CACHE,
)

from .store import (
    DabPumpsStore,
)

# Define logger
_LOGGER = logging.getLogger(__name__)

# Define helper functions
utcnow = lambda: datetime.now(timezone.utc)


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
    def create(hass: HomeAssistant, config_entry: ConfigEntry, force_create: bool = False):
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

        reload_count = 0
        
        # Sanity check
        if not DOMAIN in hass.data:
            hass.data[DOMAIN] = {}
        if not COORDINATOR in hass.data[DOMAIN]:
            hass.data[DOMAIN][COORDINATOR] = {}
            
        # already created?
        coordinator = hass.data[DOMAIN][COORDINATOR].get(install_id, None)
        if coordinator:
            # check for an active reload and copy reload settings when creating a new coordinator
            reload_count = coordinator.reload_count

            # Forcing a new coordinator?
            if force_create:
                coordinator = None

            # Verify that config and options are still the same (== and != do a recursive dict compare)
            elif coordinator.configs != configs or coordinator.options != options:
                # Not the same; force recreate of the coordinator
                _LOGGER.debug(f"Settings have changed; force use of new coordinator")
                coordinator = None

        if not coordinator:
            _LOGGER.debug(f"Create coordinator for installation '{install_name}' ({install_id}) from account '{username}'")

            # Get an instance of the DabPumpsApi for these credentials
            # This instance may be shared with other coordinators that use the same credentials
            api = DabPumpsApiFactory.create(hass, username, password)
        
            # Get an instance of our coordinator. This is unique to this install_id
            coordinator = DabPumpsCoordinator(hass, config_entry.entry_id, api, configs, options)

            # Apply reload settings if needed
            coordinator.reload_count = reload_count

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
        coordinator = DabPumpsCoordinator(hass, None, api, configs, options)
        return coordinator
    

class DabPumpsCoordinator(DataUpdateCoordinator):
    """My custom coordinator."""

    def __init__(self, hass: HomeAssistant, config_entry_id: str, api: DabPumpsApiWrap, configs: dict[str,Any], options: dict[str,Any]):
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

        self._config_entry_id: str = config_entry_id
        self._api: DabPumpsApiWrap = api
        self._configs: dict[str,Any] = configs
        self._options: dict[str,Any] = options

        self._username = configs.get(CONF_USERNAME, None)
        self._install_id = configs.get(CONF_INSTALL_ID, None)
        self._install_name = configs.get(CONF_INSTALL_NAME, None)

        self._fetch_order = DabPumpsCoordinatorFetchOrder.INIT

        self._install_map: dict[str, DabPumpsInstall] = {}  # Points to either map read from cache or to map from _api
        self._device_map: dict[str, DabPumpsDevice] = {}    # Points to either map read from cache or to map from _api
        self._config_map: dict[str, DabPumpsConfig] = {}    # Points to either map read from cache or to map from _api
        self._status_map: dict[str, DabPumpsStatus] = {}    # Points to either map read from cache or to map from _api

        # Keep track of entity and device ids during init so we can cleanup unused ids later
        self._valid_unique_ids: dict[Platform, list[str]] = {} # platform -> entity unique_id
        self._valid_device_ids: dict[str, tuple[str,str]] = {} # serial -> HA device identifier

        # counters for diagnostics
        self._diag_retries: dict[int, int] = { n: 0 for n in range(COORDINATOR_RETRY_ATTEMPTS) }
        self._diag_durations: dict[int, int] = { n: 0 for n in range(10) }
        self._diag_fetch: dict[str, int] = { n.name: 0 for n in DabPumpsCoordinatorFetch }

        # Persisted cached data in case communication to DAB Pumps fails
        self._hass: HomeAssistant = hass
        self._cache: DabPumpsStore = DabPumpsStore(hass, STORE_KEY_CACHE, STORE_WRITE_PERIOD_CACHE)

        # Auto reload when a new device is detected
        self._reload_count: int = 0
        self._reload_time: datetime = utcnow()
        self._reload_delay: int = COORDINATOR_RELOAD_DELAY
        

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
    def user_name(self) -> str:
        return self._username


    @property
    def user_role(self) -> str:
        # Return the user role for this install_id
        # Note: we only use the first character
        if self._install_id in self._install_map:
            return self._install_map[self._install_id].role[0]
        else:
            return DabPumpsUserRole.CUSTOMER[0]
    

    @property
    def language(self) -> str:
        lang = self._options.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
        if lang == LANGUAGE_AUTO:
            system_lang = DabPumpsCoordinator.system_language()
            lang = system_lang if system_lang in LANGUAGE_MAP else LANGUAGE_AUTO_FALLBACK
    
        return lang
    

    @property
    def reload_count(self) -> int:
        return self._reload_count
    
    @reload_count.setter
    def reload_count(self, count: int):
        # Double the delay on each next reload to prevent enless reloads if something is wrong.
        self._reload_count = count
        self._reload_delay = min( pow(2,count-1)*COORDINATOR_RELOAD_DELAY, COORDINATOR_RELOAD_DELAY_MAX )
    

    async def async_on_unload(self):
        """
        Called when Home Assistant shuts down or config-entry unloads
        """
        _LOGGER.info(f"Unload installation '{self._install_name}'")

        # Persist the last statuses
        await self._cache.async_write(force = True)

        # Do not logout or close the api. Another coordinator/config-entry might still be using it


    def create_id(self, *args):
        return self._api.create_id(*args)


    def set_valid_unique_ids(self, platform: Platform, ids: list[str]):
        """
        Set list of valid entity ids for this installation.
        Called from entity_base when all entities for a platform have been created.
        """
        self._valid_unique_ids[platform] = ids


    async def async_create_devices(self, config_entry: ConfigEntry):
        """
        Add all detected devices to the hass device_registry
        """

        _LOGGER.info(f"Create devices for installation '{self._install_name}'")
        dr: DeviceRegistry = device_registry.async_get(self.hass)
        valid_ids: dict[str, tuple[str,str]] = {}

        install_devices = [ d for d in self._device_map.values() if d.install_id == self._install_id ]
        for device in install_devices:
            _LOGGER.debug(f"Create device {device.serial} ({device.name}) for installation '{self._install_name}'")

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
            valid_ids[device.serial] = (DOMAIN, device.serial)

        # Remember valid device ids so we can do a cleanup of invalid ones later
        self._valid_device_ids = valid_ids


    async def async_cleanup_devices(self, config_entry: ConfigEntry):
        """
        cleanup all devices that are no longer in use
        """
        _LOGGER.info(f"Cleanup devices for installation '{self._install_name}'")
        valid_identifiers = list(self._valid_device_ids.values())

        dr = device_registry.async_get(self.hass)
        registered_devices = device_registry.async_entries_for_config_entry(dr, config_entry.entry_id)

        for device in registered_devices:
            if all(id not in valid_identifiers for id in device.identifiers):
                _LOGGER.info(f"Remove obsolete device {next(iter(device.identifiers))} from installation '{self._install_name}'")
                dr.async_remove_device(device.id)


    async def async_cleanup_entities(self, config_entry: ConfigEntry):
        """
        cleanup all entities within this installation that are no longer in use
        """
        _LOGGER.info(f"Cleanup entities for installation '{self._install_name}'")

        er = entity_registry.async_get(self.hass)
        registered_entities = entity_registry.async_entries_for_config_entry(er, config_entry.entry_id)

        for entity in registered_entities:
            # Retrieve all valid ids matching the platform of this registered entity.
            # Note that platform and domain are mixed up in entity_registry
            valid_unique_ids = self._valid_unique_ids.get(entity.domain, [])

            if entity.unique_id not in valid_unique_ids:
                _LOGGER.info(f"Remove obsolete entity {entity.entity_id} ({entity.unique_id}) from installation '{self._install_name}'")
                er.async_remove(entity.entity_id)


    async def async_config_flow_data(self):
        """
        Fetch installation data from API.
        """
        _LOGGER.debug(f"Config flow data")
        self._fetch_order = DabPumpsCoordinatorFetchOrder.CONFIG

        await self._async_detect_for_config()  
        
        #_LOGGER.debug(f"install_map: {self._api.install_map}")
        return (self._install_map)


    async def _async_update_data(self):
        """
        Fetch sensor data from API.
        
        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        _LOGGER.debug(f"Update data for installation '{self._install_name}'")

        # Fetch the actual data
        # Note: asyncio.TimeoutError and aiohttp.ClientError are already
        # handled by the data update coordinator.
        await self._async_detect_data()

        # If this was the first fetch, then make sure all next ones use the correct fetch order (web or cache)
        self._fetch_order = DabPumpsCoordinatorFetchOrder.NEXT

        # Periodically detect changes in the installation and trigger reload of the integration if needed.
        await self._async_detect_changes()

        #_LOGGER.debug(f"device_map: {self._api.device_map}")
        #_LOGGER.debug(f"config_map: {self._api.config_map}")
        #_LOGGER.debug(f"status_map: {self._api.status_map}")
        return (self._device_map, self._config_map, self._status_map)
    
    
    async def async_modify_data(self, object_id: str, entity_id: str, code: str|None = None, value: Any|None = None) -> DabPumpsStatus|None:
        """
        Set an entity param via the API.
        """
        status = self._status_map.get(object_id)
        if not status:
            # Not found
            return None

        # update the remote value
        success = await self._async_change_device_status(status, code=code, value=value)
        if success:
            status.code = code if code != None else status.code
            status.value = value if value != None else status.value
            status.update_ts = utcnow()

            return status
        else:
            return None

    
    async def _async_detect_for_config(self):
        ex_first = None
        ts_start = utcnow()

        for retry,fetch_method in enumerate(self._fetch_order):
            try:
                # Retry handling
                await self._async_handle_retry(retry, fetch_method)

                match fetch_method:
                    case DabPumpsCoordinatorFetch.WEB:
                        # Logout so we really force a subsequent login and not use an old token
                        await self._api.async_logout()
                        await self._api.async_login()
                        
                        # Fetch the list of installations
                        await self._api.async_detect_installations(expiry=0, ignore=False)

                        # If we reach this point then every fetch succeeded
                        self._install_map = self._api.install_map

                    case DabPumpsCoordinatorFetch.CACHE:
                        raise Exception(f"Fetch from cache is not supported during config")
                
                # Keep track of how many retries were needed and duration
                # Keep track of how often the successfull fetch is from Web or is from Cache
                self._update_statistics(retries = retry, duration = utcnow()-ts_start, fetch=fetch_method)
                return True;
            
            except Exception as ex:
                _LOGGER.debug(str(ex))
                if not ex_first:
                    ex_first = ex

                await self._async_logout()
            
        # Keep track of how many retries were needed and duration
        self._update_statistics(retries = retry, duration = utcnow()-ts_start)

        if ex_first:
            _LOGGER.warning(str(ex_first))
            raise ex_first from None
        
        return False
    
        
    async def _async_detect_data(self):
        error = None
        ts_start = utcnow()

        for retry,fetch_method in enumerate(self._fetch_order):
            try:
                # Retry handling
                await self._async_handle_retry(retry, fetch_method)

                ignore_periodic_refresh = self._fetch_order in [DabPumpsCoordinatorFetchOrder.NEXT]

                match fetch_method:
                    case DabPumpsCoordinatorFetch.WEB:
                        # Check access token, if needed do a logout, wait and re-login
                        await self._api.async_login()

                        # Once a day, attempt to refresh
                        # - list of translations
                        await self._api.async_detect_strings(self.language, expiry=24*60*60, ignore=ignore_periodic_refresh)

                        # Once an hour, attempt to refresh
                        # - list of installations (just for diagnostics)
                        # - installation devices, additional device details and device configurations
                        await self._api.async_detect_installations(expiry=60*60, ignore=ignore_periodic_refresh)
                        await self._api.async_detect_install_details(self._install_id, expiry=60*60, ignore=ignore_periodic_refresh)

                        # Always fetch device statuses
                        await self._api.async_detect_install_statuses(self._install_id, expiry=0, ignore=False)

                        # If no exception was thrown, then all detects succeeded.
                        # Let our internal maps point to the _api maps (filtered for this install)
                        install_serials = { device.serial for device in self._api.device_map.values() if device.install_id == self._install_id }
                        install_configs = { device.config_id for device in self._api.device_map.values() if device.install_id == self._install_id }

                        self._install_map = { k:i for k,i in self._api.device_map.items() }
                        self._device_map = { k:d for k,d in self._api.device_map.items() if d.serial in install_serials }
                        self._config_map = { k:c for k,c in self._api.config_map.items() if c.id in install_configs }
                        self._status_map = { k:s for k,s in self._api.status_map.items() if s.serial in install_serials }

                        # Update the persisted cache
                        await self._async_write_cache()

                    case DabPumpsCoordinatorFetch.CACHE:
                        await self._async_read_cache()

                # Keep track of how many retries were needed and duration
                # Keep track of how often the successfull fetch is from Web or is from Cache
                self._update_statistics(retries = retry, duration = utcnow()-ts_start, fetch = fetch_method)

                return True
            
            except Exception as ex:
                error = str(ex)
                _LOGGER.debug(error)
                await self._async_logout()

        if error:
            _LOGGER.warning(error)
        
        # Keep track of how many retries were needed and duration
        self._update_statistics(retries = retry, duration = utcnow()-ts_start)
        return False
    

    async def _async_change_device_status(self, status: DabPumpsStatus, code: str|None = None, value: Any|None = None):
        error = None
        ts_start = utcnow()
        fetch_web_done = False

        for retry,fetch_method in enumerate(DabPumpsCoordinatorFetchOrder.CHANGE):
            try:
                # Retry handling
                await self._async_handle_retry(retry, fetch_method)

                match fetch_method:
                    case DabPumpsCoordinatorFetch.WEB:
                        # Check access token, if needed do a logout, wait and re-login
                        await self._api.async_login()

                        # Attempt to change the device status via the API
                        await self._api.async_change_device_status(status.serial, status.key, code=code, value=value)

                    case DabPumpsCoordinatorFetch.CACHE:
                        continue

                # Keep track of how many retries were needed and duration
                # Keep track of how often the successfull fetch is from Web or is from Cache
                self._update_statistics(retries = retry, duration = utcnow()-ts_start, fetch=fetch_method)
                return True
            
            except Exception as ex:
                error = str(ex)
                _LOGGER.debug(error)
                await self._async_logout()
            
        if error:
            _LOGGER.warning(error)
        
        # Keep track of how many retries were needed and duration
        self._update_statistics(retries = retry, duration = utcnow()-ts_start)
        return False


    async def _async_handle_retry(self, retry: int, fetch_method: DabPumpsCoordinatorFetch):
            """
            """
            if retry == 0:
                # This is not a retry, but the first attempt
                return

            fetch_history: tuple[DabPumpsCoordinatorFetch] = self._fetch_order[slice(retry)]

            if fetch_method in fetch_history:
                # Wait a bit before the next fetch using same method
                _LOGGER.info(f"Retry from {str(fetch_method)} in {COORDINATOR_RETRY_DELAY} seconds.")
                await asyncio.sleep(COORDINATOR_RETRY_DELAY)
            else:
                _LOGGER.info(f"Retry from {str(fetch_method)} now")


    async def _async_detect_changes(self):
        """Detect changes in the installation and trigger a integration reload if needed"""

        # Deliberately delay reload checks to prevent enless reloads if something is wrong
        if (utcnow() - self._reload_time).total_seconds() < self._reload_delay:
            return

        # Detect any changes
        reload = await self._async_detect_install_changes()
        if reload:
            self._reload_count += 1
            self.hass.config_entries.async_schedule_reload(self._config_entry_id)

        
    async def _async_detect_install_changes(self)  -> bool:
        """
        Detect any new devices. Returns True if a reload needs to be triggered else False
        """

        # Get list of device serials in HA device registry and as retrieved from Api
        old_serials: set[str] = set(self._valid_device_ids.keys())
        api_serials: set[str] = set([ d.serial for d in self._device_map.values() if d.install_id == self._install_id ])
        new_serials: set[str] = api_serials.difference(old_serials)

        for new_serial in new_serials:
            new_device = self._device_map.get(new_serial)
            _LOGGER.info(f"Found newly added device {new_device.serial} ({new_device.name}) for installation '{self._install_name}'. Trigger reload of integration.")
            return True
        
        return False


    async def _async_write_cache(self):
        """
        Write maps retrieved from api to persisted storage
        """
 
        # Make sure we have read the storage file before we attempt set values and write it
        await self._cache.async_read()

        # Set the updated values
        install_dict = { k:asdict(v) for k,v in self._install_map.items() }
        device_dict = { k:asdict(v) for k,v in self._device_map.items() }
        config_dict = { k:asdict(v) for k,v in self._config_map.items() }
        status_dict = { k:asdict(v) for k,v in self._status_map.items() }
        
        self._cache.set(f"install_map {self.user_name}", install_dict )
        self._cache.set(f"device_map {self.install_id}", device_dict )
        self._cache.set(f"config_map {self.install_id}", config_dict )
        self._cache.set(f"status_map {self.install_id}", status_dict )

        # Note that async_write will reduce the number of writes if needed.
        await self._cache.async_write()


    async def _async_read_cache(self):
        """
        Read internal maps from persisted storage
        """             

        # Read from persisted file if not already read
        await self._cache.async_read()

        # Get all mappings, these will be returned as pure dicts and need to be converted into the proper dataclasses
        install_dict = self._cache.get(f"install_map {self.user_name}", {})
        device_dict = self._cache.get(f"device_map {self.install_id}", {})
        config_dict = self._cache.get(f"config_map {self.install_id}", {})
        status_dict = self._cache.get(f"status_map {self.install_id}", {})

        self._install_map = { k:DabPumpsInstall(**v) for k,v in install_dict.items() }
        self._device_map = { k:DabPumpsDevice(**v) for k,v in device_dict.items() }
        self._config_map = { k:DabPumpsConfig(**v) for k,v in config_dict.items() }
        self._status_map = { k:DabPumpsStatus(**v) for k,v in status_dict.items() }

        if not self._install_map or not self._device_map or not self._config_map or not self._status_map:
            raise Exception(f"Not all data found in {self._cache.key}")


    def _update_statistics(self, retries: int|None = None, duration: timedelta|None = None, fetch: DabPumpsCoordinatorFetch|None = None):
        """
        Update internal counters used for diagnostics
        """
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


    async def async_get_diagnostics(self) -> dict[str, Any]:
        """
        Get all diagnostics values
        """
        retries_total = sum(self._diag_retries.values()) or 1
        retries_counter = dict(sorted(self._diag_retries.items()))
        retries_percent = { key: round(100.0 * n / retries_total, 2) for key,n in retries_counter.items() }

        durations_total = sum(self._diag_durations.values()) or 1
        durations_counter = dict(sorted(self._diag_durations.items()))
        durations_percent = { key: round(100.0 * n / durations_total, 2) for key, n in durations_counter.items() }

        fetch_total = sum(self._diag_fetch.values()) or 1
        fetch_counter = dict(sorted(self._diag_fetch.items()))
        fetch_percent = { key: round(100.0 * n / fetch_total, 2) for key, n in fetch_counter.items() }
        
        return {
            "diagnostics_ts": utcnow(),
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
                "install_map": self._install_map,
                "device_map": self._device_map,
                "config_map": self._config_map,
                "status_map": self._status_map,
                "user_name": self.user_name,
                "user_role": self.user_role,
                "language": self.language,
                "language_sys": self.system_language(),
                "reload_count": self.reload_count,
            },
        }
    

    @staticmethod
    def _utcnow():
        """Helper to get current time as UTC"""
        return datetime.now(timezone.utc)
    


    