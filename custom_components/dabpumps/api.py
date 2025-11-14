"""api.py: DabPumps API for DAB Pumps integration."""

import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Final
import httpx
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import create_async_httpx_client

from pydabpumps import (
    AsyncDabPumps,
    DabPumpsInstall,
    DabPumpsDevice,
    DabPumpsConfig,
    DabPumpsStatus,
    DabPumpsUserRole,
    DabPumpsHistoryItem,
    DabPumpsHistoryDetail,
    DabPumpsConnectError,
    DabPumpsAuthError,
) 

from .const import (
    DOMAIN,
    API,
    API_RETRY_ATTEMPTS,
    API_RETRY_DELAY,
    STORE_KEY_CACHE,
    STORE_WRITE_PERIOD_CACHE,
    utcnow,
    utcmin,
)
from .store import (
    DabPumpsStore,
)

# Define logger
_LOGGER = logging.getLogger(__name__)

class DabPumpsApiFactory:
    
    @staticmethod
    def create(hass: HomeAssistant, username: str, password: str, language: str) -> 'DabPumpsApiWrap':
        """
        Get a stored instance of the DabPumpsApi for given credentials
        """
    
        key = f"{username.lower()}_{hash(password) % 10**8}"
    
        # Sanity check
        if not DOMAIN in hass.data:
            hass.data[DOMAIN] = {}
        if not API in hass.data[DOMAIN]:
            hass.data[DOMAIN][API] = {}
            
        # if a DabPumpsApi instance for these credentials is already available then re-use it
        api = hass.data[DOMAIN][API].get(key, None)

        if not api or api.closed:
            _LOGGER.debug(f"create Api for account '{username}'")
            
            # Create a new DabPumpsApi instance and remember it
            api = DabPumpsApiWrap(hass, username, password, language)
            hass.data[DOMAIN][API][key] = api
        else:
            _LOGGER.debug(f"reuse Api for account '{username}'")

        return api
    

    @staticmethod
    def create_temp(hass: HomeAssistant, username: str, password: str, language: str) -> 'DabPumpsApiWrap':
        """
        Get a temporary instance of the DabPumpsApi for given credentials
        """

        key = f"{username.lower()}_{hash(password) % 10**8}"
    
        # Sanity check
        if not DOMAIN in hass.data:
            hass.data[DOMAIN] = {}
        if not API in hass.data[DOMAIN]:
            hass.data[DOMAIN][API] = {}
            
        # if a DabPumpsApi instance for these credentials is already available then re-use it
        api = hass.data[DOMAIN][API].get(key, None)
        
        if not api or api.closed:
            _LOGGER.debug(f"create temp Api")

            # Create a new DabPumpsApi instance
            api = DabPumpsApiWrap(hass, username, password, language)
    
        return api    



class DabPumpsFetchMethod(Enum):
    """Fetch methods"""
    WEB = 0     # slower, contains new data
    CACHE = 1   # faster, but old data

    def __str__(self):
        return self.name
    
    def __repr__(self):
        return self.name


class DabPumpsFetchOrder():
    """Fetch orders"""

    # On config, we try to fetch new data from web (slower)
    # No retries; if all login methods fail, we want to know immediately
    CONFIG: Final = ( DabPumpsFetchMethod.WEB, )   # Deliberate trailing comma to force create a tuple

    # On first fetch, we try to fetch old data from cache (faster) and 
    # fallback to fetch new data from web (slower and with two retries)
    # This allows for a faster startup of the integration
    INIT: Final = ( DabPumpsFetchMethod.CACHE, DabPumpsFetchMethod.WEB, DabPumpsFetchMethod.WEB, DabPumpsFetchMethod.WEB, )

    # On next fetches, we try to fetch new data from web (slower). 
    # No retries, next fetch will be 20 or 30 seconds later anyway. 
    # Also no need to read cached data; the api already contains these values.
    # Entities will display "unknown" once existing data gets too old.
    NEXT: Final = ( DabPumpsFetchMethod.WEB, )   # Deliberate trailing comma to force create a tuple

    # On change, we try to write the changed data to web (slower) with two retries
    CHANGE: Final = ( DabPumpsFetchMethod.WEB, DabPumpsFetchMethod.WEB, DabPumpsFetchMethod.WEB, )


