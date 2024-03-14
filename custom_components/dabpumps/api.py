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
        
        
        if hass:
            # calls history for diagnostics during normal operations    
            self._hass = hass
            self._history_store = Store[dict](hass, self._STORAGE_VERSION, self._STORAGE_KEY_HISTORY)
        
            # cleanup history store after each restart
            self._hass.async_create_task(self._history_store.async_remove())
        else:
            # Use from a temporary coordinator during config-flow first time setup of component
            self._hass = None
            self._history_store = None
    
    
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
        
        # Step 1: get login url
        # Use a fresh client to keep track of cookies during login and subsequent calls
        url = DABPUMPS_API_URL
        client = httpx.AsyncClient(follow_redirects=True, timeout=120.0)

        _LOGGER.debug(f"DAB Pumps retrieve login page via GET {url}")
        text = await self._async_send_data_request("home", "GET", url, client=client)
        
        match = re.search(r'action\s?=\s?\"(.*?)\"', text, re.MULTILINE)
        if not match:    
            error = f"Unexpected response while retrieving login url from {url}: {text}"
            _LOGGER.debug(error)    # logged as warning after last retry
            raise DabPumpsApiAuthError(error)
        
        login_url = match.group(1).replace('&amp;', '&')
        login_data = {'username': self._username, 'password': self._password }
        login_hdrs = {'Content-Type': 'application/x-www-form-urlencoded'}
        
        # Step 2: Login
        _LOGGER.debug(f"DAB Pumps login via POST {login_url}")
        await self._async_send_data_request("login", "POST", login_url, data=login_data, hdrs=login_hdrs, client=client)

        # if we reach this point without exceptions then login was successfull
        # remember this session so we re-use any cookies for subsequent calls
        self._client = client
        return True
        
        
    async def async_logout(self):
        if self._client:
            try:
                url = DABPUMPS_API_URL + '/logout'
                
                _LOGGER.debug(f"DAB Pumps logout via GET {url}")
                await self._async_send_data_request("logout", "GET", url)
            except:
                pass # ignore any exceptions

            try:
                # Forget our current session so we are forced to do a fresh login in a next retry
                await self._client.aclose()
            except:
                pass # ignore any exceptions
            finally:
                self._client = None
            
        return True
        
        
    async def async_fetch_install_list(self):
        """Get installation list"""
        context = f"installation list"
        verb = "GET"
        url = DABPUMPS_API_URL + '/api/v1/installation'
        
        _LOGGER.debug(f"DAB Pumps retrieve installation list via {verb} {url}")
        result = await self._async_send_json_request(context, verb, url)
    
        return result
    

    async def async_fetch_install(self, install_id):
        """Get installation data"""
        context = f"installation {install_id}"
        verb = "GET"
        url = DABPUMPS_API_URL + f"/api/v1/installation/{install_id.removesuffix(SIMULATE_SUFFIX_ID)}"
        
        _LOGGER.debug(f"DAB Pumps retrieve installation info via {verb} {url}")
        result = await self._async_send_json_request(context, verb, url)
    
        return result


    async def async_fetch_device_config(self, device):
        """Fetch the statusses for a DAB Pumps device, which then constitues the Sensors"""
    
        config_id = device.config_id

        context = f"configuration {config_id}"
        verb = "GET"
        url = DABPUMPS_API_URL + f"/api/v1/configuration/{config_id}"
        
        _LOGGER.debug(f"DAB Pumps retrieve device statusses for '{device.name}' via {verb} {url}")
        result = await self._async_send_json_request(context, verb, url, check_res=False)
        
        return result
        
        
    async def async_fetch_device_statusses(self, device):
        """Fetch the statusses for a DAB Pumps device, which then constitues the Sensors"""
    
        serial = device.serial.removesuffix(SIMULATE_SUFFIX_ID)

        context = f"statusses {serial}"
        verb = "GET"
        url = DABPUMPS_API_URL + f"/dumstate/{serial}"
        
        _LOGGER.debug(f"DAB Pumps retrieve device statusses for '{device.name}' via {verb} {url}")
        result = await self._async_send_json_request(context, verb, url)
        
        return json.loads(result.get('status', '{}'))
        
        
    async def async_change_device_status(self, status, value):
        """Set a new status value for a DAB Pumps device"""

        serial = status.serial.removesuffix(SIMULATE_SUFFIX_ID)
        
        context = f"set {serial}:{status.key}"
        verb = "POST"
        url = DABPUMPS_API_URL + f"/dum/{serial}"
        data = {'key': status.key, 'value': str(value) }
        hdrs = {'Content-Type': 'application/json'}
        
        _LOGGER.debug(f"DAB Pumps set device param for '{status.unique_id}' to '{value}' via {verb} {url}")
        result = await self._async_send_json_request(context, verb, url, data, hdrs)
        
        # If no exception was thrown then the operation was successfull
        return True
    

    async def async_fetch_strings(self, lang):
        """Get string translations"""
        context = f"localization_{lang}"
        verb = "GET"
        url = DABPUMPS_API_URL + f"/resources/js/localization_{lang}.properties?format=JSON"
        
        _LOGGER.debug(f"DAB Pumps retrieve language info via {verb} {url}")
        result = await self._async_send_json_request(context, verb, url)
    
        return result.get('messages', {})


    async def _async_send_data_request(self, context, verb, url, data=None, hdrs=None, client=None):
        # GET or POST a request for general data
        client = client or self._client

        request = client.build_request(verb, url, data=data, headers=hdrs)
        response = await client.send(request)
        
        await self._async_update_diagnostics(context, request, response)
        
        if not response.is_success:
            error = f"Unable to perform request, got response {response.status_code} while trying to reach {url}"
            _LOGGER.debug(error)    # logged as warning after last retry
            raise DabPumpsApiError(error)
        
        return response.text


    async def _async_send_json_request(self, context, verb, url, data=None, hdrs=None, check_res=True):
        # GET or POST a request for JSON data
        request = self._client.build_request(verb, url, json=data, headers=hdrs)
        response = await self._client.send(request)
        
        await self._async_update_diagnostics(context, request, response)
        
        if not response.is_success:
            error = f"Unable to perform request, got response {response.status_code} while trying to reach {url}"
            _LOGGER.debug(error)    # logged as warning after last retry
            raise DabPumpsApiError(error)
        
        result = response.json()
        
        if check_res and result['res'] != 'OK':
            # BAD RESPONSE: { "res": "ERROR", "code": "FORBIDDEN", "msg": "Forbidden operation", "where": "ROUTE RULE" }
            if result['code'] in ['FORBIDDEN']:
                error = f"Authorization failed: {result['res']} {result['code']} {result.get('msg','')}"
                _LOGGER.debug(error)    # logged as warning after last retry
                raise DabPumpsApiRightsError(error)
            else:
                error = f"Unable to perform request, got response {result['res']} {result['code']} {result.get('msg','')} while trying to reach {url}"
                _LOGGER.debug(error)    # logged as warning after last retry
                raise DabPumpsApiError(error)
        
        return result


    async def async_get_diagnostics(self) -> dict[str, Any]:
        if not self._history_store:
            return None
            
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
            if not self._history_store:
                return None
            
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
        if self._hass:
            self._hass.async_create_task(_async_worker(self, context, request, response))



class DabPumpsApiAuthError(Exception):
    """Exception to indicate authentication failure."""


class DabPumpsApiRightsError(Exception):
    """Exception to indicate authorization failure"""

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
