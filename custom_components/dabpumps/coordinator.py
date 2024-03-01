import asyncio
import async_timeout
import logging
import re

from collections import namedtuple
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.diagnostics import REDACTED
from homeassistant.components.diagnostics.util import async_redact_data
from homeassistant.components.light import LightEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
)
from .api import (
    DabPumpsApiFactory,
    DabPumpsApi,
    DabPumpsApiAuthError,
    DabPumpsApiError,
)

from .const import (
    DOMAIN,
    NAME,
    COORDINATOR,
    DEFAULT_POLLING_INTERVAL,
    CONF_INSTALL_ID,
    CONF_INSTALL_NAME,
    CONF_OPTIONS,
    CONF_POLLING_INTERVAL,
    DIAGNOSTICS_REDACT,
    API_RETRY_ATTEMPTS,
    API_RETRY_DELAY,
    SIMULATE_MULTI_INSTALL,
    SIMULATE_SUFFIX_ID,
    SIMULATE_SUFFIX_NAME,
)


_LOGGER = logging.getLogger(__name__)

DabPumpsInstall = namedtuple('DabPumpsInstall', 'id, name, description, company, address, timezone, devices')
DabPumpsDevice = namedtuple('DabPumpsDevice', 'serial, id, name, vendor, product, version, build, config_id, install_id')
DabPumpsConfig = namedtuple('DabPumpsConfig', 'label, description, meta_params')
DabPumpsParams = namedtuple('DabPumpsParams', 'key, type, unit, weight, values, min, max, family, group, view, change, log, report')
DabPumpsStatus = namedtuple('DabPumpsStatus', 'serial, unique_id, key, val')
DabPumpsMessages = namedtuple('DabPumpsMessages', 'key, val')


class DabPumpsCoordinatorFactory:
    
    @staticmethod
    def create(hass: HomeAssistant, config_entry: ConfigEntry):
        """
        Get existing Coordinator for a config entry, or create a new one if it does not yet exist
        """
    
        # Get properties from the config_entry
        username = config_entry.data[CONF_USERNAME]
        password = config_entry.data[CONF_PASSWORD]
        install_id = config_entry.data[CONF_INSTALL_ID]
        install_name = config_entry.data[CONF_INSTALL_NAME]
        options = config_entry.options
        
        if not COORDINATOR in hass.data[DOMAIN]:
            hass.data[DOMAIN][COORDINATOR] = {}
            
        # already created?
        coordinator = hass.data[DOMAIN][COORDINATOR].get(install_id, None)
        if not coordinator:
            # Get an instance of the DabPumpsApi for these credentials
            # This instance may be shared with other coordinators that use the same credentials
            api = DabPumpsApiFactory.create(hass, username, password)
        
            # Get an instance of our coordinator. This is unique to this install_id
            coordinator = DabPumpsCoordinator(hass, api, install_id, options)
            hass.data[DOMAIN][COORDINATOR][install_id] = coordinator
            
        return coordinator

    @staticmethod
    def create_temp(username, password):
        """
        Get temporary Coordinator for a given username+password.
        This coordinator will only provide limited functionality
        """
    
        # Get properties from the config_entry
        hass = None
        install_id = None
        options = {}
        
        # Get a temporary instance of the DabPumpsApi for these credentials
        api = DabPumpsApiFactory.create(hass, username, password)
        
        # Get an instance of our coordinator. This is unique to this install_id
        coordinator = DabPumpsCoordinator(hass, api, install_id, options)
        return coordinator
    

