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
    DABPUMPS_SSO_URL,
    DABPUMPS_API_URL,
    DABPUMPS_API_DOMAIN,
    DABPUMPS_API_TOKEN_COOKIE,
    DABPUMPS_API_TOKEN_TIME_MIN,
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
            token = self._client.cookies.get(DABPUMPS_API_TOKEN_COOKIE, domain=DABPUMPS_API_DOMAIN)
            if token:
                token_payload = jwt.decode(jwt=token, options={"verify_signature": False})
                
                if token_payload.get("exp", 0) - time.time() > DABPUMPS_API_TOKEN_TIME_MIN:
                    # still valid for another 10 seconds
                    await self._async_update_diagnostics("token reuse", None, None)
                    return
                    
        # Make sure to have been logged out of previous sessions.
        # DAB Pumps service does not handle multiple logins from same account very well
        await self.async_logout()
        
        # Step 1: get authorization token
        # Use a fresh client to keep track of cookies during login and subsequent calls
        client = httpx.AsyncClient(follow_redirects=True, timeout=120.0)

        context = "login"
        verb = "POST"
        url = DABPUMPS_SSO_URL + f"/auth/realms/dwt-group/protocol/openid-connect/token"
        data = {
             'client_id': 'DWT-Dconnect-Mobile',
             'client_secret': 'ce2713d8-4974-4e0c-a92e-8b942dffd561',
             'scope': 'openid',
             'grant_type': 'password',
             'username': self._username, 
             'password': self._password 
        }
        hdrs = {
             'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        _LOGGER.debug(f"DAB Pumps login via {verb} {url}")
        result = await self._async_send_json_request(context, verb, url, data=data, hdrs=hdrs, client=client)

        token = result.get('access_token') or ""
        if not token:
            error = f"No access token found in response from {url}"
            _LOGGER.debug(error)    # logged as warning after last retry
            raise DabPumpsApiAuthError(error)

        # Step 2: Validate the auth token against the DABPumps Api
        context = "validate token"
        verb = "GET"
        url = DABPUMPS_API_URL + f"/api/v1/token/validatetoken"
        params = {
            'email': self._username,
            'token': token,
        }

        _LOGGER.debug(f"DAB Pumps validate token via {verb} {url}")
        result = await self._async_send_json_request(context, verb, url, params=params, client=client)
        # if we reach this point then the token was OK

        # Step 3: Store returned access-token as cookie so it will automatically be passed in next calls
        client.cookies.set(name=DABPUMPS_API_TOKEN_COOKIE, value=token, domain=DABPUMPS_API_DOMAIN, path='/')

        # if we reach this point without exceptions then login was successfull
        # remember this session so we re-use any cookies for subsequent calls
        self._client = client
        
        
    async def async_logout(self):
        if not self._client:
            return
        
        try:
            # Forget our current session so we are forced to do a fresh login in a next retry
            await self._client.aclose()
        except:
            pass # ignore any exceptions
        finally:
            self._client = None
        
        
    async def async_fetch_install_list(self):
        """Get installation list"""
        context = f"installation list"
        verb = "GET"
        url = DABPUMPS_API_URL + '/api/v1/installation'
        # or  DABPUMPS_API_URL + f"/api/v1/installation/{install_id.removesuffix(SIMULATE_SUFFIX_ID)}"
        
        _LOGGER.debug(f"DAB Pumps retrieve installation list via {verb} {url}")
        result = await self._async_send_json_request(context, verb, url)
    
        return result


    async def async_fetch_device_config(self, device):
        """Fetch the statusses for a DAB Pumps device, which then constitues the Sensors"""
    
        config_id = device.config_id

        context = f"configuration {config_id}"
        verb = "GET"
        url = DABPUMPS_API_URL + f"/api/v1/configuration/{config_id}"
        # or  DABPUMPS_API_URL + f"/api/v1/configure/paramsDefinition?version=0&doc={config_name}"
        
        _LOGGER.debug(f"DAB Pumps retrieve device statusses for '{device.name}' via {verb} {url}")
        result = await self._async_send_json_request(context, verb, url)
        
        return result
        
        
    async def async_fetch_device_statusses(self, device):
        """Fetch the statusses for a DAB Pumps device, which then constitues the Sensors"""
    
        serial = device.serial.removesuffix(SIMULATE_SUFFIX_ID)

        context = f"statusses {serial}"
        verb = "GET"
        url = DABPUMPS_API_URL + f"/api/v1/dum/{serial}/state"
        # or  DABPUMPS_API_URL + f"/dumstate/{serial}"
        
        _LOGGER.debug(f"DAB Pumps retrieve device statusses for '{device.name}' via {verb} {url}")
        result = await self._async_send_json_request(context, verb, url)
        
        return result
        
        
    async def async_change_device_status(self, status, value):
        """Set a new status value for a DAB Pumps device"""

        serial = status.serial.removesuffix(SIMULATE_SUFFIX_ID)
        
        context = f"set {serial}:{status.key}"
        verb = "POST"
        url = DABPUMPS_API_URL + f"/dum/{serial}"
        json = {'key': status.key, 'value': str(value) }
        hdrs = {'Content-Type': 'application/json'}
        
        _LOGGER.debug(f"DAB Pumps set device param for '{status.unique_id}' to '{value}' via {verb} {url}")
        result = await self._async_send_json_request(context, verb, url, json=json, hdrs=hdrs)
        
        # If no exception was thrown then the operation was successfull
        return True
    

    async def async_fetch_strings(self, lang):
        """Get string translations"""
        context = f"localization_{lang}"
        verb = "GET"
        url = DABPUMPS_API_URL + f"/resources/js/localization_{lang}.properties?format=JSON"
        
        _LOGGER.debug(f"DAB Pumps retrieve language info via {verb} {url}")
        result = await self._async_send_json_request(context, verb, url)
    
        return result


    async def _async_send_json_request(self, context, verb, url, params=None, data=None, json=None, hdrs=None, client=None):
        # GET or POST a request for JSON data
        client = client or self._client

        request = client.build_request(verb, url, params=params, data=data, json=json, headers=hdrs)
        response = await client.send(request)
        
        await self._async_update_diagnostics(context, request, response)
        
        if not response.is_success:
            error = f"Unable to perform request, got response {response.status_code} while trying to reach {url}"
            _LOGGER.debug(error)    # logged as warning after last retry
            raise DabPumpsApiError(error)
        
        if not response.headers.get('content-type','').startswith('application/json'):
            return {}
        
        result = response.json()
        
        # if the result structure contains a 'res' value, then check it
        res = result.get('res') or None
        if res and res != 'OK':
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