class DabPumpsApiWrap(AsyncDabPumps):
    """Wrapper around pydabpumps AsyncDabPumps class"""

    def __init__(self, hass: HomeAssistant, username: str, password: str, language: str):
        """Initialize the api"""

        self._hass = hass
        self._username = username
        self._password = password
        self._language = language

        # Create a fresh http client
        client: httpx.AsyncClient = create_async_httpx_client(hass) 
    
        # Initialize the actual api
        super().__init__(username, password, client=client)
        super().set_diagnostics(self._diag_api_handler)

        # Other properties
        self._fetch_ts: dict[str, datetime] = {}

        # Persisted cached data in case communication to DAB Pumps fails
        self._hass: HomeAssistant = hass
        self._cache: DabPumpsStore = DabPumpsStore(hass, STORE_KEY_CACHE, STORE_WRITE_PERIOD_CACHE)

        # Counters for diagnostics
        self._diag_api_counters: dict[str, int] = {}
        self._diag_api_history: list[DabPumpsHistoryItem] = []
        self._diag_api_details: dict[str, DabPumpsHistoryDetail] = {}
        self._diag_api_data: dict[str, Any] = {}

        self._diag_retries: dict[int, int] = { n: 0 for n in range(API_RETRY_ATTEMPTS) }
        self._diag_durations: dict[int, int] = { n: 0 for n in range(10) }
        self._diag_fetch: dict[str, int] = { n.name: 0 for n in DabPumpsFetchMethod }


    async def async_on_unload(self, install_id:str):

        # Do not logout or close the api. Another coordinator/config-entry might still be using it.
        # But do trigger write of cache
        await self._async_write_cache(install_id, force=True)


    async def async_detect_for_config(self):
        ex_first = None
        ts_start = utcnow()

        fetch_order = DabPumpsFetchOrder.CONFIG
        for retry,fetch_method in enumerate(fetch_order):
            try:
                # Retry handling
                await self._async_handle_retry(retry, fetch_method, fetch_order)

                match fetch_method:
                    case DabPumpsFetchMethod.WEB:
                        # Logout so we really force a subsequent login and not use an old token
                        await super().logout()
                        await super().login()
                        
                        # Fetch the list of installations
                        await self._async_detect_installations(expiry=0, ignore=False)

                    case DabPumpsFetchMethod.CACHE:
                        raise Exception(f"Fetch from cache is not supported during config")
                
                # Keep track of how many retries were needed and duration
                # Keep track of how often the successfull fetch is from Web or is from Cache
                self._update_statistics(retries = retry, duration = utcnow()-ts_start, fetch=fetch_method)
                return True;
            
            except Exception as ex:
                # Already logged at debug level in pydabpumps
                if not ex_first:
                    ex_first = ex

                await super().logout()
            
        # Keep track of how many retries were needed and duration
        self._update_statistics(retries = retry, duration = utcnow()-ts_start)

        if ex_first:
            _LOGGER.warning(str(ex_first))
            raise ex_first from None
        
        return False
    
        
    async def async_detect_data(self, install_id: str, fetch_order: DabPumpsFetchOrder):
        ex_first = None
        ts_start = utcnow()

        for retry,fetch_method in enumerate(fetch_order):
            try:
                # Retry handling
                await self._async_handle_retry(retry, fetch_method, fetch_order)

                ignore_periodic_refresh = fetch_order in [DabPumpsFetchOrder.NEXT]

                match fetch_method:
                    case DabPumpsFetchMethod.WEB:
                        # Check access token, if needed do a logout, wait and re-login
                        await super().login()

                        # Once a day, attempt to refresh
                        # - list of translations
                        await self._async_detect_strings(self._language, expiry=24*60*60, ignore=ignore_periodic_refresh)

                        # Once an hour, attempt to refresh
                        # - list of installations (just for diagnostics)
                        # - installation devices, additional device details and device configurations
                        await self._async_detect_installations(expiry=60*60, ignore=ignore_periodic_refresh)
                        await self._async_detect_install_details(install_id, expiry=60*60, ignore=ignore_periodic_refresh)

                        # Always fetch device statuses
                        await self._async_detect_install_statuses(install_id, expiry=0, ignore=False)

                        # Update the persisted cache
                        await self._async_write_cache(install_id)

                    case DabPumpsFetchMethod.CACHE:
                        await self._async_read_cache(install_id)

                # Keep track of how many retries were needed and duration
                # Keep track of how often the successfull fetch is from Web or is from Cache
                self._update_statistics(retries = retry, duration = utcnow()-ts_start, fetch = fetch_method)

                return True
            
            except Exception as ex:
                # Already logged at debug level in pydabpumps
                if not ex_first:
                    ex_first = ex
                await super().logout()

        if ex_first:
            if isinstance(ex_first, (DabPumpsConnectError,DabPumpsAuthError)):
                # Log as info, not warning, as we expect the issue to be gone at a next data refresh
                _LOGGER.info(ex_first)
            else:
                _LOGGER.warning(ex_first)
        
        # Keep track of how many retries were needed and duration
        self._update_statistics(retries = retry, duration = utcnow()-ts_start)
        return False
    

    async def async_change_device_status(self, status: DabPumpsStatus, code: str|None = None, value: Any|None = None):
        ex_first = None
        ts_start = utcnow()
        fetch_web_done = False

        fetch_order = DabPumpsFetchOrder.CHANGE
        for retry,fetch_method in enumerate(fetch_order):
            try:
                # Retry handling
                await self._async_handle_retry(retry, fetch_method, fetch_order)

                match fetch_method:
                    case DabPumpsFetchMethod.WEB:
                        # Check access token, if needed do a logout, wait and re-login
                        await super().login()

                        # Attempt to change the device status via the API
                        await super().change_device_status(status.serial, status.key, code=code, value=value)

                    case DabPumpsFetchMethod.CACHE:
                        continue

                # Keep track of how many retries were needed and duration
                # Keep track of how often the successfull fetch is from Web or is from Cache
                self._update_statistics(retries = retry, duration = utcnow()-ts_start, fetch=fetch_method)
                return True
            
            except Exception as ex:
                # Already logged at debug level in pydabpumps
                if not ex_first:
                    ex_first = ex
                await super().logout()
            
        if ex_first:
            _LOGGER.warning(ex_first)
        
        # Keep track of how many retries were needed and duration
        self._update_statistics(retries = retry, duration = utcnow()-ts_start)
        return False
    

    async def async_change_install_role(self, install_id: str, role_old: DabPumpsUserRole, role_new: DabPumpsUserRole):
        ex_first = None
        ts_start = utcnow()
        fetch_web_done = False

        fetch_order = DabPumpsFetchOrder.CHANGE
        for retry,fetch_method in enumerate(fetch_order):
            try:
                # Retry handling
                await self._async_handle_retry(retry, fetch_method, fetch_order)

                match fetch_method:
                    case DabPumpsFetchMethod.WEB:
                        # Check access token, if needed do a logout, wait and re-login
                        await super().login()

                        # Attempt to change the user role via the API
                        await super().change_install_role(install_id, role_old, role_new)

                    case DabPumpsFetchMethod.CACHE:
                        continue

                # Keep track of how many retries were needed and duration
                # Keep track of how often the successfull fetch is from Web or is from Cache
                self._update_statistics(retries = retry, duration = utcnow()-ts_start, fetch=fetch_method)
                return True
            
            except Exception as ex:
                # Already logged at debug level in pydabpumps
                if not ex_first:
                    ex_first = ex
                await super().logout()
            
        if ex_first:
            _LOGGER.warning(ex_first)
        
        # Keep track of how many retries were needed and duration
        self._update_statistics(retries = retry, duration = utcnow()-ts_start)
        return False
    


    async def _async_handle_retry(self, retry: int, fetch_method: DabPumpsFetchMethod, fetch_order: DabPumpsFetchOrder):
            """
            """
            if retry == 0:
                # This is not a retry, but the first attempt
                return

            fetch_history: tuple[DabPumpsFetchMethod] = fetch_order[slice(retry)]

            if fetch_method in fetch_history:
                # Wait a bit before the next fetch using same method
                _LOGGER.info(f"Retry from {str(fetch_method)} in {API_RETRY_DELAY} seconds.")
                await asyncio.sleep(API_RETRY_DELAY)
            else:
                _LOGGER.info(f"Retry from {str(fetch_method)} now")


    async def _async_detect_installations(self, expiry:int=0, ignore:bool=False):
        """
        Attempt to refresh the list of installations
        """
        context = f"installations {self._username.lower()}"

        if (utcnow() - self._fetch_ts.get(context, utcmin())).total_seconds() < expiry:
            return  # Not yet expired
        
        try:
            await super().fetch_install_list()
            self._fetch_ts[context] = utcnow()

        except Exception as e:
            # Ignore issues if this is just a periodic update
            if ignore:
                _LOGGER.info(f"{e}")
            else:
                raise e from None
            
        return super().install_map


    async def _async_detect_install_details(self, install_id: str, expiry:int=0, ignore:bool=False):
        """
        Attempt to refresh installation details and devices when the cached one expires
        """
        context = f"installation {install_id}"

        if (utcnow() - self._fetch_ts.get(context, utcmin())).total_seconds() < expiry:
            return  # Not yet expired

        try:        
            await super().fetch_install_details(install_id)
            self._fetch_ts[context] = utcnow()

        except Exception as e:
            # Ignore issues if this is just a periodic update
            if ignore:
                _LOGGER.info(f"{e}")
            else:
                raise e from None


    async def _async_detect_install_statuses(self, install_id:str, expiry:int=0, ignore:bool=False):
        """
        Fetch device statuses for all devices in an install
        """
        context = f"statuses {install_id}"

        if (utcnow() - self._fetch_ts.get(context, utcmin())).total_seconds() < expiry:
            return  # Not yet expired

        try:
            await super().fetch_install_statuses(install_id)
            self._fetch_ts[context] = utcnow()

        except Exception as e:
            # Never ignore issues
            if ignore:
                _LOGGER.info(f"{e}")
            else:
                raise e from None
            

    async def _async_change_device_status(self, serial:str, key:str, code:str=None, value:str=None):
        """
        Update a device status to a new value
        """
        return 
    

    async def _async_detect_strings(self, language:str, expiry:int=0, ignore:bool=False):
        """
        Attempt to refresh the list of translations (once a day)
        """
        context = f"localization_{language}"

        if (utcnow() - self._fetch_ts.get(context, utcmin())).total_seconds() < expiry:
            return  # Not yet expired

        try:
            await super().fetch_strings(language)
            self._fetch_ts[context] = utcnow()
                    
            # If no exception was thrown, then the fetch method succeeded.
            # We do not need a local copy of super().string_map; the pydabpumps api takes care of translations

        except Exception as e:
            # Ignore issues if this is just a periodic update
            if ignore:
                _LOGGER.info(f"{e}")
            else:
                raise e from None


    async def _async_write_cache(self, install_id:str, force:bool=False):
        """
        Write maps retrieved from api to persisted storage
        """
 
        # Make sure we have read the storage file before we attempt set values and write it
        await self._cache.async_read()

        # Set the updated values
        install_serials = { device.serial for device in self.device_map.values() if device.install_id == install_id }
        install_configs = { device.config_id for device in self.device_map.values() if device.install_id == install_id }

        install_dict = { k:asdict(v) for k,v in self.install_map.items() }
        device_dict = { k:asdict(v) for k,v in self.device_map.items() if v.serial in install_serials }
        config_dict = { k:asdict(v) for k,v in self.config_map.items() if v.id in install_configs }
        status_dict = { k:asdict(v) for k,v in self.status_map.items() if v.serial in install_serials }
        
        self._cache.set(f"install_map {self._username}", install_dict )
        self._cache.set(f"device_map {install_id}", device_dict )
        self._cache.set(f"config_map {install_id}", config_dict )
        self._cache.set(f"status_map {install_id}", status_dict )

        # Note that async_write will reduce the number of writes if needed.
        await self._cache.async_write(force)


    async def _async_read_cache(self, install_id: str):
        """
        Read internal maps from persisted storage
        """             

        # Read from persisted file if not already read
        await self._cache.async_read()

        # Get all mappings, these will be returned as pure dicts and need to be converted into the proper dataclasses
        install_dict = self._cache.get(f"install_map {self._username}", {})
        device_dict = self._cache.get(f"device_map {install_id}", {})
        config_dict = self._cache.get(f"config_map {install_id}", {})
        status_dict = self._cache.get(f"status_map {install_id}", {})

        if not install_dict or not device_dict or not config_dict or not status_dict:
            raise Exception(f"Not all data found in {self._cache.key}")

        self._install_map.update( { k:DabPumpsInstall(**v) for k,v in install_dict.items() } )
        self._device_map.update( { k:DabPumpsDevice(**v) for k,v in device_dict.items() } )
        self._config_map.update( { k:DabPumpsConfig(**v) for k,v in config_dict.items() } )
        self._status_actual_map.update( { k:DabPumpsStatus(**v) for k,v in status_dict.items() } )


    def _update_statistics(self, retries: int|None = None, duration: timedelta|None = None, fetch: DabPumpsFetchMethod|None = None):
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


    def _diag_api_handler(self, context, item:DabPumpsHistoryItem, detail:DabPumpsHistoryDetail, data:dict):
        """
        Handle diagnostics updates from the api
        """

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


    async def async_get_diagnostics(self) -> dict[str, Any]:

        data = self._diag_api_data | {
            "install_map": self.install_map,
            "device_map": self.device_map,
            "config_map": self.config_map,
            "status_map": self.status_map,
            "string_map": self.string_map,
        }

        retries_total = sum(self._diag_retries.values()) or 1
        retries_counter = dict(sorted(self._diag_retries.items()))
        retries_percent = { key: round(100.0 * n / retries_total, 2) for key,n in retries_counter.items() }

        durations_total = sum(self._diag_durations.values()) or 1
        durations_counter = dict(sorted(self._diag_durations.items()))
        durations_percent = { key: round(100.0 * n / durations_total, 2) for key, n in durations_counter.items() }

        fetch_total = sum(self._diag_fetch.values()) or 1
        fetch_counter = dict(sorted(self._diag_fetch.items()))
        fetch_percent = { key: round(100.0 * n / fetch_total, 2) for key, n in fetch_counter.items() }
        
        calls_total = sum([ n for key, n in self._diag_api_counters.items() ]) or 1
        calls_counter = { key: n for key, n in self._diag_api_counters.items() }
        calls_percent = { key: round(100.0 * n / calls_total, 2) for key, n in self._diag_api_counters.items() }

        return {
            "data": data,
            "cache": await self._cache.async_get_diagnostics(),
            "diagnostics": {
                "ts": utcnow(),
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
                "calls": {
                  "counter": calls_counter,
                    "percent": calls_percent,
                },
            },
            "fetch_ts": self._fetch_ts,
            "history": self._diag_api_history,
            "details": self._diag_api_details,
        }
    






