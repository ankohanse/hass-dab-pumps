"""api.py: DabPumps API for DAB Pumps integration."""

from datetime import datetime, timezone
from typing import Any
import aiohttp
import httpx
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.httpx_client import create_async_httpx_client

from aiodabpumps import (
    DabPumpsApi,
    DabPumpsHistoryItem,
    DabPumpsHistoryDetail,
) 

from .const import (
    DOMAIN,
    API,
)

# Define logger
_LOGGER = logging.getLogger(__name__)

# Define helper functions
utcnow = lambda: datetime.now(timezone.utc)


class DabPumpsApiFactory:
    
    @staticmethod
    def create(hass: HomeAssistant, username, password) -> 'DabPumpsApiWrap':
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
            api = DabPumpsApiWrap(hass, username, password)
            hass.data[DOMAIN][API][key] = api
        else:
            _LOGGER.debug(f"reuse Api for account '{username}'")

        return api
    

    @staticmethod
    def create_temp(hass: HomeAssistant, username, password) -> 'DabPumpsApiWrap':
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
            api = DabPumpsApiWrap(hass, username, password)
    
        return api    



class DabPumpsApiWrap(DabPumpsApi):
    """Wrapper around aiodabpumps DabPumpsApi class"""

    def __init__(self, hass: HomeAssistant, username: str, password: str):
        """Initialize the api"""

        self._hass = hass
        self._username = username
        self._password = password

        # Create a fresh http client
        client: aiohttp.ClientSession = async_create_clientsession(hass) 
        #client: httpx.AsyncClient = create_async_httpx_client(hass)
    
        # Initialize the actual api
        super().__init__(username, password, client=client)
        super().set_diagnostics(self._diag_api_handler)

        # Other properties
        self._fetch_ts: dict[str, datetime] = {}

        # Counters for diagnostics
        self._diag_api_counters: dict[str, int] = {}
        self._diag_api_history: list[DabPumpsHistoryItem] = []
        self._diag_api_details: dict[str, DabPumpsHistoryDetail] = {}
        self._diag_api_data: dict[str, Any] = {}
            

    async def async_login(self):
        """
        Attempt to refresh login token and re-login if needed.
        """
        await super().async_login()


    async def async_logout(self):
        """
        Logout
        """
        await super().async_logout()


    async def async_detect_installations(self, expiry:int=0, ignore:bool=False):
        """
        Attempt to refresh the list of installations
        """
        context = f"installations {self._username.lower()}"

        if (utcnow() - self._fetch_ts.get(context, datetime.min)).total_seconds() < expiry:
            return  # Not yet expired
        
        try:
            await self._api.async_fetch_install_list()
            self._fetch_ts[context] = utcnow()

        except Exception as e:
            # Ignore issues if this is just a periodic update
            if ignore:
                _LOGGER.info(f"{e}")
            else:
                raise e from None
            
        return super().install_map


    async def async_detect_install_details(self, install_id: str, expiry:int=0, ignore:bool=False):
        """
        Attempt to refresh installation details and devices when the cached one expires
        """
        context = f"installation {install_id}"

        if (utcnow() - self._fetch_ts.get(context, datetime.min)).total_seconds() < expiry:
            return  # Not yet expired

        try:        
            await super().async_fetch_install_details(install_id)
            self._fetch_ts[context] = utcnow()

        except Exception as e:
            # Ignore issues if this is just a periodic update
            if ignore:
                _LOGGER.info(f"{e}")
            else:
                raise e from None


    async def async_detect_install_statuses(self, install_id:str, expiry:int=0, ignore:bool=False):
        """
        Fetch device statuses for all devices in an install
        """
        context = f"statuses {install_id}"

        if (utcnow() - self._fetch_ts.get(context, datetime.min)).total_seconds() < expiry:
            return  # Not yet expired

        try:
            await super().async_fetch_install_statuses(install_id)
            self._fetch_ts[context] = utcnow()

        except Exception as e:
            # Never ignore issues
            if ignore:
                _LOGGER.info(f"{e}")
            else:
                raise e from None
            

    async def async_change_device_status(self, serial:str, key:str, code:str=None, value:str=None):
        """
        Update a device status to a new value
        """
        return await super().async_change_device_status(serial, key, code, value)
    

    async def async_detect_strings(self, language:str, expiry:int=0, ignore:bool=False):
        """
        Attempt to refresh the list of translations (once a day)
        """
        context = f"localization_{language}"

        if (utcnow() - self._fetch_ts.get(context, datetime.min)).total_seconds() < expiry:
            return  # Not yet expired

        try:
            await super().async_fetch_strings(self.language)
            self._fetch_ts[context] = utcnow()
                    
            # If no exception was thrown, then the fetch method succeeded.
            # We do not need a local copy of super().string_map; the aiodabpumps api takes care of translations

        except Exception as e:
            # Ignore issues if this is just a periodic update
            if ignore:
                _LOGGER.info(f"{e}")
            else:
                raise e from None


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

        api_calls_total = sum([ n for key, n in self._diag_api_counters.items() ]) or 1
        api_calls_counter = { key: n for key, n in self._diag_api_counters.items() }
        api_calls_percent = { key: round(100.0 * n / api_calls_total, 2) for key, n in self._diag_api_counters.items() }

        return {
            "data": self._diag_api_data,
            "calls": {
                "counter": api_calls_counter,
                "percent": api_calls_percent,
            },
            "fetch_ts": self._fetch_ts,
            "history": self._diag_api_history,
            "details": self._diag_api_details,
        }
    






