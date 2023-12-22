"""resolapi.py: DabPumps API for DAB Pumps integration."""

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
)


_LOGGER = logging.getLogger(__name__)

# Debug: set this constant to True to simulate a configuration with multiple installations for one DAB account
SIMULATE_MULTI_INSTALL = False
SIMULATE_SUFFIX_ID = "_test"
SIMULATE_SUFFIX_NAME = " (test)"


# Get a stored instance of the DabPumpsApi for given credentials
def get_dabpumpsapi(hass: HomeAssistant, username, password):

    # if a DabPumpsApi instance for these credentials is already available then e-use it
    if hass:
        if not API in hass.data[DOMAIN]:
            hass.data[DOMAIN][API] = {}
            
        key = f"{username.lower()}_{hash(password) % 10**8}"
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


DabPumpsInstall = namedtuple('DabPumpsInstall', 'id, name, description, company, address, timezone, devices')
DabPumpsDevice = namedtuple('DabPumpsDevice', 'serial, id, name, vendor, product, version, build, install_id')
DabPumpsStatus = namedtuple('DabPumpsStatus', 'serial, unique_id, key, val')


# DabPumpsAPI to detect device and get device info, fetch the actual data from the Resol device, and parse it
class DabPumpsApi:
    def __init__(self, username, password):
        self._install_map_ts = datetime.min
        self._install_map = {}
        self._device_map = {}
        self._status_map = {}
        
        self._username = username
        self._password = password
        self._client = None
    
    
    async def async_detect_installs(self):
        await self._async_detect(None)
        return self._install_map
    
    
    async def async_detect_install_statusses(self, install_id):
        await self._async_detect(install_id)
        return (self._device_map, self._status_map)
    
    
    async def _async_detect(self, install_id):
        error = None
        for retry in range(2, 0, -1):
            try:
                success = await self._async_login()
                    
                # Fetch a new list of installations when the cached one expires
                if success and (datetime.now() - self._install_map_ts).total_seconds() > 3600:
                    success = await self._async_fetch_installs()

                # Retrieve the devices and statusses when an install_id is given
                if success and install_id:
                    fetches = [self._async_fetch_device_statusses(device) for device in self._device_map.values() if device.install_id == install_id ]
                    results = await asyncio.gather(*fetches)
                    success = all(results)

                if (success):
                    return True;
            
            except DabPumpsApiAuthError:
                error = f"Please re-check your username and password in your configuration!"
    
            except DabPumpsApiError:
                error = f"Unable to connect to dconnect.dabpumps.com."

            # Log off, end session and retry if possible
            await self._async_logout();  
            if retry > 0:
                _LOGGER.debug(f"Failed to retrieve devices and statusses. Retrying")
            
        if error:
            _LOGGER.warning(error)

        return False

        
    async def _async_login(self):
        if (self._client):
            return True
        
        # Use a fresh client to keep track of cookies during login and subsequent calls
        client = httpx.AsyncClient(follow_redirects=True, timeout=60.0)
        
        # Step 1: get login url
        url = DABPUMPS_API_URL
        _LOGGER.debug(f"DAB Pumps retrieve login page via GET {url}")
        response = await client.get(url)

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
        
        if (not response.is_success):
            error = f"Unable to login, got response {response.status_code}"
            raise DabPumpsApiAuthError(error)

        # remember this session so we re-use any cookies for subsequent calls
        self._client = client
        return True
        
        
    async def _async_logout(self):
        if self._client:
            try:
                url = DABPUMPS_API_URL + '/logout'
                
                _LOGGER.debug(f"DAB Pumps logout via GET {url}")
                response = await self._client.get(url)
                
                if (not response.is_success):
                    error = f"Unable to logout, got response {response.status_code} while trying to reach {url}"
                    # ignore and continue
                
                # Forget our current session so we are forced to do a fresh login in a next retry
                await self._client.aclose()
            finally:
                self._client = None
            
        return True
        
        
    async def _async_fetch_installs(self):
        # Get installation data
        url = DABPUMPS_API_URL + '/api/v1/gui/installation/list?lang=en'

        _LOGGER.debug(f"DAB Pumps retrieve installation info via GET {url}")
        response = await self._client.get(url)
        
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
            
        # Get device data for each installation
        install_map = {}
        device_map = {}
        
        data = result.get('data', {})
        installation_map = data.get('installation_map', {})
        
        # Go through the list of installations twice,
        # the second one to generate an extra dummy install for testing purposes
        for test in [0,1]:
            if test and not SIMULATE_MULTI_INSTALL:
                break
            
            suffix_id = SIMULATE_SUFFIX_ID if test else ""
            suffix_name = SIMULATE_SUFFIX_NAME if test else ""
            
            for ins_idx, installation in enumerate(installation_map.values()):
                
                ins_id = installation.get('installation_id', '')
                ins_name = installation.get('name', None) or installation.get('description', None) or f"installation {ins_idx}"
                
                install_id = DabPumpsApi.create_id(ins_id + suffix_id)
                install_name = ins_name + suffix_name
                install_devices = []

                _LOGGER.debug(f"DAB Pumps installation found: {install_name}")
                    
                for dum_idx, dum in enumerate(installation.get('dums', [])):
                    
                    dum_serial = dum.get('serial', '')
                    dum_name = dum.get('name', None) or dum.get('distro_embedded', None) or dum.get('distro', None) or f"device {dum_idx}"

                    device_id = DabPumpsApi.create_id(dum_name + suffix_id)
                    device_serial = dum_serial + suffix_id
                    device_name = dum_name + suffix_name

                    device = DabPumpsDevice(
                        vendor = 'DAB Pumps',
                        name = device_name,
                        id = device_id,
                        serial = device_serial,
                        product = dum.get('distro_embedded', None) or dum.get('distro', None) or '',
                        version = dum.get('version_embedded', None) or dum.get('version', None) or '',
                        build = dum.get('channel_embedded', None) or dum.get('channel', None) or '',
                        install_id = install_id,
                    )
                    device_map[device_serial] = device
                    install_devices.append(device_serial)
                    
                    _LOGGER.debug(f"DAB Pumps device found: {device_name} with serial {device_serial}")
                    
                install = DabPumpsInstall(
                    id = install_id,
                    name = install_name,
                    description = installation.get('description', None) or '',
                    company = installation.get('company', None) or '',
                    address = installation.get('address', None) or '',
                    timezone = installation.get('timezone', None) or '',
                    devices = install_devices
                )
                install_map[install_id] = install
                
                if test:
                    break
        
        # Cleanup statusses to only keep values that are still part of an install
        status_map = { k: v for k, v in self._status_map.items() if v.serial in device_map }
        
        self._install_map_ts = datetime.now()
        self._install_map = install_map
        self._device_map = device_map
        self._status_map = status_map
        return True


    # Fetch the statusses for a DAB Pumps device, which then constitues the Sensors
    async def _async_fetch_device_statusses(self, device):

        url = DABPUMPS_API_URL + f"/dumstate/{device.serial.removesuffix(SIMULATE_SUFFIX_ID)}"
        
        _LOGGER.debug(f"DAB Pumps retrieve device statusses for '{device.name}' via GET {url}")
        response = await self._client.get(url)
        
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
        
        status_map = {}
        status = json.loads(result['status'])
        for item_key, item_val in status.items():
            # the value 'h' is used when a property is not available/supported
            if item_val=='h':
                continue
            
            # Item Entity ID is combination of device serial and each field unique name as internal sensor hash
            # Item Unique ID is a more readable version
            entity_id = DabPumpsApi.create_id(device.serial, item_key)
            unique_id = DabPumpsApi.create_id(device.name, item_key)

            # Add it to our statusses
            item = DabPumpsStatus(
                serial = device.serial,
                unique_id = unique_id,
                key = item_key,
                val = item_val,
            )
            status_map[entity_id] = item
        
        # Merge with statusses from other devices
        self._status_map.update(status_map)
        return True
    
    
    def get_diagnostics(self) -> dict[str, Any]:
        install_map = { k: v._asdict() for k,v in self._install_map.items() }
        device_map = { k: v._asdict() for k,v in self._device_map.items() }
        status_map = { k: v._asdict() for k,v in self._status_map.items() }
        
        return {
            "username": self._username,
            "password": self._password,
            "diagnostics_ts": datetime.now(),
            "install_map_ts": self._install_map_ts,
            "install_map": install_map,
            "device_map": device_map,
            "status_map": status_map,
        }
    
    
    @staticmethod
    def create_id(*args):
        str = '_'.join(args).strip('_')
        str = re.sub(' ', '_', str)
        str = re.sub('[^a-z0-9_-]+', '', str.lower())
        return str        

    
class DabPumpsApiAuthError(Exception):
    """Exception to indicate authentication failure."""


class DabPumpsApiError(Exception):
    """Exception to indicate generic error failure."""    