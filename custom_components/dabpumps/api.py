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
from homeassistant.helpers.httpx_client import get_async_client
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
    API_LOGIN,
    SIMULATE_SUFFIX_ID,
    DIAGNOSTICS_REDACT,
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
            # Create a new DabPumpsApi instance
            api = DabPumpsApi(hass, username, password, use_history_store=True)
            hass.data[DOMAIN][API][key] = api
        
        return api
    

    @staticmethod
    def create_temp(hass: HomeAssistant, username, password):
        """
        Get a temporary instance of the DabPumpsApi for given credentials
        """
    
        # Create a new DabPumpsApi instance
        api = DabPumpsApi(hass, username, password, use_history_store=False)
    
        return api    


# DabPumpsAPI to detect device and get device info, fetch the actual data from the Resol device, and parse it
class DabPumpsApi:
    
    def __init__(self, hass, username, password, use_history_store=True):
        self._hass = hass
        self._username = username
        self._password = password
        self._client = None
        self._login_method = None
        
        if use_history_store:
            # maintain calls history for diagnostics during normal operations    
            self._history_key = username.lower()
            self._history_store = DabPumpsApiHistoryStore(hass, self._history_key)

            # Cleanup the history store after each restart.
            asyncio.run_coroutine_threadsafe(self._async_cleanup_diagnostics(), hass.loop)
        else:
            # Use from a temporary coordinator during config-flow first time setup of component
            self._history_key = None
            self._history_store = None


    async def async_login(self):
        # Step 0: do we still have a client with a non-expired auth token?
        if self._client:
            token = self._client.cookies.get(DABPUMPS_API_TOKEN_COOKIE, domain=DABPUMPS_API_DOMAIN)
            if token:
                token_payload = jwt.decode(jwt=token, options={"verify_signature": False})
                
                if token_payload.get("exp", 0) - time.time() > DABPUMPS_API_TOKEN_TIME_MIN:
                    # still valid for another 10 seconds
                    await self._async_update_diagnostics(datetime.now(), "token reuse", None, None, token_payload)
                    return

        # Make sure to have been logged out of previous sessions.
        # DAB Pumps service does not handle multiple logins from same account very well
        await self.async_logout()
        
        # We have four possible login methods that all seem to work for both DConnect (non-expired) and for DAB Live
        # First try the method that succeeded last time!
        error = None
        methods = [self._login_method, API_LOGIN.DABLIVE_APP_1, API_LOGIN.DABLIVE_APP_0, API_LOGIN.DCONNECT_APP, API_LOGIN.DCONNECT_WEB]
        for method in methods:
            try:
                match method:
                    case API_LOGIN.DABLIVE_APP_1: 
                        # Try the simplest method first
                        client = await self.async_login_dablive_app(isDabLive=1)
                    case API_LOGIN.DABLIVE_APP_0:
                        # Try the alternative simplest method
                        client = await self.async_login_dablive_app(isDabLive=0)
                    case API_LOGIN.DCONNECT_APP:
                        # Try the method that uses 2 steps
                        client = await self.async_login_dconnect_app()
                    case API_LOGIN.DCONNECT_WEB:
                        # Finally try the most complex and unreliable one
                        client = await self.async_login_dconnect_web()
                    case _:
                        # No previously known login method was set yet
                        continue

                # if we reached this point then a login method succeeded
                # start using this client and remember which method had success
                self._client = client
                self._login_method = method  
                return  
            
            except Exception as ex:
                error = ex

        # if we reached this point then all methods failed.
        if error:
            raise error
        

    async def async_login_dablive_app(self, isDabLive=1):
        # Step 1: get authorization token
        # Use a fresh client to keep track of cookies during login and subsequent calls
        client = get_async_client(self._hass)
        client.follow_redirects = True
        client.timeout = 120.0

        context = f"login DabLive_app (isDabLive={isDabLive})"
        verb = "POST"
        url = DABPUMPS_API_URL + f"/auth/token"
        params = {
            'isDabLive': isDabLive,     # required param, though actual value seems to be completely ignored
        }
        data = {
            'username': self._username, 
            'password': self._password 
        }
        hdrs = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        _LOGGER.debug(f"DAB Pumps login for '{self._username}' via {verb} {url}")
        result = await self._async_send_request(context, verb, url, params=params, data=data, hdrs=hdrs, client=client)

        token = result.get('access_token') or ""
        if not token:
            error = f"No access token found in response from {url}"
            _LOGGER.debug(error)    # logged as warning after last retry
            raise DabPumpsApiAuthError(error)

        # if we reach this point then the token was OK
        # Store returned access-token as cookie so it will automatically be passed in next calls
        client.cookies.set(name=DABPUMPS_API_TOKEN_COOKIE, value=token, domain=DABPUMPS_API_DOMAIN, path='/')
        return client
        
        
    async def async_login_dconnect_app(self):
        # Step 1: get authorization token
        # Use a fresh client to keep track of cookies during login and subsequent calls
        client = get_async_client(self._hass)
        client.follow_redirects = True
        client.timeout = 120.0

        context = f"login DConnect_app"
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
        
        _LOGGER.debug(f"DAB Pumps login for '{self._username}' via {verb} {url}")
        result = await self._async_send_request(context, verb, url, data=data, hdrs=hdrs, client=client)

        token = result.get('access_token') or ""
        if not token:
            error = f"No access token found in response from {url}"
            _LOGGER.debug(error)    # logged as warning after last retry
            raise DabPumpsApiAuthError(error)

        # Step 2: Validate the auth token against the DABPumps Api
        context = f"login DConnect_app validatetoken"
        verb = "GET"
        url = DABPUMPS_API_URL + f"/api/v1/token/validatetoken"
        params = {
            'email': self._username,
            'token': token,
        }

        _LOGGER.debug(f"DAB Pumps validate token via {verb} {url}")
        result = await self._async_send_request(context, verb, url, params=params, client=client)

        # if we reach this point then the token was OK
        # Store returned access-token as cookie so it will automatically be passed in next calls
        client.cookies.set(name=DABPUMPS_API_TOKEN_COOKIE, value=token, domain=DABPUMPS_API_DOMAIN, path='/')
        return client
        

    async def async_login_dconnect_web(self):
        # Step 1: get login url
        # Use a fresh client to keep track of cookies during login and subsequent calls
        client = get_async_client(self._hass)
        client.follow_redirects = True
        client.timeout = 120.0

        _LOGGER.debug(f"DAB Pumps retrieve login page via GET {url}")
        context = f"login DConnect_web home"
        verb = "GET"
        url = DABPUMPS_API_URL
        text = await self._async_send_request(context, verb, url, client=client)
        
        match = re.search(r'action\s?=\s?\"(.*?)\"', text, re.MULTILINE)
        if not match:    
            error = f"Unexpected response while retrieving login url from {url}: {text}"
            _LOGGER.debug(error)    # logged as warning after last retry
            raise DabPumpsApiAuthError(error)
        
        login_url = match.group(1).replace('&amp;', '&')
        login_data = {'username': self._username, 'password': self._password }
        login_hdrs = {'Content-Type': 'application/x-www-form-urlencoded'}
        
        # Step 2: Login
        _LOGGER.debug(f"DAB Pumps login for '{self._username}' via POST {login_url}")
        context = f"login DConnect_web login"
        verb = "POST"
        await self._async_send_request(context, verb, login_url, data=login_data, hdrs=login_hdrs, client=client)

        # if we reach this point without exceptions then login was successfull
        # client access_token is already set by the last call
        return client
        
        
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

        timestamp = datetime.now()
        context = f"installation list"
        verb = "GET"
        url = DABPUMPS_API_URL + '/api/v1/installation'

        _LOGGER.debug(f"DAB Pumps retrieve installation list for '{self._username}' via {verb} {url}")
        (result, request, response) = await self._async_send_request_ex(context, verb, url, diagnostics=False)   

        # only update diagnostics if we actually received data
        # this data is then used as fallback for async_fetch_install_details
        values = result.get('values', [])
        if values and len(values) > 0:
            await self._async_update_diagnostics(timestamp, context, request, response)

        return result


    async def async_fetch_install_details(self, install_id):
        """Get installation details"""

        install_id_org = install_id.removesuffix(SIMULATE_SUFFIX_ID)

        context = f"installation {install_id}"
        verb = "GET"
        url = DABPUMPS_API_URL + f"/api/v1/installation/{install_id_org}"
        
        _LOGGER.debug(f"DAB Pumps retrieve installation details via {verb} {url}")
        result = await self._async_send_request(context, verb, url)
    
        return result


    async def async_fallback_install_details(self, install_id):
        """
        Get installation details saved in history store 'details->installation list'
        """
        _LOGGER.debug(f"DAB Pumps retrieve installation details via history-store for {install_id}")
        if not self._history_store:
            return {}
        
        install_id_org = install_id.removesuffix(SIMULATE_SUFFIX_ID)
        context = f"installation list"
        
        data = await self._history_store.async_get_data() or {}
        installation_list = data.get("details", {}).get(context, {})
        installations = installation_list.get("response", {}).get("json", {}).get("values", [])

        installation = next( (install for install in installations if install.get("installation_id", "") == install_id_org), {})

        return installation


    async def async_fetch_device_config(self, device):
        """Fetch the statusses for a DAB Pumps device, which then constitues the Sensors"""
    
        config_id = device.config_id

        context = f"configuration {config_id}"
        verb = "GET"
        url = DABPUMPS_API_URL + f"/api/v1/configuration/{config_id}"
        # or  DABPUMPS_API_URL + f"/api/v1/configure/paramsDefinition?version=0&doc={config_name}"
        
        _LOGGER.debug(f"DAB Pumps retrieve device config for '{device.name}' via {verb} {url}")
        result = await self._async_send_request(context, verb, url)
        
        return result
        
        
    async def async_fetch_device_statusses(self, device):
        """Fetch the statusses for a DAB Pumps device, which then constitues the Sensors"""
    
        serial = device.serial.removesuffix(SIMULATE_SUFFIX_ID)

        context = f"statusses {serial}"
        verb = "GET"
        url = DABPUMPS_API_URL + f"/dumstate/{serial}"
        # or  DABPUMPS_API_URL + f"/api/v1/dum/{serial}/state"
        
        _LOGGER.debug(f"DAB Pumps retrieve device statusses for '{device.name}' via {verb} {url}")
        result = await self._async_send_request(context, verb, url)
        
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
        result = await self._async_send_request(context, verb, url, json=json, hdrs=hdrs)
        
        # If no exception was thrown then the operation was successfull
        return True
    

    async def async_fetch_strings(self, lang):
        """Get string translations"""
    
        context = f"localization_{lang}"
        verb = "GET"
        url = DABPUMPS_API_URL + f"/resources/js/localization_{lang}.properties?format=JSON"
        
        _LOGGER.debug(f"DAB Pumps retrieve language info via {verb} {url}")
        result = await self._async_send_request(context, verb, url)
    
        return result


    async def _async_send_request(self, context, verb, url, params=None, data=None, json=None, hdrs=None, client=None):
        """GET or POST a request for JSON data"""
        (data, _, _) = await self._async_send_request_ex(context, verb, url, params=params, data=data, json=json, hdrs=hdrs, client=client, diagnostics=True)
        return data
    

    async def _async_send_request_ex(self, context, verb, url, params=None, data=None, json=None, hdrs=None, client=None, diagnostics=True):
        """
        GET or POST a request for JSON data.
        Also returns the request and response performed
        """
        client = client or self._client

        timestamp = datetime.now()
        request = client.build_request(verb, url, params=params, data=data, json=json, headers=hdrs)
        response = await client.send(request)
        
        # Save the diagnostics if requested
        if diagnostics:
            await self._async_update_diagnostics(timestamp, context, request, response)
        
        # Check response
        if not response.is_success:
            error = f"Unable to perform request, got response {response.status_code} {response.reason_phrase} while trying to reach {url}"
            _LOGGER.debug(error)    # logged as warning after last retry
            raise DabPumpsApiError(error)
        
        if not response.headers.get('content-type','').startswith('application/json'):
            return (response.text, request, response)
        
        result = response.json()
        
        # if the result structure contains a 'res' value, then check it
        res = result.get('res', None)
        if res and res != 'OK':
            # BAD RESPONSE: { "res": "ERROR", "code": "FORBIDDEN", "msg": "Forbidden operation", "where": "ROUTE RULE" }
            code = result.get('code', '')
            msg = result.get('msg', '')
            
            if code in ['FORBIDDEN']:
                error = f"Authorization failed: {res} {code} {msg}"
                _LOGGER.debug(error)    # logged as warning after last retry
                raise DabPumpsApiRightsError(error)
            else:
                error = f"Unable to perform request, got response {res} {code} {msg} while trying to reach {url}"
                _LOGGER.debug(error)    # logged as warning after last retry
                raise DabPumpsApiError(error)
        
        return (result, request, response)


    async def async_get_diagnostics(self) -> dict[str, Any]:
        if not self._history_store:
            return None
            
        data = await self._history_store.async_get_data() or {}
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
            "data": {
                "login_method": self._login_method,
            },
            "diagnostics": {
                "counter": calls_counter,
                "percent": calls_percent,
                "history": history,
                "details": details,
            }
        }
    
    
    async def _async_update_diagnostics(self, timestamp, context, request, response, token=None):
        # worker function
        async def _async_worker(self, timestamp, context, request, response, token):
            item = DabPumpsApiHistoryItem(timestamp, context, request, response, token)
            detail = DabPumpsApiHistoryDetail(timestamp, context, request, response, token)
            
            # Persist this history in file instead of keeping in memory
            if not self._history_store:
                return None
            
            data = await self._history_store.async_get_data() or {}
            counter = data.get("counter", {})
            history = data.get("history", [])
            details = data.get("details", {})
            
            if context in counter:
                counter[context] += 1
            else:
                counter[context] = 1
            
            history.append( async_redact_data(item, DIAGNOSTICS_REDACT) )
            if len(history) > 64:
                history.pop(0)
            
            details[context] = async_redact_data(detail, DIAGNOSTICS_REDACT)
            
            data["history"] = history
            data["counter"] = counter
            data["details"] = details
            await self._history_store.async_set_data(data)

        # Create the worker task to update diagnostics in the background,
        # but do not let main loop wait for it to finish
        if self._hass:
            self._hass.async_create_task(_async_worker(self, timestamp, context, request, response, token))


    async def _async_cleanup_diagnostics(self):
        # worker function
        async def _async_worker(self):
            # Sanity check
            if not self._history_store:
                return None
            
            # Only the counter part is reset.
            # We retain the history and details information as we rely on it if communication to DAB Pumps fails.
            data = await self._history_store.async_get_data() or {}
            data["counter"] = {}
            await self._history_store.async_set_data(data)

        # Create the worker task to update diagnostics in the background,
        # but do not let main loop wait for it to finish
        if self._hass:
            self._hass.async_create_task(_async_worker(self))


