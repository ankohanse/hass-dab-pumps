"""api.py: DabPumps API for DAB Pumps integration."""

import asyncio
import hashlib
import json
import logging
import math
import re
import httpx

from collections import namedtuple
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.diagnostics import REDACTED
from homeassistant.components.diagnostics.util import async_redact_data
from homeassistant.components.sensor import SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import IntegrationError

from httpx import RequestError, TimeoutException

from .const import (
    DOMAIN,
    API,
    DABPUMPS_API_URL,
    SIMULATE_SUFFIX_ID
)


_LOGGER = logging.getLogger(__name__)


class DabPumpsApiFactory:
    
    @staticmethod
    def create(hass: HomeAssistant, username, password):
        """
        Get a stored instance of the DabPumpsApi for given credentials
        """
    
        key = f"{username.lower()}_{hash(password) % 10**8}"
    
        # if a DabPumpsApi instance for these credentials is already available then e-use it
        if hass:
            if not API in hass.data[DOMAIN]:
                hass.data[DOMAIN][API] = {}
                
            api = hass.data[DOMAIN][API].get(key, None)
        else:
            api = None
            
        if not api:
            # Create a new DabPumpsApi instance
            api = DabPumpsApi(username, password)
    
            # cache this new DabPumpsApi instance        
            if hass:
                hass.data[DOMAIN][API][key] = api
        
        return api


