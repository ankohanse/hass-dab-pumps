"""api.py: DabPumps API for DAB Pumps integration."""

import aiohttp
import asyncio

import jwt
import logging
import re
import time

from collections import namedtuple
from datetime import datetime
from typing import Any
from yarl import URL

from homeassistant.components.diagnostics import REDACTED
from homeassistant.components.diagnostics.util import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.storage import Store


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
        self._login_method = None

        # Client to keep track of cookies during login and subsequent calls
        # We keep the same client for the whole life of the api instance.
        self._client:aiohttp.ClientSession = async_create_clientsession(self._hass)  

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
        """Login to DAB Pumps by trying each of the possible login methods"""

        # Step 0: do we still have a cookie with a non-expired auth token?
        cookie = self._client.cookie_jar.filter_cookies(URL(DABPUMPS_API_URL)).get(DABPUMPS_API_TOKEN_COOKIE, None)
        if cookie:
            token_payload = jwt.decode(jwt=cookie.value, options={"verify_signature": False})
            
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
                        await self.async_login_dablive_app(isDabLive=1)
                    case API_LOGIN.DABLIVE_APP_0:
                        # Try the alternative simplest method
                        await self.async_login_dablive_app(isDabLive=0)
                    case API_LOGIN.DCONNECT_APP:
                        # Try the method that uses 2 steps
                        await self.async_login_dconnect_app()
                    case API_LOGIN.DCONNECT_WEB:
                        # Finally try the most complex and unreliable one
                        await self.async_login_dconnect_web()
                    case _:
                        # No previously known login method was set yet
                        continue

                # if we reached this point then a login method succeeded
                # keep using this client and its cookies and remember which method had success
                _LOGGER.debug(f"DAB Pumps login succeeded using method {method}")
                self._login_method = method  
                return 
            
            except Exception as ex:
                error = ex

            # Clear any login cookies before the next try
            await self.async_logout()

        # if we reached this point then all methods failed.
        if error:
            raise error
        

    async def async_login_dablive_app(self, isDabLive=1):
        """Login to DAB Pumps via the method as used by the DAB Live app"""

        # Step 1: get authorization token
        context = f"login DabLive_app (isDabLive={isDabLive})"
        request = {
            "method": "POST",
            "url": DABPUMPS_API_URL + f"/auth/token",
            "params": {
                'isDabLive': isDabLive,     # required param, though actual value seems to be completely ignored
            },
            "headers": {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            "data": {
                'username': self._username, 
                'password': self._password,
            },
        }
        
        _LOGGER.debug(f"DAB Pumps login for '{self._username}' via {request["method"]} {request["url"]}")
        result = await self._async_send_request(context, request)

        token = result.get('access_token') or ""
        if not token:
            error = f"No access token found in response from {request["url"]}"
            _LOGGER.debug(error)    # logged as warning after last retry
            raise DabPumpsApiAuthError(error)

        # if we reach this point then the token was OK
        # Store returned access-token as cookie so it will automatically be passed in next calls
        self._client.cookie_jar.update_cookies( { DABPUMPS_API_TOKEN_COOKIE: token }, URL(DABPUMPS_API_URL) )

        
    async def async_login_dconnect_app(self):
        """Login to DAB Pumps via the method as used by the DConnect app"""

        # Step 1: get authorization token
        context = f"login DConnect_app"
        request = {
            "method": "POST",
            "url": DABPUMPS_SSO_URL + f"/auth/realms/dwt-group/protocol/openid-connect/token",
            "headers": {
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            "data": {
                'client_id': 'DWT-Dconnect-Mobile',
                'client_secret': 'ce2713d8-4974-4e0c-a92e-8b942dffd561',
                'scope': 'openid',
                'grant_type': 'password',
                'username': self._username, 
                'password': self._password 
            },
        }
        
        _LOGGER.debug(f"DAB Pumps login for '{self._username}' via {request["method"]} {request["url"]}")
        result = await self._async_send_request(context, request)

        token = result.get('access_token') or ""
        if not token:
            error = f"No access token found in response from {request["url"]}"
            _LOGGER.debug(error)    # logged as warning after last retry
            raise DabPumpsApiAuthError(error)

        # Step 2: Validate the auth token against the DABPumps Api
        context = f"login DConnect_app validatetoken"
        request = {
            "method": "GET",
            "url": DABPUMPS_API_URL + f"/api/v1/token/validatetoken",
            "params": { 
                'email': self._username,
                'token': token,
            }
        }

        _LOGGER.debug(f"DAB Pumps validate token via {request["method"]} {request["url"]}")
        result = await self._async_send_request(context, request)

        # if we reach this point then the token was OK
        # Store returned access-token as cookie so it will automatically be passed in next calls
        self._client.cookie_jar.update_cookies( { DABPUMPS_API_TOKEN_COOKIE: token }, URL(DABPUMPS_API_URL) )
       

    async def async_login_dconnect_web(self):
        """Login to DAB Pumps via the method as used by the DConnect website"""

        # Step 1: get login url
        context = f"login DConnect_web home"
        request = {
            "method": "GET",
            "url": DABPUMPS_API_URL,
        }

        _LOGGER.debug(f"DAB Pumps retrieve login page via GET {request["url"]}")
        text = await self._async_send_request(context, request)
        
        match = re.search(r'action\s?=\s?\"(.*?)\"', text, re.MULTILINE)
        if not match:    
            error = f"Unexpected response while retrieving login url from {request["url"]}: {text}"
            _LOGGER.debug(error)    # logged as warning after last retry
            raise DabPumpsApiAuthError(error)
        
        # Step 2: Login
        context = f"login DConnect_web login"
        request = {
            "method": "POST",
            "url": match.group(1).replace('&amp;', '&'),
            "headers": {
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            "data": {
                'username': self._username, 
                'password': self._password 
            },
        }
        
        _LOGGER.debug(f"DAB Pumps login for '{self._username}' via {request["method"]} {request["url"]}")
        await self._async_send_request(context, request)

        # if we reach this point without exceptions then login was successfull
        # client access_token is already set by the last call
        
        
    async def async_logout(self):
        """Logout from DAB Pumps"""

        # Home Assistant will issue a warning when calling aclose() on the async aiohttp client.
        # Instead of closing we will simply forget all cookies. The result is that on a next
        # request, the client will act like it is a new one.
        self._client.cookie_jar.clear()
        
        
    async def async_fetch_install_list(self):
        """Get installation list"""

        timestamp = datetime.now()
        context = f"installation list"
        request = {
            "method": "GET",
            "url": DABPUMPS_API_URL + '/api/v1/installation',
        }

        _LOGGER.debug(f"DAB Pumps retrieve installation list for '{self._username}' via {request["method"]} {request["url"]}")
        (result, request, response) = await self._async_send_request_ex(context, request, diagnostics=False)   

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
        request = {
            "method": "GET",
            "url": DABPUMPS_API_URL + f"/api/v1/installation/{install_id_org}",
        }
        
        _LOGGER.debug(f"DAB Pumps retrieve installation details via {request["method"]} {request["url"]}")
        result = await self._async_send_request(context, request)
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
        installations = installation_list.get("rsp", {}).get("json", {}).get("values", [])

        installation = next( (install for install in installations if install.get("installation_id", "") == install_id_org), {})
        return installation


    async def async_fetch_device_config(self, device):
        """Fetch the statusses for a DAB Pumps device, which then constitues the Sensors"""
    
        config_id = device.config_id

        context = f"configuration {config_id}"
        request = {
            "method": "GET",
            "url":  DABPUMPS_API_URL + f"/api/v1/configuration/{config_id}",
            # or    DABPUMPS_API_URL + f"/api/v1/configure/paramsDefinition?version=0&doc={config_name}",
        }
        
        _LOGGER.debug(f"DAB Pumps retrieve device config for '{device.name}' via {request["method"]} {request["url"]}")
        result = await self._async_send_request(context, request)
        return result
        
        
    async def async_fetch_device_statusses(self, device):
        """Fetch the statusses for a DAB Pumps device, which then constitues the Sensors"""
    
        serial = device.serial.removesuffix(SIMULATE_SUFFIX_ID)

        context = f"statusses {serial}"
        request = {
            "method": "GET",
            "url": DABPUMPS_API_URL + f"/dumstate/{serial}",
            # or   DABPUMPS_API_URL + f"/api/v1/dum/{serial}/state",
        }
        
        _LOGGER.debug(f"DAB Pumps retrieve device statusses for '{device.name}' via {request["method"]} {request["url"]}")
        result = await self._async_send_request(context, request)
        return result
        
        
    async def async_change_device_status(self, status, value):
        """Set a new status value for a DAB Pumps device"""

        serial = status.serial.removesuffix(SIMULATE_SUFFIX_ID)
        
        context = f"set {serial}:{status.key}"
        request = {
            "method": "POST",
            "url": DABPUMPS_API_URL + f"/dum/{serial}",
            "headers": {
                'Content-Type': 'application/json'
            },
            "json": {
                'key': status.key, 
                'value': str(value) 
            },
        }
        
        _LOGGER.debug(f"DAB Pumps set device param for '{status.unique_id}' to '{value}' via {request["method"]} {request["url"]}")
        result = await self._async_send_request(context, request)
        
        # If no exception was thrown then the operation was successfull
        return True
    

    async def async_fetch_strings(self, lang):
        """Get string translations"""
    
        context = f"localization_{lang}"
        request = {
            "method": "GET",
            "url": DABPUMPS_API_URL + f"/resources/js/localization_{lang}.properties?format=JSON",
        }
        
        _LOGGER.debug(f"DAB Pumps retrieve language info via {request["method"]} {request["url"]}")
        result = await self._async_send_request(context, request)
        return result


    async def _async_send_request(self, context, request):
        """GET or POST a request for JSON data"""
        (data, _, _) = await self._async_send_request_ex(context, request, diagnostics=True)
        return data
    

    async def _async_send_request_ex(self, context, request, diagnostics=True):
        """
        GET or POST a request for JSON data.
        Also returns the request and response performed
        """
        # Perform the http request
        timestamp = datetime.now()
        async with self._client.request(
            method = request["method"], 
            url = request["url"],
            params = request.get("params", None), 
            data = request.get("data", None), 
            json = request.get("json", None), 
            headers = request.get("headers", None),
        ) as rsp:

            # Remember actual requests and response params, used for diagnostics
            request["headers"] = rsp.request_info.headers
            response = {
                "status": f"{rsp.status} {rsp.reason}",
                "headers": rsp.headers,
                "elapsed": (datetime.now() - timestamp).total_seconds(),
            }
            if rsp.ok and rsp.headers.get('content-type','').startswith('application/json'):
                json = response["json"] = await rsp.json()
                text = None
            else:
                text = response["text"] = await rsp.text()
                json = None
            
            # Save the diagnostics if requested
            if diagnostics:
                await self._async_update_diagnostics(timestamp, context, request, response)
            
            # Check response
            if not rsp.ok:
                error = f"Unable to perform request, got response {response["status"]} while trying to reach {request["url"]}"
                _LOGGER.debug(error)    # logged as warning after last retry
                raise DabPumpsApiError(error)
            
            if text is not None:
                return (text, request, response)
            
            # if the result structure contains a 'res' value, then check it
            res = json.get('res', None)
            if res and res != 'OK':
                # BAD RESPONSE: { "res": "ERROR", "code": "FORBIDDEN", "msg": "Forbidden operation", "where": "ROUTE RULE" }
                code = json.get('code', '')
                msg = json.get('msg', '')
                
                if code in ['FORBIDDEN']:
                    error = f"Authorization failed: {res} {code} {msg}"
                    _LOGGER.debug(error)    # logged as warning after last retry
                    raise DabPumpsApiRightsError(error)
                else:
                    error = f"Unable to perform request, got response {res} {code} {msg} while trying to reach {request["url"]}"
                    _LOGGER.debug(error)    # logged as warning after last retry
                    raise DabPumpsApiError(error)
            
            return (json, request, response)


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
    
    
    async def _async_update_diagnostics(self, timestamp, context: str, request: dict|None, response: dict|None, token: dict|None = None):
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
    def __init__(self, timestamp, context: str , request: dict|None, response: dict|None, token: dict|None):
        item = { 
            "ts": timestamp, 
            "op": context,
        }

        # If possible, add a summary of the response status and json res and code
        if response:
            rsp = []
            rsp.append(response["status"])
            
            if json := response.get("json", None):
                if res := json.get('res', ''): rsp.append(f"res={res}")
                if code := json.get('code', ''): rsp.append(f"code={code}")
                if msg := json.get('msg', ''): rsp.append(f"msg={msg}")
                if details := json.get('details', ''): rsp.append(f"details={details}")

            item["rsp"] = ', '.join(rsp)

        # add as new history item
        super().__init__(item)


class DabPumpsApiHistoryDetail(dict):
    def __init__(self, timestamp, context: str, request: dict|None, response: dict|None, token: dict|None):
        item = { 
            "ts": timestamp, 
        }

        if request:
            item["req"] = request
        if response:
            item["rsp"] = response
        if token:
            item["token"] = token

        super().__init__(item)
