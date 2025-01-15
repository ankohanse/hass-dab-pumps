import asyncio
import async_timeout
import json
import logging
import re

from collections import namedtuple
from datetime import datetime, timedelta, timezone
from typing import Any

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

DabPumpsInstall = namedtuple('DabPumpsInstall', 'id, name, description, company, address, role, devices')
DabPumpsDevice = namedtuple('DabPumpsDevice', 'id, serial, name, vendor, product, hw_version, sw_version, config_id, install_id, mac_address')
DabPumpsConfig = namedtuple('DabPumpsConfig', 'id, label, description, meta_params')
DabPumpsParams = namedtuple('DabPumpsParams', 'key, type, unit, weight, values, min, max, family, group, view, change, log, report')
DabPumpsStatus = namedtuple('DabPumpsStatus', 'serial, unique_id, key, val')


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

    @staticmethod
    def create_temp(username: str, password: str):
        """
        Get temporary Coordinator for a given username+password.
        This coordinator will only provide limited functionality
        """
    
        # Get properties from the config_entry
        hass = async_get_hass()
        install_id = None
        options = {}
        
        # Get a temporary instance of the DabPumpsApi for these credentials
        api = DabPumpsApiFactory.create_temp(hass, username, password)
        
        # Get an instance of our coordinator. This is unique to this install_id
        coordinator = DabPumpsCoordinator(hass, api, install_id, options)
        return coordinator
    

