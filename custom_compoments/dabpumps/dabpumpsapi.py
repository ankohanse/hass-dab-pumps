"""resolapi.py: DabPumps API for DAB Pumps integration."""

import asyncio
import datetime
import json
import logging
import math
import re
import httpx

from collections import namedtuple
from homeassistant.components.sensor import SensorStateClass
from homeassistant.exceptions import IntegrationError

from httpx import RequestError, TimeoutException

from .const import (
    DABPUMPS_API_URL,
)


_LOGGER = logging.getLogger(__name__)


DabPumpsDevice = namedtuple('DabPumpsDevice',   'serial, id, name, vendor, product, version, build, installation_id, installation_name, installation_timezone')
DabPumpsStatus = namedtuple('DabPumpsStatus', 'serial, unique_id, key, val')


# DabPumpsAPI to detect device and get device info, fetch the actual data from the Resol device, and parse it
class DabPumpsApi:
    def __init__(self, username, password):
        self.device_map = {}
        self.status_map = {}
        
        self._username = username
        self._password = password
        self._client = None

    async def async_detect_devices(self):
        await self._async_detect(False)
        return self.device_map 
        
    async def async_detect_device_statusses(self):
        await self._async_detect(True)
        return (self.device_map, self.status_map)
    
    async def _async_detect(self, include_status):
        error = None
        for retry in range(2, 0, -1):
            try:
                success = await self._async_login()
                    
                if success:
                    success = await self._async_fetch_devices()
                    
                if success and include_status:
                    fetches = [self._async_fetch_device_statusses(device) for device in self.device_map.values()]
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
            url = DABPUMPS_API_URL + '/logout'
            
            _LOGGER.debug(f"DAB Pumps logout via GET {url}")
            response = await self._client.get(url)
            
            if (not response.is_success):
                error = f"Unable to logout, got response {response.status_code} while trying to reach {url}"
                # ignore and continue
            
            # Forget our current session so we are forced to do a fresh login in a next retry
            await self._client.aclose()
            self._client = None
            
        return True
        
        
    async def _async_fetch_devices(self):
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
                error = f"Authentication failed: {result['res']} {result['code']} {result['msg']}"
                _LOGGER.debug(error)    # logged as warning after last retry
                raise DabPumpsApiAuthError(error)
            else:
                error = f"Unable retrieve installations, got response {result['res']} {result['code']} {result['msg']} while trying to reach {url}"
                _LOGGER.debug(error)    # logged as warning after last retry
                raise DabPumpsApiError(error)
            
        # Get device data for each installation
        device_map = {}
        data = result.get('data', {})
        installation_map = data.get('installation_map', {})
        for installation in installation_map.values():
            
            _LOGGER.debug(f"DAB Pumps installation found: {installation.get('name', '<unknown>'),}")
            
            for dum in installation.get('dums', []):
                device = DabPumpsDevice(
                    vendor = 'DAB Pumps',
                    name = dum.get('name', ''),
                    id = DabPumpsApi.create_id(dum.get('name', '')),
                    serial = dum.get('serial', ''),
                    product = dum.get('distro_embedded', dum.get('distro', '')),
                    version = dum.get('version_embedded', dum.get('version', '')),
                    build = dum.get('channel_embedded', dum.get('channel', '')),
                    installation_id = installation.get('installation_id', ''),
                    installation_name = installation.get('name', ''),
                    installation_timezone = installation.get('timezone', ''),
                )
                device_map[device.serial] = device
                
                _LOGGER.debug(f"DAB Pumps device found: {device.name} with serial {device.serial}")
        
        self.device_map = device_map
        self.status_map = {}
        return True


    # Fetch the statusses for a DAB Pumps device, which then constitues the Sensors
    async def _async_fetch_device_statusses(self, device):

        url = DABPUMPS_API_URL + f"/dumstate/{device.serial}"

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
                error = f"Authentication failed: {result['res']} {result['code']} {result['msg']}"
                _LOGGER.debug(error)    # logged as warning after last retry
                raise DabPumpsApiAuthError(error)
            else:
                error = f"Unable retrieve device data, got response {result['res']} {result['code']} {result['msg']} while trying to reach {url}"
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
        self.status_map.update(status_map)
        return True
    
    
    @staticmethod
    def create_id(*args):
        str = '_'.join(args)
        str = re.sub(' ', '_', str)
        str = re.sub('[^a-z0-9_]+', '', str.lower())
        return str        

    
class DabPumpsApiAuthError(Exception):
    """Exception to indicate authentication failure."""


class DabPumpsApiError(Exception):
    """Exception to indicate generic error failure."""    