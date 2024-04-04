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
    CONF_LANGUAGE,
)
from .api import (
    DabPumpsApiFactory,
    DabPumpsApi,
    DabPumpsApiAuthError,
    DabPumpsApiRightsError,
    DabPumpsApiError,
)

from .const import (
    DOMAIN,
    NAME,
    COORDINATOR,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_LANGUAGE,
    LANGUAGE_MAP,
    LANGUAGE_AUTO,
    LANGUAGE_AUTO_FALLBACK,
    CONF_INSTALL_ID,
    CONF_INSTALL_NAME,
    CONF_OPTIONS,
    CONF_POLLING_INTERVAL,
    DIAGNOSTICS_REDACT,
    COORDINATOR_RETRY_ATTEMPTS,
    COORDINATOR_RETRY_DELAY,
    SIMULATE_MULTI_INSTALL,
    SIMULATE_SUFFIX_ID,
    SIMULATE_SUFFIX_NAME,
)


_LOGGER = logging.getLogger(__name__)

DabPumpsInstall = namedtuple('DabPumpsInstall', 'id, name, description, company, address, role, devices')
DabPumpsDevice = namedtuple('DabPumpsDevice', 'id, serial, name, vendor, product, version, config_id, install_id')
DabPumpsConfig = namedtuple('DabPumpsConfig', 'id, label, description, meta_params')
DabPumpsParams = namedtuple('DabPumpsParams', 'key, type, unit, weight, values, min, max, family, group, view, change, log, report')
DabPumpsStatus = namedtuple('DabPumpsStatus', 'serial, unique_id, key, val')


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
        self._string_map_lang = None
        self._string_map = {}
        self._user_role_ts = datetime.min
        self._user_role = 'CUSTOMER'
        
        # retry counter for diagnosis
        self._retries_needed = [ 0 for r in range(COORDINATOR_RETRY_ATTEMPTS) ]

    
    @property
    def string_map(self):
        return self._string_map


    @property
    def user_role(self):
        return self._user_role[0] # only use the first character
    

    @property
    def language(self):
        lang = self._options.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
        if lang == LANGUAGE_AUTO:
            system_lang = self.system_language
            lang = system_lang if system_lang in LANGUAGE_MAP else LANGUAGE_AUTO_FALLBACK
    
        return lang
    

    @property
    def system_language(self):
        """
        Get HASS system language as set under Settings->System->General.
        Unless that language is not allowed in DConnect DAB LANGUAGE_MAP, in that case fallback to DEFAULT_LANGUAGE
        """
        return self.hass.config.language.split('-', 1)[0] # split from 'en-GB' to just 'en'


    async def async_config_flow_data(self):
        """
        Fetch installation data from API.
        """
        _LOGGER.debug(f"Config flow data")
        
        try:
            async with async_timeout.timeout(60):
                await self._async_detect_install_list()
                
                #_LOGGER.debug(f"install_map: {self._install_map}")
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
                await self._async_detect_data()
                
                #_LOGGER.debug(f"device_map: {self._device_map}")
                #_LOGGER.debug(f"config_map: {self._config_map}")
                #_LOGGER.debug(f"status_map: {self._status_map}")
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
                return await self._async_change_device_status(status, value)
        
        except asyncio.TimeoutError as err:
            raise UpdateFailed(f"Timeout while communicating with API: {err}")
    
    
    async def _async_detect_install_list(self):
        error = None
        for retry in range(0, COORDINATOR_RETRY_ATTEMPTS):
            try:
                await self._api.async_login()
                    
                # Fetch the list of installations
                data = await self._api.async_fetch_install_list()
                self._process_install_list(data)
                
                self._retries_needed[retry] += 1
                return True;
            
            except Exception as ex:
                error = ex
            
            # Log off, end session and retry if possible
            await self._api.async_logout();  
            
            if retry < COORDINATOR_RETRY_ATTEMPTS:
                if retry < 2:
                    _LOGGER.info(f"Retry {retry+1} in {COORDINATOR_RETRY_DELAY} seconds. {error}")
                else:
                    _LOGGER.warn(f"Retry {retry+1} in {COORDINATOR_RETRY_DELAY} seconds. {error}")
                await asyncio.sleep(COORDINATOR_RETRY_DELAY)
            
        if error:
            _LOGGER.warning(error)
        
        self._retries_needed[retry] += 1
        return False
    
        
    async def _async_detect_data(self):
        error = None
        for retry in range(0, COORDINATOR_RETRY_ATTEMPTS):
            try:
                await self._api.async_login()

                # Fetch installation and devices when the cached one expires (once a day)
                if (datetime.now() - self._device_map_ts).total_seconds() > 86400:
                    data = await self._api.async_fetch_install_list()
                    self._process_install_list(data)
                    self._process_install_data(data)

                # Fetch device configurations (once a day)
                if (datetime.now() - self._config_map_ts).total_seconds() > 86400:
                    for device in self._device_map.values():
                        data = await self._api.async_fetch_device_config(device)
                        self._process_device_config_data(device, data)

                # Fetch device statusses (always)
                if (datetime.now() - self._status_map_ts).total_seconds() > 0:
                    for device in self._device_map.values():
                        data = await self._api.async_fetch_device_statusses(device)
                        self._process_device_status_data(device, data)
                
                # Refresh the list of translations (once a day)
                if (datetime.now() - self._string_map_ts).total_seconds() > 86400:
                    data = await self._api.async_fetch_strings(self._language)
                    self._process_strings_data(data)

                self._retries_needed[retry] += 1
                return True;
            
            except Exception as ex:
                error = ex
            
            # Log off, end session and retry if possible
            await self._api.async_logout();  
            
            if retry < COORDINATOR_RETRY_ATTEMPTS:
                if retry < 2:
                    _LOGGER.info(f"Retry {retry+1} in {COORDINATOR_RETRY_DELAY} seconds. {error}")
                else:
                    _LOGGER.warn(f"Retry {retry+1} in {COORDINATOR_RETRY_DELAY} seconds. {error}")
                await asyncio.sleep(COORDINATOR_RETRY_DELAY)
            
        if error:
            _LOGGER.warning(error)
        
        self._retries_needed[retry] += 1
        return False
    
        
    async def _async_change_device_status(self, status, value):
        error = None
        for retry in range(0, COORDINATOR_RETRY_ATTEMPTS):
            try:
                await self._api.async_login()
                
                # Fetch a new list of installations when the cached one expires
                await self._api.async_change_device_status(status, value)

                self._retries_needed[retry] += 1
                return True
            
            except Exception as ex:
                error = ex
            
            # Log off, end session and retry if possible
            await self._api.async_logout();  
            
            if retry < COORDINATOR_RETRY_ATTEMPTS:
                if retry < 2:
                    _LOGGER.info(f"Retry {retry+1} in {COORDINATOR_RETRY_DELAY} seconds. {error}")
                else:
                    _LOGGER.warn(f"Retry {retry+1} in {COORDINATOR_RETRY_DELAY} seconds. {error}")
                await asyncio.sleep(COORDINATOR_RETRY_DELAY)
            
        if error:
            _LOGGER.warning(error)
        
        self._retries_needed[retry] += 1
        return False

    
    def _process_install_list(self, data):
        """
        Get installations list
        """
        install_map = {}
        installations = data.get('values', [])
        
        # Go through the list of installations twice,
        # the second one to generate an extra dummy install for testing purposes
        for test in [0,1]:
            if test and not SIMULATE_MULTI_INSTALL:
                break
            
            suffix_id = SIMULATE_SUFFIX_ID if test else ""
            suffix_name = SIMULATE_SUFFIX_NAME if test else ""
            
            for ins_idx, installation in enumerate(installations):
                
                ins_id = installation.get('installation_id', '')
                ins_name = installation.get('name', None) or installation.get('description', None) or f"installation {ins_idx}"
                
                install_id = DabPumpsCoordinator.create_id(ins_id + suffix_id)
                install_name = ins_name + suffix_name

                _LOGGER.debug(f"DAB Pumps installation found: {install_name}")
                install = DabPumpsInstall(
                    id = install_id,
                    name = install_name,
                    description = installation.get('description', None) or '',
                    company = installation.get('company', None) or '',
                    address = installation.get('address', None) or '',
                    role = installation.get('user_role', None) or 'CUSTOMER',
                    devices = len(installation.get('dums', None) or []),
                )
                install_map[install_id] = install

        # Remember this data
        self._install_map_ts = datetime.now() if len(install_map) > 0 else datetime.min
        self._install_map = install_map


    def _process_install_data(self, data):
        """
        Update device data for the installation
        """

        # Process installation details
        # Take into account that this may be an 'extra' generated installation for testing
        install_id = self._install_id
        install_id_org = self._install_id.removesuffix(SIMULATE_SUFFIX_ID)
        
        suffix_id = SIMULATE_SUFFIX_ID if install_id != install_id_org else ""
        suffix_name = SIMULATE_SUFFIX_NAME if install_id != install_id_org else ""
        
        # Find the current installation
        installations = data.get('values', [])
        installation = next( (ins for ins in installations if ins.get('installation_id', '') == install_id_org), None)
        if not installation: 
            raise DabPumpsDataError(f"Could not find configured installation id {install_id_org} in list of installations")

        # Go through the list of all device definitions for the current installation
        device_map = {}
        serial_list = []
        config_list = []

        ins_dums = installation.get('dums', [])

        for dum_idx, dum in enumerate(ins_dums):
            dum_serial = dum.get('serial', None) or ''
            dum_name = dum.get('name', None) or dum.get('ProductName', None) or f"device {dum_idx}"
            dum_product = dum.get('ProductName', None) or f"device {dum_idx}"
            dum_version = dum.get('configuration_name', None) or ''
            dum_config = dum.get('configuration_id', None) or ''

            if not dum_serial: 
                raise DabPumpsDataError(f"Could not find installation attribute 'serial'")
            if not dum_config: 
                raise DabPumpsDataError(f"Could not find installation attribute 'configuration_id'")

            device_id = DabPumpsCoordinator.create_id(dum_name + suffix_id)
            device_serial = dum_serial + suffix_id
            device_name = dum_name + suffix_name

            device = DabPumpsDevice(
                vendor = 'DAB Pumps',
                name = device_name,
                id = device_id,
                serial = device_serial,
                product = dum_product,
                version = dum_version,
                config_id = dum_config,
                install_id = install_id,
            )
            device_map[device_serial] = device

            # Keep track of config_id's and serials we have seen
            if dum_config not in config_list:
                config_list.append(dum_config) 
            
            if device_serial not in serial_list:
                serial_list.append(device_serial)
            
            _LOGGER.debug(f"DAB Pumps device found: {device_name} with serial {device_serial}")
            
        # Also remember the user role within this installation
        user_role = installation.get('user_role', 'CUSTOMER')

        # Cleanup device config and device statusses to only keep values that are still part of a device in this installation
        config_map = { k: v for k, v in self._config_map.items() if v.id in config_list }
        status_map = { k: v for k, v in self._status_map.items() if v.serial in serial_list }
        
        # Remember/update the found maps
        self._device_map_ts = datetime.now() if len(device_map) > 0 else datetime.min
        self._device_map = device_map
        self._config_map = config_map
        self._status_map = status_map

        self._user_role_ts = datetime.now() if user_role else datetime.min
        self._user_role = user_role


    def _process_device_config_data(self, device, data):
        """
        Update device config for the installation
        """
        config_map = {}

        conf_id = data.get('configuration_id', '') or device.config_id
        conf_name = data.get('name') or f"config{conf_id}"
        conf_label = data.get('label') or f"config{conf_id}"
        conf_descr = data.get('description') or f"config {conf_id}"
        conf_params = {}

        if not conf_id: 
            raise DabPumpsDataError(f"Could not find configuration attribute 'configuration_id'")
            
        meta = data.get('metadata') or {}
        meta_params = meta.get('params') or []
        
        for meta_param_idx, meta_param in enumerate(meta_params):
            # get param details
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
            id = conf_id,
            label = conf_label,
            description = conf_descr,
            meta_params = conf_params
        )
        config_map[conf_id] = config
        
        _LOGGER.debug(f"DAB Pumps configuration found: {conf_name} with {len(conf_params)} metadata params")        

        # Merge with configurations from other devices
        self._config_map_ts = datetime.now()
        self._config_map.update(config_map)


    def _process_device_status_data(self, device, data):
        """
        Process status data for a device
        """
        status_map = {}
        values = data.get('values') or {}

        for item_key, item_val in values.items():
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


    def _process_strings_data(self, data):
        """
        Get translated strings from data
        """
        language = data.get('bundle', DEFAULT_LANGUAGE)
        messages = data.get('messages', {})
        string_map = { k: v for k, v in messages.items() }
        
        _LOGGER.debug(f"DAB Pumps strings found: {len(string_map)} in language '{language}'")
        
        self._string_map_ts = datetime.now() if len(string_map) > 0 else datetime.min
        self._string_map_lang = language
        self._string_map = string_map

    
    async def async_get_diagnostics(self) -> dict[str, Any]:
        install_map = { k: v._asdict() for k,v in self._install_map.items() }
        device_map = { k: v._asdict() for k,v in self._device_map.items() }
        config_map = { k: v._asdict() for k,v in self._config_map.items() }
        status_map = { k: v._asdict() for k,v in self._status_map.items() }
        
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
                "string_map_lang": self._string_map_lang,
                "string_map": self._string_map,
                "user_role_ts": self._user_role_ts,
                "user_role": self._user_role
            },
            "api": async_redact_data(api_data, DIAGNOSTICS_REDACT),
        },
    
    
    @staticmethod
    def create_id(*args):
        str = '_'.join(args).strip('_')
        str = re.sub(' ', '_', str)
        str = re.sub('[^a-z0-9_-]+', '', str.lower())
        return str        


class DabPumpsDataError(Exception):
    """Exception to indicate generic data failure."""    
    