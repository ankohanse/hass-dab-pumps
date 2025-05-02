"""api.py: DabPumps API for DAB Pumps integration."""

import aiohttp
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from aiodabpumps import (
    DabPumpsApi,
) 

from .const import (
    DOMAIN,
    API,
)


_LOGGER = logging.getLogger(__name__)


class DabPumpsApiFactory:
    
    @staticmethod
    def create(hass: HomeAssistant, username, password):
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
        (api,client) = hass.data[DOMAIN][API].get(key, (None,None))

        if not api or not client or client.closed:
            _LOGGER.debug(f"create Api")

            # Create a fresh http client
            client: aiohttp.ClientSession = async_create_clientsession(hass)  

            # Create a new DabPumpsApi instance
            api = DabPumpsApi(username, password, client=client)

            # Remember this DabPumpsApi instance
            hass.data[DOMAIN][API][key] = (api, client)
        else:
            _LOGGER.debug(f"reuse Api")

        return api
    

    @staticmethod
    def create_temp(hass: HomeAssistant, username, password):
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
        (api,client) = hass.data[DOMAIN][API].get(key, (None,None))
        
        if not api or not client or client.closed:
            _LOGGER.debug(f"create temp Api")

            # Create a fresh http client
            client = async_create_clientsession(hass)  
    
            # Create a new DabPumpsApi instance
            api = DabPumpsApi(username, password, client=client)
    
        return api    