class DabPumpsApiAuthError(Exception):
    """Exception to indicate authentication failure."""

class DabPumpsApiRightsError(Exception):
    """Exception to indicate authorization failure"""

class DabPumpsApiError(Exception):
    """Exception to indicate generic error failure."""    
    
    
class DabPumpsApiHistoryStore(Store[dict]):
    
    _STORAGE_VERSION_MAJOR = 2
    _STORAGE_VERSION_MINOR = 0
    _STORAGE_KEY_HISTORY = DOMAIN + ".api_history"
    
    def __init__(self, hass, key):
        super().__init__(
            hass, 
            key=self._STORAGE_KEY_HISTORY, 
            version=self._STORAGE_VERSION_MAJOR, 
            minor_version=self._STORAGE_VERSION_MINOR
        )
        self._key = key

    
    async def _async_migrate_func(self, old_major_version, old_minor_version, old_data):
        """Migrate the history store data"""

        if old_major_version <= 1:
            # version 1 had a flat structure and did not take into account to have multiple installations (with different username+password)
            old_data = {
                self._key: old_data
            }

        if old_major_version <= 2:
            # version 2 is the current version. No migrate needed
            data = old_data

        return data
    

    async def async_get_data(self):
        """Load the persisted api_history file and return the data specific for this api instance"""
        data = await super().async_load() or {}
        data_self = data.get(self._key, {})

        return data_self
    

    async def async_set_data(self, data_self):
        """Save the data specific for this api instance into the persisted api_history file"""
        data = await super().async_load() or {}
        data[self._key] = data_self

        await super().async_save(data)