# DabPumpsAPI to detect device and get device info, fetch the actual data from the Resol device, and parse it
class DabPumpsApi:
    def __init__(self, username, password):
        self._username = username
        self._password = password
        self._client = None
        
        # calls history for diagnostics
        self._history = []
        self._calls = {}

    
    async def async_login(self):
        if (self._client):
            return True
        
        # Use a fresh client to keep track of cookies during login and subsequent calls
        client = httpx.AsyncClient(follow_redirects=True, timeout=120.0)
        
        # Step 1: get login url
        url = DABPUMPS_API_URL
        _LOGGER.debug(f"DAB Pumps retrieve login page via GET {url}")
        response = await client.get(url)
        
        self._update_diagnostics("home", "GET", url, None, None, response)

        if (not response.is_success):
            error = f"Unable to connect, got response {response.status_code} while trying to reach {url}"
            _LOGGER.debug(error)    # logged as warning after last retry
            raise DabPumpsApiError(error)

        match = re.search(r'action\s?=\s?\"(.*?)\"', response.text, re.MULTILINE)
        if not match:    
            error = f"Unexpected response while retrieving login url from {url}: {response.text}"
            _LOGGER.debug(error)    # logged as warning after last retry
            raise DabPumpsApiAuthError(error)
        
        login_url = match.group(1).replace('&amp;', '&')
        login_data = {'username': self._username, 'password': self._password }
        login_hdrs = {'Content-Type': 'application/x-www-form-urlencoded'}

        # Step 2: Login
        _LOGGER.debug(f"DAB Pumps login via POST {login_url}")
        response = await client.post(login_url, data=login_data, headers=login_hdrs)
        
        self._update_diagnostics("login", "POST", login_url, login_hdrs, login_data, response)

        if (not response.is_success):
            error = f"Unable to login, got response {response.status_code}"
            raise DabPumpsApiAuthError(error)

        # remember this session so we re-use any cookies for subsequent calls
        self._client = client
        return True
        
        
    async def async_logout(self):
        if self._client:
            try:
                url = DABPUMPS_API_URL + '/logout'
                
                _LOGGER.debug(f"DAB Pumps logout via GET {url}")
                response = await self._client.get(url)
                
                self._update_diagnostics("logout", "GET", url, None, None, response)

                if (not response.is_success):
                    error = f"Unable to logout, got response {response.status_code} while trying to reach {url}"
                    # ignore and continue
                
                # Forget our current session so we are forced to do a fresh login in a next retry
                await self._client.aclose()
            finally:
                self._client = None
            
        return True
        
        
    async def async_fetch_installs(self):
        # Get installation data
        url = DABPUMPS_API_URL + '/api/v1/gui/installation/list?lang=en'

        _LOGGER.debug(f"DAB Pumps retrieve installation info via GET {url}")
        response = await self._client.get(url)
        
        self._update_diagnostics("installation list", "GET", url, None, None, response)

        if (not response.is_success):
            error = f"Unable retrieve installations, got response {response.status_code} while trying to reach {url}"
            _LOGGER.debug(error)    # logged as warning after last retry
            raise DabPumpsApiError(error)
        
        result = response.json()
        
        if (result['res'] != 'OK'):
            # BAD RESPONSE: { "res": "ERROR", "code": "FORBIDDEN", "msg": "Forbidden operation", "where": "ROUTE RULE" }
            if result['code'] in ['FORBIDDEN']:
                error = f"Authentication failed: {result['res']} {result['code']} {result.get('msg','')}"
                _LOGGER.debug(error)    # logged as warning after last retry
                raise DabPumpsApiAuthError(error)
            else:
                error = f"Unable retrieve installations, got response {result['res']} {result['code']} {result.get('msg','')} while trying to reach {url}"
                _LOGGER.debug(error)    # logged as warning after last retry
                raise DabPumpsApiError(error)
            
        return result.get('data', {})


    async def async_fetch_strings(self, lang):
        
        # Get installation data
        url = DABPUMPS_API_URL + f"/resources/js/localization_{lang}.properties?format=JSON"
        
        _LOGGER.debug(f"DAB Pumps retrieve language info via GET {url}")
        response = await self._client.get(url)
        
        self._update_diagnostics(f"localization_{lang}", "GET", url, None, None, response)
        
        if (not response.is_success):
            error = f"Unable retrieve language info, got response {response.status_code} while trying to reach {url}"
            _LOGGER.debug(error)    # logged as warning after last retry
            raise DabPumpsApiError(error)
        
        result = response.json()
        
        if (result['res'] != 'OK'):
            # BAD RESPONSE: { "res": "ERROR", "code": "FORBIDDEN", "msg": "Forbidden operation", "where": "ROUTE RULE" }
            if result['code'] in ['FORBIDDEN']:
                error = f"Authentication failed: {result['res']} {result['code']} {result.get('msg','')}"
                _LOGGER.debug(error)    # logged as warning after last retry
                raise DabPumpsApiAuthError(error)
            else:
                error = f"Unable retrieve installations, got response {result['res']} {result['code']} {result.get('msg','')} while trying to reach {url}"
                _LOGGER.debug(error)    # logged as warning after last retry
                raise DabPumpsApiError(error)
            
        return result.get('messages', {})


    # Fetch the statusses for a DAB Pumps device, which then constitues the Sensors
    async def async_fetch_device_statusses(self, device):

        url = DABPUMPS_API_URL + f"/dumstate/{device.serial.removesuffix(SIMULATE_SUFFIX_ID)}"
        
        _LOGGER.debug(f"DAB Pumps retrieve device statusses for '{device.name}' via GET {url}")
        response = await self._client.get(url)
        
        self._update_diagnostics(f"statusses {device.serial.removesuffix(SIMULATE_SUFFIX_ID)}", "GET", url, None, None, response)
        
        if (not response.is_success):
            error = f"Unable retrieve device data for '{device.name}', got response {response.status_code} while trying to reach {url}"
            _LOGGER.debug(error)    # logged as warning after last retry
            raise DabPumpsApiError(error)
        
        result = response.json()
        if (result['res'] != 'OK'):
            # BAD RESPONSE: { "res": "ERROR", "code": "FORBIDDEN", "msg": "Forbidden operation", "where": "ROUTE RULE" }
            if result['code'] in ['FORBIDDEN']:
                error = f"Authentication failed: {result['res']} {result['code']} {result.get('msg','')}"
                _LOGGER.debug(error)    # logged as warning after last retry
                raise DabPumpsApiAuthError(error)
            else:
                error = f"Unable retrieve device data, got response {result['res']} {result['code']} {result.get('msg','')} while trying to reach {url}"
                _LOGGER.debug(error)    # logged as warning after last retry
                raise DabPumpsApiError(error)
        
        return json.loads(result.get('status', '{}'))
    
    
    def get_diagnostics(self) -> dict[str, Any]:
        return {
            "username": self._username,
            "password": self._password,
            
            "history": self._history,
            "details": self._calls,
        }
        
    
    def _update_diagnostics(self, context, method, url, headers, content, response):
        # remember a history of what call happened when
        self._history.append({
            "ts": datetime.now(),
            "op": context,
        })
        if len(self._history) > 24:
            self._history.pop(0)
            
        # Remember details of last occurance of each call type
        req = {
            "method": method,
            "url": url,
            "headers": headers,
            "content": content,
        }
        res = {
            "status": response.status_code,
            "reason": response.reason_phrase,
            "headers": response.headers,
            "elapsed": response.elapsed.total_seconds(),
        }
        if response.is_success and response.headers.get('content-type','').startswith('application/json'):
            res['json'] = response.json()

        self._calls[context] = {
            "request": req,
            "response": res,
        }
    
    
class DabPumpsApiAuthError(Exception):
    """Exception to indicate authentication failure."""


class DabPumpsApiError(Exception):
    """Exception to indicate generic error failure."""    