class DabPumpsCoordinator(DataUpdateCoordinator):
    """My custom coordinator."""
    
    def __init__(self, hass: HomeAssistant, api: DabPumpsApi, install_id: str, options: dict):
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

        self._api: DabPumpsApi = api
        self._install_id: str = install_id
        self._options: dict = options

        # counters for diagnostics
        self._diag_retries: dict[int, int] = { n: 0 for n in range(COORDINATOR_RETRY_ATTEMPTS) }
        self._diag_durations: dict[int, int] = { n: 0 for n in range(10) }
        self._diag_api_counters: dict[str, int] = {}
        self._diag_api_history: list[DabPumpsApiHistoryItem] = []
        self._diag_api_details: dict[str, DabPumpsApiHistoryDetail] = {}
        self._diag_api_data: dict[str, Any] = {}

        self._api.set_diagnostics(self._diag_api_handler)

        # Persisted cached data in case communication to DAB Pumps fails
        self._hass: HomeAssistant = hass
        self._store_key: str = install_id
        self._store: DabPumpsCoordinatorStore = DabPumpsCoordinatorStore(hass, self._store_key)
        self._cache: dict = None
        self._cache_last_write: datetime = datetime.min


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
            system_lang = self.system_language
            lang = system_lang if system_lang in LANGUAGE_MAP else LANGUAGE_AUTO_FALLBACK
    
        return lang
    

    @property
    def system_language(self) -> str:
        """
        Get HASS system language as set under Settings->System->General.
        Unless that language is not allowed in DConnect DAB LANGUAGE_MAP, in that case fallback to DEFAULT_LANGUAGE
        """
        return self.hass.config.language.split('-', 1)[0] # split from 'en-GB' to just 'en'


    async def async_config_flow_data(self):
        """
        Fetch installation data from API.
        """
        _LOGGER.debug(f"Config flow data")
        await self._async_detect_install_list()
        
        #_LOGGER.debug(f"install_map: {self._api.install_map}")
        return (self._api.install_map)


    async def async_create_devices(self, config_entry: ConfigEntry):
        """
        Add all detected devices to the hass device_registry
        """

        install_id: str = config_entry.data[CONF_INSTALL_ID]
        install_name: str = config_entry.data[CONF_INSTALL_NAME]

        _LOGGER.info(f"Create devices for installation '{install_name}' ({install_id})")
        dr: DeviceRegistry = device_registry.async_get(self.hass)
       
        for device in self._api.device_map.values():
            _LOGGER.debug(f"Create device {device.serial} ({DabPumpsCoordinator.create_id(device.name)})")

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


    async def _async_update_data(self):
        """
        Fetch sensor data from API.
        
        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        _LOGGER.debug(f"Update data")

        # Make sure our cache is available
        if self._cache is None:
            if self._store:
                _LOGGER.debug(f"Read persisted cache")
                store = await self._store.async_get_data() or {}
                self._cache = store.get("cache", {})
            else:
                self._cache = {}

        # Note: asyncio.TimeoutError and aiohttp.ClientError are already
        # handled by the data update coordinator.
        await self._async_detect_data()

        # Periodically persist the cache
        if self._hass and \
           self._store and \
           self._cache and \
           (datetime.now() - self._cache_last_write).total_seconds() > COORDINATOR_CACHE_WRITE_PERIOD:
            
            _LOGGER.debug(f"Persist cache")
            self._cache_last_write = datetime.now()

            store = await self._store.async_get_data() or {}
            store["cache"] = self._cache
            await self._store.async_set_data(store)
        
        #_LOGGER.debug(f"device_map: {self._api.device_map}")
        #_LOGGER.debug(f"config_map: {self._api.config_map}")
        #_LOGGER.debug(f"status_map: {self._api.status_map}")
        return (self._api.device_map, self._api.config_map, self._api.status_map)
    
    
    async def async_modify_data(self, object_id: str, entity_id: str, value: Any):
        """
        Set an entity param via the API.
        """
        status = self._api.status_map.get(object_id)
        if not status:
            # Not found
            return False
            
        if status.val == value:
            # Not changed
            return False
        
        # update the remote value
        return await self._async_change_device_status(status, value)
   
    
    async def _async_detect_install_list(self):
        error = None
        ts_start = datetime.now()

        for retry in range(0, COORDINATOR_RETRY_ATTEMPTS):
            try:
                await self._api.async_login()
                    
                # Fetch the list of installations
                await self._async_detect_installations()
                
                # Keep track of how many retries were needed and duration
                self._update_statistics(retries = retry, duration = datetime.now()-ts_start)
                return True;
            
            except Exception as ex:
                error = str(ex)
            
            # Log off, end session and retry if possible
            await self._api.async_logout();  
            
            if retry < COORDINATOR_RETRY_ATTEMPTS:
                if retry < 2:
                    _LOGGER.info(f"Retry {retry+1} in {COORDINATOR_RETRY_DELAY} seconds. {error}")
                else:
                    _LOGGER.warning(f"Retry {retry+1} in {COORDINATOR_RETRY_DELAY} seconds. {error}")
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

        for retry in range(0, COORDINATOR_RETRY_ATTEMPTS):
            try:
                try:
                    await self._api.async_login()
                except:
                    if len(self._api.device_map) > 0:
                        # Force retry in loop by raising original exception
                        raise
                    else:
                        # Ignore and use persisted cached data if this is the initial retrieve
                        pass

                # Attempt to refresh installation details and devices when the cached one expires (once a day)
                await self._async_detect_install_details()

                # Attempt to refresh additional device details (once a day)
                await self._async_detect_device_details()

                # Attempt to refresh device configurations (once a day)
                await self._async_detect_device_configs()

                # Fetch device statusses (always)
                await self._async_detect_device_statusses()

                # Attempt to refresh the list of translations (once a day)
                await self._async_detect_strings()

                # Attempt to refresh the list of installations (once a day, just for diagnostocs)
                await self._async_detect_installations(ignore_exception=True)

                # Keep track of how many retries were needed and duration
                self._update_statistics(retries = retry, duration = datetime.now()-ts_start)
                return True
            
            except Exception as ex:
                error = str(ex)
            
            # Log off, end session and retry if possible
            await self._api.async_logout();  
            
            if retry < COORDINATOR_RETRY_ATTEMPTS:
                if retry < 2:
                    _LOGGER.info(f"Retry {retry+1} in {COORDINATOR_RETRY_DELAY} seconds. {error}")
                else:
                    _LOGGER.warning(f"Retry {retry+1} in {COORDINATOR_RETRY_DELAY} seconds. {error}")
                await asyncio.sleep(COORDINATOR_RETRY_DELAY)
            
        if error:
            _LOGGER.warning(error)
        
        # Keep track of how many retries were needed and duration
        self._update_statistics(retries = retry, duration = datetime.now()-ts_start)
        return False
    
        
    async def _async_change_device_status(self, status: DabPumpsStatus, value: Any):
        error = None
        ts_start = datetime.now()

        for retry in range(0, COORDINATOR_RETRY_ATTEMPTS):
            try:
                await self._api.async_login()
                
                # Attempt to change the device status via the API
                await self._api.async_change_device_status(status.serial, status.key, value)

                # Keep track of how many retries were needed and duration
                self._update_statistics(retries = retry, duration = datetime.now()-ts_start)
                return True
            
            except Exception as ex:
                error = str(ex)
            
            # Log off, end session and retry if possible
            await self._api.async_logout();  
            
            if retry < COORDINATOR_RETRY_ATTEMPTS:
                if retry < 2:
                    _LOGGER.info(f"Retry {retry+1} in {COORDINATOR_RETRY_DELAY} seconds. {error}")
                else:
                    _LOGGER.warning(f"Retry {retry+1} in {COORDINATOR_RETRY_DELAY} seconds. {error}")
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
        if (datetime.now() - self._api.device_map_ts).total_seconds() < 86400:
            # Not yet expired
            return
        
        # Try to retrieve via API
        context = f"installation {self._install_id}"
        try:
            raw = await self._api.async_fetch_install_details(self._install_id, ret=DabPumpsRet.RAW)

            # Result is in self._api.device_map.
            # We also cache the raw data so we have something to fall back on in case of http errors
            await self._async_update_cache(context, raw)
            ex = None
        except Exception as e:
            if len(self._api.device_map) > 0:
                # Ignore problems if this is just a periodic refresh
                ex = None
            else:
                # Try next alternative while remembering original exception
                ex = e

        if ex:
            # Next, try from persisted cache
            try:
                raw = await self._async_fetch_from_cache(context)
                await self._api.async_fetch_install_details(self._install_id, raw=raw, ret=DabPumpsRet.NONE)
                ex = None
            except Exception:
                # Try next alternative while remembering original exception
                pass

        if ex:
            # Force retry in calling function by raising original exception
            raise ex


    async def _async_detect_device_details(self):
        """
        Attempt to refresh device details (once a day)
        """
        if (datetime.now() - self._api.device_detail_ts).total_seconds() < 86400:
            # Not yet expired
            return
        
        for device in self._api.device_map.values():

            # First try to retrieve from API
            context = f"device {device.serial}"
            try:
                raw = await self._api.async_fetch_device_details(device.serial, ret=DabPumpsRet.RAW)

                # Result is in self._api.device_map.
                # We also cache the raw data so we have something to fall back on in case of http errors
                await self._async_update_cache(context, raw)
                ex = None
            except Exception as e:
                if device.serial in self._api.device_map:
                    # Ignore problems if this is just a refresh
                    ex = None
                else:
                    # Try next alternative while remembering original exception
                    ex = e

            if ex:
                # Next try from persisted cache if this is the initial retrieve
                try:
                    raw = await self._async_fetch_from_cache(context)
                    await self._api.async_fetch_device_details(device.serial, raw=raw)
                    ex = None
                except Exception:
                    # Try next alternative while remembering original exception
                    pass

            if ex:
                # Force retry in calling function by raising original exception
                raise ex


    async def _async_detect_device_configs(self):
        """
        Attempt to refresh device configurations (once a day)
        """
        if (datetime.now() - self._api.config_map_ts).total_seconds() < 86400:
            # Not yet expired
            return
        
        for device in self._api.device_map.values():

            # First try to retrieve from API
            context = f"configuration {device.config_id}"
            try:
                raw = await self._api.async_fetch_device_config(device.config_id, ret=DabPumpsRet.RAW)

                # Result is in self._api.config_map.
                # We also cache the raw data so we have something to fall back on in case of http errors
                await self._async_update_cache(context, raw)
                ex = None
            except Exception as e:
                if device.config_id in self._api.config_map:
                    # Ignore problems if this is just a refresh
                    ex = None
                else:
                    # Try next alternative while remembering original exception
                    ex = e

            if ex:
                # Next try from persisted cache if this is the initial retrieve
                try:
                    raw = await self._async_fetch_from_cache(context)
                    await self._api.async_fetch_device_config(device.config_id, raw=raw)
                    ex = None
                except Exception:
                    # Try next alternative while remembering original exception
                    pass

            if ex:
                # Force retry in calling function by raising original exception
                raise ex


    async def _async_detect_device_statusses(self):
        """
        Fetch device statusses (always)
        """
        if (datetime.now() - self._api.status_map_ts).total_seconds() < 0:
            # Not yet expired
            return
        
        for device in self._api.device_map.values():

            # First try to retrieve from API
            context = f"statusses {device.serial}"
            try:
                raw = await self._api.async_fetch_device_statusses(device.serial, ret=DabPumpsRet.RAW)

                # Result is in self._api.status_map
                # We also cache the raw data so we have something to fall back on in case of http errors
                await self._async_update_cache(context, raw)
                ex = None
            except Exception as e:
                if any(status.serial==device.serial for status in self._api.status_map.values()):
                    # Ignore problems if this is just a refresh
                    ex = None
                else:
                    # Try next alternative while remembering original exception
                    ex = e

            if ex:
                # Next try from (outdated) persisted cache if this is the initial retrieve.
                # However, we will then set all values to unknown.
                try:
                    raw = await self._async_fetch_from_cache(context)
                    await self._api.async_fetch_device_statusses(device.serial, raw=raw)
                    ex = None
                except Exception:
                    # Try next alternative while remembering original exception
                    pass

            if ex:
                # Force retry in calling function by raising original exception
                raise ex


    async def _async_detect_strings(self):
        """
        Attempt to refresh the list of translations (once a day)
        """
        if (datetime.now() - self._api.string_map_ts).total_seconds() < 86400:
            # Not yet expired
            return
        
        context = f"localization_{self.language}"
        try:
            raw = await self._api.async_fetch_strings(self.language, ret=DabPumpsRet.RAW)

            # Result is in self._api.string_map
            # We also cache the raw data so we have something to fall back on in case of http errors
            await self._async_update_cache(context, raw)
            ex = None
        except Exception as e:
            if len(self._api.string_map) > 0:
                # Ignore problems if this is just a refresh
                ex = None
            else:
                # Try next alternative while remembering original exception
                ex = e
                
        if ex:
            # Next, try from persisted cache if this is the initial retrieve
            try:
                raw = await self._async_fetch_from_cache(context)
                await self._api.async_fetch_strings(self.language, raw=raw)
                ex = None
            except Exception:
                # Try next alternative while remembering original exception
                pass

        if ex:
            # Force retry in calling function by raising original exception
            raise ex


    async def _async_detect_installations(self, ignore_exception=False):
        """
        Attempt to refresh the list of installations (once a day, just for diagnostocs)
        """
        if (datetime.now() - self._api.install_map_ts).total_seconds() < 86400:
            # Not yet expired
            return
        
        # First try to retrieve from API.
        context = f"installation list"
        try:
            raw = await self._api.async_fetch_install_list(ret=DabPumpsRet.RAW)

            # Result is in self._api.install_map
            # We also cache the raw data so we have something to fall back on in case of http errors
            await self._async_update_cache(context, raw)
            ex = None
        except Exception as e:
            if ignore_exception:
                # Ignore problems
                ex = None
            else:
                # Try next alternative while remembering original exception
                ex = e

        if ex:
            # Force retry in calling function by raising original exception
            raise ex


    async def _async_update_cache(self, context, data):
        """
        Update the memory cache.
        Persisted cache is saved periodicaly by another function
        """
        if self._cache:
            data["ts"] = datetime.now()
            self._cache[context] = data


    async def _async_fetch_from_cache(self, context):
        """
        Fetch from the memory cache
        """
        if self._cache:
            _LOGGER.debug(f"Fetch from cache: {context}")
            return self._cache.get(context, {})
        else:
            return {}
        

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
    

    def _update_statistics(self, retries = None, duration = None):
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


    @staticmethod
    def create_id(*args):
        str = '_'.join(args).strip('_')
        str = re.sub(' ', '_', str)
        str = re.sub('[^a-z0-9_-]+', '', str.lower())
        return str        


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
    

    async def async_get_data(self):
        """Load the persisted coordinator_cache file and return the data specific for this coordinator instance"""
        data = await super().async_load() or {}
        data_self = data.get(self._store_key, {})
        return data_self
    

    async def async_set_data(self, data_self):
        """Save the data specific for this coordinator instance into the persisted coordinator_cache file"""
        data = await super().async_load() or {}
        data[self._store_key] = data_self
        await super().async_save(data)
    