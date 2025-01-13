"""api.py: DabPumps API for DAB Pumps integration."""

import logging

from datetime import datetime
from typing import Any
from yarl import URL

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
    
        if not API in hass.data[DOMAIN]:
            hass.data[DOMAIN][API] = {}
            
        # if a DabPumpsApi instance for these credentials is already available then e-use it
        api = hass.data[DOMAIN][API].get(key, None)
        if not api:
            # Create a fresh http client
            client = async_create_clientsession(hass)  

            # Create a new DabPumpsApi instance
            api = DabPumpsApi(hass, username, password, client=client)

            # Remember this DabPumpsApi instance
            hass.data[DOMAIN][API][key] = api
        
        return api
    

    @staticmethod
    def create_temp(hass: HomeAssistant, username, password):
        """
        Get a temporary instance of the DabPumpsApi for given credentials
        """

        # Create a fresh http client
        client = async_create_clientsession(hass)  
    
        # Create a new DabPumpsApi instance
        api = DabPumpsApi(hass, username, password, client=client)
    
        return api    