class DabPumpsApiHistoryItem(dict):
    def __init__(self, timestamp, context, request, response, token):
        item = { 
            "ts": timestamp, 
            "op": context,
        }

        # If possible, add a summary of the response status_code and json res and code
        if response:
            rsp = []
            rsp.append(f"{response.status_code} {response.reason_phrase}")
            
            if response.is_success and response.headers.get('content-type','').startswith('application/json'):
                json = response.json()

                if res := json.get('res', ''): 
                    rsp.append(f"res={res}")
                if code := json.get('code', ''): 
                    rsp.append(f"code={code}")
                if msg := json.get('msg', ''):
                    rsp.append(f"msg={msg}")
                if details := json.get('details', ''):
                    rsp.append(f"details={details}")

            item["rsp"] = ', '.join(rsp)

        # add as new history item
        super().__init__(item)


class DabPumpsApiHistoryDetail(dict):
    def __init__(self, timestamp, context, request, response, token):
        item = { 
            "ts": timestamp, 
        }

        if request:
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

            item["req"] = req
        
        if response:
            res = {
                "status": f"{response.status_code} {response.reason_phrase}",
                "headers": response.headers,
                "elapsed": response.elapsed.total_seconds(),
            }

            if response.is_success and response.headers.get('content-type','').startswith('application/json'):
                res['json'] = response.json()

            item["res"] = res

        if token:
            item["token"] = token

        super().__init__(item)
