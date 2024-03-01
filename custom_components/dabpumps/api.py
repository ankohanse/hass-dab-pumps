"""api.py: DabPumps API for DAB Pumps integration."""

import asyncio
import hashlib
import httpx
import json
import jwt
import logging
import math
import re
import time
import urllib.parse

from collections import defaultdict, namedtuple
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.diagnostics import REDACTED
from homeassistant.components.diagnostics.util import async_redact_data
from homeassistant.components.sensor import SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import IntegrationError
from homeassistant.helpers.storage import Store

from httpx import RequestError, TimeoutException

from .const import (
    DOMAIN,
    API,
    DABPUMPS_API_URL,
    DABPUMPS_API_HOST,
    API_TOKEN_TIME_MIN,
    SIMULATE_SUFFIX_ID,
    DIAGNOSTICS_REDACT
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
            api = DabPumpsApi(hass, username, password)
    
            # cache this new DabPumpsApi instance        
            if hass:
                hass.data[DOMAIN][API][key] = api
        
        return api


# DabPumpsAPI to detect device and get device info, fetch the actual data from the Resol device, and parse it
class DabPumpsApi:
    
    _STORAGE_VERSION = 1
    _STORAGE_KEY_HISTORY = DOMAIN + ".api_history"
    
    def __init__(self, hass, username, password):
        self._username = username
        self._password = password
        self._client = None
        
        # calls history for diagnostics
        self._hass = hass
        self._history_store = Store[dict](hass, self._STORAGE_VERSION, self._STORAGE_KEY_HISTORY) if hass else None

        # cleanup history store after each restart
        self._hass.async_create_task(self._history_store.async_remove())
    
    
    async def async_login(self):
        # Step 0: do we still have a client with a non-expired auth token?
        if self._client:
            token = self._client.cookies.get("dabcsauthtoken", domain=DABPUMPS_API_HOST)
            if token:
                token_payload = jwt.decode(jwt=token, options={"verify_signature": False})
                
                if token_payload.get("exp", 0) - time.time() > API_TOKEN_TIME_MIN:
                    # still valid for another 10 seconds
                    await self._async_update_diagnostics("token reuse", None, None)
                    return True
                    
        # Make sure to have been logged out of previous sessions.
        # DAB Pumps service does not handle multiple logins from same account very well
        await self.async_logout()
        
        # Use a fresh client to keep track of cookies during login and subsequent calls
        client = httpx.AsyncClient(follow_redirects=True, timeout=120.0)
        
        # Step 1: get login url
        url = DABPUMPS_API_URL
        _LOGGER.debug(f"DAB Pumps retrieve login page via GET {url}")
        request = client.build_request("GET", url)
        response = await client.send(request)
        
        await self._async_update_diagnostics("home", request, response)
        
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
        request = client.build_request("POST", login_url, data=login_data, headers=login_hdrs)
        response = await client.send(request)

        await self._async_update_diagnostics("login", request, response)

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
                request = self._client.build_request("GET", url)
                response = await self._client.send(request)

                await self._async_update_diagnostics("logout", request, response)
                
                if (not response.is_success):
                    error = f"Unable to logout, got response {response.status_code} while trying to reach {url}"
                    # ignore and continue
                
                # Forget our current session so we are forced to do a fresh login in a next retry
                await self._client.aclose()
            except:
                pass # ignore any exceptions
            finally:
                self._client = None
            
        return True
        
        
    async def async_fetch_installs(self):
        # Get installation data
        url = DABPUMPS_API_URL + '/api/v1/gui/installation/list?lang=en'
        
        _LOGGER.debug(f"DAB Pumps retrieve installation info via GET {url}")
        request = self._client.build_request("GET", url)
        response = await self._client.send(request)
        
        await self._async_update_diagnostics("installation list", request, response)
        
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
        request = self._client.build_request("GET", url)
        response = await self._client.send(request)
        
        await self._async_update_diagnostics(f"localization_{lang}", request, response)
        
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
        
        serial = device.serial.removesuffix(SIMULATE_SUFFIX_ID)
        url = DABPUMPS_API_URL + f"/dumstate/{serial}"
        
        _LOGGER.debug(f"DAB Pumps retrieve device statusses for '{device.name}' via GET {url}")
        request = self._client.build_request("GET", url)
        response = await self._client.send(request)
        
        await self._async_update_diagnostics(f"statusses {serial}", request, response)
        
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
        
        
    async def async_change_device_status(self, status, value):
        
        serial = status.serial.removesuffix(SIMULATE_SUFFIX_ID)
        
        url = DABPUMPS_API_URL + f"/dum/{serial}"
        data = {'key': status.key, 'value': str(value) }
        hdrs = {'Content-Type': 'application/json'}
        
        _LOGGER.debug(f"DAB Pumps set device param for '{status.unique_id}' to '{value}' via POST {url}")
        request = self._client.build_request("POST", url, json=data, headers=hdrs)
        response = await self._client.send(request)
        
        await self._async_update_diagnostics(f"set {serial}:{status.key}", request, response)
        
        if (not response.is_success):
            error = f"Unable to set status {status.unique_id} to '{value}', got response {response.status_code} while trying to reach {url}"
            _LOGGER.debug(error)    # logged as warning after last retry
            raise DabPumpsApiError(error)
        
        result = response.json()
        if (result['res'] != 'OK'):
            if result['code'] in ['FORBIDDEN']:
                error = f"Authentication failed: {result['res']} {result['code']} {result.get('msg','')}"
                _LOGGER.debug(error)    # logged as warning after last retry
                raise DabPumpsApiAuthError(error)
            else:
                error = f"Unable retrieve device data, got response {result['res']} {result['code']} {result.get('message','')} while trying to reach {url}"
                _LOGGER.debug(error)    # logged as warning after last retry
                raise DabPumpsApiError(error)

        return True
        
    
    
    async def async_get_diagnostics(self) -> dict[str, Any]:
        data = await self._history_store.async_load() or {}
        counter = data.get("counter", {})
        history = data.get("history", [])
        details = data.get("details", {})
        
        calls_total = sum([ n for key, n in counter.items() ]) or 1
        calls_counter = { key: n for key, n in counter.items() }
        calls_percent = { key: round(100.0 * n / calls_total, 2) for key, n in counter.items() }
            
        return {
            "config": {
                "username": self._username,
                "password": self._password,
            },
            "diagnostics": {
                "counter": calls_counter,
                "percent": calls_percent,
                "history": history,
                "details": details,
            }
        }
    
    
    async def _async_update_diagnostics(self, context, request, response):
        # worker function
        async def _async_worker(self, context, request, response):
            item = DabPumpsApiHistoryItem(context)
            detail = DabPumpsApiHistoryDetail(context, request, response) if request and response else None
            
            # Persist this history in file instead of keeping in memory
            data = await self._history_store.async_load() or {}
            counter = data.get("counter", {})
            history = data.get("history", [])
            details = data.get("details", {})
            
            if context in counter:
                counter[context] += 1
            else:
                counter[context] = 1
            
            history.append( async_redact_data(item, DIAGNOSTICS_REDACT) )
            if len(history) > 32:
                history.pop(0)
            
            details[context] = async_redact_data(detail, DIAGNOSTICS_REDACT)
            
            data["history"] = history
            data["counter"] = counter
            data["details"] = details
            await self._history_store.async_save(data)

        # Create the worker task to update diagnostics in the background,
        # but do not let main loop wait for it to finish
        self._hass.async_create_task(_async_worker(self, context, request, response))



class DabPumpsApiAuthError(Exception):
    """Exception to indicate authentication failure."""


class DabPumpsApiError(Exception):
    """Exception to indicate generic error failure."""    
    
    
class DabPumpsApiHistoryItem(dict):
    def __init__(self, context):
        super().__init__({ 
            "ts": datetime.now(), 
            "op": context 
        })


class DabPumpsApiHistoryDetail(dict):
    def __init__(self, context, request, response):
        req = {
            "method": request.method,
            "url": str(request.url),
            "headers": request.headers,
        }
        if request.method == "POST":
            content = str(request.content).lstrip('b').strip("'")
            
            if request.headers.get('content-type','').startswith('application/json'):
                req["json"] = json.loads(content)
            elif request.headers.get('content-type','').startswith('application/x-www-form-urlencoded'):
                req["data"] = dict(urllib.parse.parse_qsl(content))
            else:
                req["content"] = content
        
        res = {
            "status": response.status_code,
            "reason": response.reason_phrase,
            "headers": response.headers,
            "elapsed": response.elapsed.total_seconds(),
        }
        if response.is_success and response.headers.get('content-type','').startswith('application/json'):
            res['json'] = response.json()
        
        super().__init__({
            "request": req, 
            "response": res,
        })