class DabPumpsCoordinator(DataUpdateCoordinator):
    """My custom coordinator."""
    
    def __init__(self, hass, api, install_id, options):
        """Initialize my coordinator."""
        if hass:
            super().__init__(
                hass,
                _LOGGER,
                # Name of the data. For logging purposes.
                name=NAME,
                # Polling interval. Will only be polled if there are subscribers.
                update_interval=timedelta(seconds=options.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL)),
                update_method=self._async_update_data,
            )

        self._api = api
        self._install_id = install_id
        self._options = options
        self._language = 'en'

        self._install_map_ts = datetime.min
        self._install_map = {}
        self._device_map_ts = datetime.min
        self._device_map = {}
        self._config_map_ts = datetime.min
        self._config_map = {}
        self._status_map_ts = datetime.min
        self._status_map = {}
        self._string_map_ts = datetime.min
        self._string_map = {}
        
        # retry counter for diagnosis
        self._retries_needed = [ 0 for r in range(API_RETRY_ATTEMPTS) ]

    
    @property
    def string_map(self):
        return self._string_map


    async def async_config_flow_data(self):
        """
        Fetch installation data from API.
        """
        _LOGGER.debug(f"Config flow data")
        
        try:
            async with async_timeout.timeout(60):
                success = await self._async_detect_data()
                
                _LOGGER.debug(f"install_map: {self._install_map}")
                return (self._install_map)
            
        except asyncio.TimeoutError as err:
            raise UpdateFailed(f"Timeout while communicating with API: {err}")
    
    
    async def _async_update_data(self):
        """
        Fetch sensor data from API.
        
        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        _LOGGER.debug(f"Update data")

        try:
            # Note: asyncio.TimeoutError and aiohttp.ClientError are already
            # handled by the data update coordinator.
            async with async_timeout.timeout(60):
                success = await self._async_detect_data()
                
                _LOGGER.debug(f"device_map: {self._device_map}")
                _LOGGER.debug(f"config_map: {self._config_map}")
                _LOGGER.debug(f"status_map: {self._status_map}")
                return (self._device_map, self._config_map, self._status_map)
        
        except asyncio.TimeoutError as err:
            raise UpdateFailed(f"Timeout while communicating with API: {err}")
    
    
    async def async_modify_data(self, object_id, value):
        """
        Set an entity param via the API.
        """
        status = self._status_map.get(object_id)
        if not status:
            # Not found
            return False
            
        if status.val == value:
            # Not changed
            return False
        
        _LOGGER.debug(f"Set {status.unique_id} from {status.val} to {value}")
        
        # update the cached value in status_map
        status = status._replace(val=value)
        self._status_map[object_id] = status
        
        # update the remote value
        try:
            async with async_timeout.timeout(60):
                success = await self._async_change_device_status(status, value)
                return success
        
        except asyncio.TimeoutError as err:
            raise UpdateFailed(f"Timeout while communicating with API: {err}")
    
    
    async def _async_detect_data(self):
        error = None
        for retry in range(0, API_RETRY_ATTEMPTS):
            try:
                success = await self._api.async_login()
                    
                # Fetch a new list of installations when the cached one expires
                if success and (datetime.now() - self._install_map_ts).total_seconds() > 3600:
                    data = await self._api.async_fetch_installs()
                    success = self._process_installs_data(data)
                
                if success and (datetime.now() - self._string_map_ts).total_seconds() > 86400:
                    data = await self._api.async_fetch_strings(self._language)
                    success = self._process_strings_data(data)
                
                # Retrieve the devices and statusses when an install_id is given
                if success and self._install_id:
                    for device in self._device_map.values():
                        if device.install_id == self._install_id:
                            data = await self._api.async_fetch_device_statusses(device)
                            succ = self._process_device_status_data(device, data)
                            success = success and succ
                
                if (success):
                    self._retries_needed[retry] += 1
                    return True;
            
            except DabPumpsApiAuthError:
                error = f"Unable to authenticate to dconnect.dabpumps.com. Please re-check your username and password in your configuration!"
            
            except DabPumpsApiError as dpae:
                error = f"Failed to retrieve data. {dpae}"
            
            except Exception as ex:
                error = f"Failed communication to DAB Pumps server. {ex}"
            
            # Log off, end session and retry if possible
            await self._api.async_logout();  
            
            if retry < API_RETRY_ATTEMPTS:
                if retry < 2:
                    _LOGGER.info(f"Retry {retry+1} in {API_RETRY_DELAY} seconds. {error}")
                else:
                    _LOGGER.warn(f"Retry {retry+1} in {API_RETRY_DELAY} seconds. {error}")
                await asyncio.sleep(API_RETRY_DELAY)
            
        if error:
            _LOGGER.warning(error)
        
        self._retries_needed[retry] += 1
        return False
    
        
    async def _async_change_device_status(self, status, value):
        error = None
        for retry in range(1, API_RETRY_ATTEMPTS):
            try:
                success = await self._api.async_login()
                
                # Fetch a new list of installations when the cached one expires
                if success:
                    success = await self._api.async_change_device_status(status, value)

                if (success):
                    self._retries_needed[retry] += 1
                    return True;
            
            except DabPumpsApiAuthError:
                error = f"Unable to authenticate to dconnect.dabpumps.com. Please re-check your username and password in your configuration!"
            
            except DabPumpsApiError as dpae:
                error = f"Failed to set devices param. {dpae}"
            
            except Exception as ex:
                error = f"Failed communication to DAB Pumps server. {ex}"
            
            # Log off, end session and retry if possible
            await self._api.async_logout();  
            
            if retry < API_RETRY_ATTEMPTS:
                if retry < 2:
                    _LOGGER.info(f"Retry {retry+1} in {API_RETRY_DELAY} seconds. {error}")
                else:
                    _LOGGER.warn(f"Retry {retry+1} in {API_RETRY_DELAY} seconds. {error}")
                await asyncio.sleep(API_RETRY_DELAY)
            
        if error:
            _LOGGER.warning(error)
        
        self._retries_needed[retry] += 1
        return False

    
    def _process_installs_data(self, data):
        """
        Get device data for each installation
        """
        install_map = {}
        device_map = {}
        config_map = {}
        
        installation_map = data.get('installation_map', {})
        configuration_map = data.get('configuration_map', {})
        
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
                
                install_id = DabPumpsCoordinator.create_id(ins_id + suffix_id)
                install_name = ins_name + suffix_name
                install_devices = []

                _LOGGER.debug(f"DAB Pumps installation found: {install_name}")
                    
                for dum_idx, dum in enumerate(installation.get('dums', [])):
                    
                    dum_serial = dum.get('serial', '')
                    dum_name = dum.get('name', None) or dum.get('distro_embedded', None) or dum.get('distro', None) or f"device {dum_idx}"

                    device_id = DabPumpsCoordinator.create_id(dum_name + suffix_id)
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
                        config_id = dum.get('configuration_id', None) or '',
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
        
        # Go through the list of all device configurations and params
        for conf_idx, configuration in enumerate(configuration_map.values()):
            
            conf_id = configuration.get('configuration_id', '')
            conf_name = configuration.get('name') or f"config{conf_idx}"
            conf_label = configuration.get('label') or f"config{conf_idx}"
            conf_descr = configuration.get('description') or f"config {conf_idx}"
            conf_params = {}
            
            meta = configuration.get('metadata') or {}
            meta_params = meta.get('params') or []
            
            for meta_param_idx, meta_param in enumerate(meta_params):
                
                param_name = meta_param.get('name') or f"param{meta_param_idx}"
                param_type = meta_param.get('type') or ''
                param_unit = meta_param.get('unit')
                param_weight = meta_param.get('weight')
                param_min = meta_param.get('min')
                param_max = meta_param.get('max')
                param_family = meta_param.get('family') or ''
                param_group = meta_param.get('group') or ''
                
                values = meta_param.get('values') or []
                param_values = { str(v[0]): str(v[1]) for v in values if len(v) >= 2 }
                
                param = DabPumpsParams(
                    key = param_name,
                    type = param_type,
                    unit = param_unit,
                    weight = param_weight,
                    values = param_values,
                    min = param_min,
                    max = param_max,
                    family = param_family,
                    group = param_group,
                    view = ''.join([ s[0] for s in (meta_param.get('view') or []) ]),
                    change = ''.join([ s[0] for s in (meta_param.get('change') or []) ]),
                    log = ''.join([ s[0] for s in (meta_param.get('log') or []) ]),
                    report = ''.join([ s[0] for s in (meta_param.get('report') or []) ])
                )
                conf_params[param_name] = param
            
            config = DabPumpsConfig(
                label = conf_label,
                description = conf_descr,
                meta_params = conf_params
            )
            config_map[conf_id] = config
            
            _LOGGER.debug(f"DAB Pumps configuration found: {conf_name} with {len(conf_params)} metadata params")        

        # Cleanup statusses to only keep values that are still part of an install
        status_map = { k: v for k, v in self._status_map.items() if v.serial in device_map }
        
        self._install_map_ts = datetime.now() if len(install_map) > 0 else datetime.min
        self._device_map_ts = datetime.now() if len(device_map) > 0 else datetime.min
        self._config_map_ts = datetime.now() if len(config_map) > 0 else datetime.min
        self._install_map = install_map
        self._device_map = device_map
        self._config_map = config_map
        self._status_map = status_map
        return True


    def _process_strings_data(self, data):
        """
        Get translated strings from data
        """
        string_map = { k: v for k, v in data.items() }
        
        _LOGGER.debug(f"DAB Pumps strings found: {len(string_map)}")
        
        self._string_map_ts = datetime.now() if len(string_map) > 0 else datetime.min
        self._string_map = string_map
        return True


    def _process_device_status_data(self, device, data):
        """
        Process status data for a device
        """
        status_map = {}
        for item_key, item_val in data.items():
            # the value 'h' is used when a property is not available/supported
            if item_val=='h':
                continue
            
            # Item Entity ID is combination of device serial and each field unique name as internal sensor hash
            # Item Unique ID is a more readable version
            entity_id = DabPumpsCoordinator.create_id(device.serial, item_key)
            unique_id = DabPumpsCoordinator.create_id(device.name, item_key)

            # Add it to our statusses
            item = DabPumpsStatus(
                serial = device.serial,
                unique_id = unique_id,
                key = item_key,
                val = item_val,
            )
            status_map[entity_id] = item
        
        # Merge with statusses from other devices
        self._status_map_ts = datetime.now()
        self._status_map.update(status_map)
        return True


    
    async def async_get_diagnostics(self) -> dict[str, Any]:
        install_map = { k: v._asdict() for k,v in self._install_map.items() }
        device_map = { k: v._asdict() for k,v in self._device_map.items() }
        config_map = { k: v._asdict() for k,v in self._config_map.items() }
        status_map = { k: v._asdict() for k,v in self._status_map.items() }
        string_map = self._string_map.items()
        
        for cmk,cmv in self._config_map.items():
            config_map[cmk]['meta_params'] = { k: v._asdict() for k,v in cmv.meta_params.items() }
            
        calls_total = sum(self._retries_needed) or 1
        retries_counter = { idx: n for idx, n in enumerate(self._retries_needed) }
        retries_percent = { idx: round(100.0 * n / calls_total, 2) for idx, n in enumerate(self._retries_needed) }
            
        api_data = await self._api.async_get_diagnostics()

        return {
            "diagnostics_ts": datetime.now(),
            "diagnostics": {
                "retries_counter": retries_counter,
                "retries_percent": retries_percent,
            },
            "data": {
                "install_id": self._install_id,
                "install_map_ts": self._install_map_ts,
                "install_map": install_map,
                "device_map_ts": self._device_map_ts,
                "device_map": device_map,
                "config_map_ts": self._config_map_ts,
                "config_map": config_map,
                "status_map_ts": self._status_map_ts,
                "status_map": status_map,
                "string_map_ts": self._string_map_ts,
                "string_map": string_map,
            },
            "api": async_redact_data(api_data, DIAGNOSTICS_REDACT),
        },
    
    
    @staticmethod
    def create_id(*args):
        str = '_'.join(args).strip('_')
        str = re.sub(' ', '_', str)
        str = re.sub('[^a-z0-9_-]+', '', str.lower())
        return str        

    