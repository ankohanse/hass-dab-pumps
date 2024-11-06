import asyncio
import async_timeout
import json
import logging
import re

from collections import namedtuple
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.diagnostics import REDACTED
from homeassistant.components.diagnostics.util import async_redact_data
from homeassistant.components.light import LightEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.core import HomeAssistant
from homeassistant.core import async_get_hass
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store
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
    DabPumpsApiHistoryItem,
    DabPumpsApiHistoryDetail,
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
    COORDINATOR_TIMEOUT,
    COORDINATOR_CACHE_WRITE_PERIOD,
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
        hass = async_get_hass()
        install_id = None
        options = {}
        
        # Get a temporary instance of the DabPumpsApi for these credentials
        api = DabPumpsApiFactory.create_temp(hass, username, password)
        
        # Get an instance of our coordinator. This is unique to this install_id
        coordinator = DabPumpsCoordinator(hass, api, install_id, options)
        return coordinator
    

class DabPumpsCoordinator(DataUpdateCoordinator):
    """My custom coordinator."""
    
    def __init__(self, hass, api, install_id, options):
        """Initialize my coordinator."""
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
        
        # Cache
        self._cache = None
        self._cache_last_write = datetime.min
        
        # counters for diagnostics
        self._diag_retries = { n: 0 for n in range(COORDINATOR_RETRY_ATTEMPTS) }
        self._diag_durations = { n: 0 for n in range(10) }
        self._diag_api_counters = {}
        self._diag_api_history = []
        self._diag_api_details = {}
        self._diag_api_data = {}

        self._api.set_diagnostics(self._diag_api_handler)

        # Cached data in case communication to DAB Pumps fails
        self._hass = hass
        self._store_key = install_id
        self._store = DabPumpsCoordinatorStore(hass, self._store_key)


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
        await self._async_detect_install_list()
        
        #_LOGGER.debug(f"install_map: {self._install_map}")
        return (self._install_map)

    
    async def _async_update_data(self):
        """
        Fetch sensor data from API.
        
        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        _LOGGER.debug(f"Update data")

        if self._cache is None:
            if self._store:
                _LOGGER.debug(f"Read persisted cache")
                store = await self._store.async_get_data() or {}
                self._cache = store.get("cache", {})
            else:
                self._cache = {}

        # Note: asyncio.TimeoutError and aiohttp.ClientError are already
        # handled by the data update coordinator.
        await self._async_detect_data()

        # Periodically persist the cache
        if self._hass and \
           self._store and \
           self._cache and \
           (datetime.now() - self._cache_last_write).total_seconds() > COORDINATOR_CACHE_WRITE_PERIOD:
            
            _LOGGER.debug(f"Persist cache")
            self._cache_last_write = datetime.now()

            store = await self._store.async_get_data() or {}
            store["cache"] = self._cache
            await self._store.async_set_data(store)
        
        #_LOGGER.debug(f"device_map: {self._device_map}")
        #_LOGGER.debug(f"config_map: {self._config_map}")
        #_LOGGER.debug(f"status_map: {self._status_map}")
        return (self._device_map, self._config_map, self._status_map)
    
    
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
        return await self._async_change_device_status(status, value)
   
    
    async def _async_detect_install_list(self):
        error = None
        ts_start = datetime.now()

        for retry in range(0, COORDINATOR_RETRY_ATTEMPTS):
            try:
                await self._api.async_login()
                    
                # Fetch the list of installations
                await self._async_detect_installations(ignore_exception=False)
                
                # Keep track of how many retries were needed and duration
                self._update_statistics(retries = retry, duration = datetime.now()-ts_start)
                return True;
            
            except Exception as ex:
                error = str(ex)
            
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
        
        # Keep track of how many retries were needed and duration
        self._update_statistics(retries = retry, duration = datetime.now()-ts_start)
        return False
    
        
    async def _async_detect_data(self):
        warnings = []
        error = None
        ts_start = datetime.now()

        for retry in range(0, COORDINATOR_RETRY_ATTEMPTS):
            try:
                try:
                    await self._api.async_login()
                except:
                    if len(self._device_map) > 0:
                        # Force retry in loop by raising original exception
                        raise
                    else:
                        # Ignore and use persisted cached data if this is the initial retrieve
                        pass

                # Attempt to refresh installation details and devices when the cached one expires (once a day)
                await self._async_detect_install_details()

                # Attempt to refresh device configurations (once a day)
                await self._async_detect_device_configs()

                # Fetch device statusses (always)
                await self._async_detect_device_statusses()

                # Attempt to refresh the list of translations (once a day)
                await self._async_detect_strings()

                # Attempt to refresh the list of installations (once a day, just for diagnostocs)
                await self._async_detect_installations(ignore_exception=True)

                # Keep track of how many retries were needed and duration
                self._update_statistics(retries = retry, duration = datetime.now()-ts_start)
                return True
            
            except Exception as ex:
                error = str(ex)
            
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
        
        # Keep track of how many retries were needed and duration
        self._update_statistics(retries = retry, duration = datetime.now()-ts_start)
        return False
    
        
    async def _async_change_device_status(self, status, value):
        error = None
        ts_start = datetime.now()

        for retry in range(0, COORDINATOR_RETRY_ATTEMPTS):
            try:
                await self._api.async_login()
                
                # Attempt to change the device status via the API
                await self._api.async_change_device_status(status, value)

                # Keep track of how many retries were needed and duration
                self._update_statistics(retries = retry, duration = datetime.now()-ts_start)
                return True
            
            except Exception as ex:
                error = str(ex)
            
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
        
        # Keep track of how many retries were needed and duration
        self._update_statistics(retries = retry, duration = datetime.now()-ts_start)
        return False


    async def _async_detect_install_details(self):
        """
        Attempt to refresh installation details and devices when the cached one expires (once a day)
        """
        if (datetime.now() - self._device_map_ts).total_seconds() < 86400:
            # Not yet expired
            return
        
        # Try to retrieve via API
        context = f"installation {self._install_id}"
        try:
            data = await self._api.async_fetch_install_details(self._install_id)
            await self._async_process_install_data(data)
            await self._async_update_cache(context, data)
            ex = None
        except Exception as e:
            if len(self._device_map) > 0:
                # Ignore problems if this is just a periodic refresh
                ex = None
            else:
                # Try next alternative while remembering original exception
                ex = e

        if ex:
            # Next, try from persisted cache
            try:
                data = await self._async_fetch_from_cache(context)
                await self._async_process_install_data(data)
                ex = None
            except Exception:
                # Try next alternative while remembering original exception
                pass

        if ex:
            # Force retry in calling function by raising original exception
            raise ex

        # If we reach this point, then all devices have been fetched/refreshed
        self._device_map_ts = datetime.now()


    async def _async_detect_device_configs(self):
        """
        Attempt to refresh device configurations (once a day)
        """
        if (datetime.now() - self._config_map_ts).total_seconds() < 86400:
            # Not yet expired
            return
        
        for device in self._device_map.values():

            # First try to retrieve from API
            context = f"configuration {device.config_id}"
            try:
                data = await self._api.async_fetch_device_config(device)
                await self._async_process_device_config_data(device, data)
                await self._async_update_cache(context, data)
                ex = None
            except Exception as e:
                if device.config_id in self._config_map:
                    # Ignore problems if this is just a refresh
                    ex = None
                else:
                    # Try next alternative while remembering original exception
                    ex = e

            if ex:
                # Next try from persisted cache if this is the initial retrieve
                try:
                    data = await self._async_fetch_from_cache(context)
                    await self._async_process_device_config_data(device, data)
                    ex = None
                except Exception:
                    # Try next alternative while remembering original exception
                    pass

            if ex:
                # Force retry in calling function by raising original exception
                raise ex
                    
        # If we reach this point, then all device configs have been fetched/refreshed
        self._config_map_ts = datetime.now()


    async def _async_detect_device_statusses(self):
        """
        Fetch device statusses (always)
        """
        if (datetime.now() - self._status_map_ts).total_seconds() < 0:
            # Not yet expired
            return
        
        for device in self._device_map.values():

            # First try to retrieve from API
            context = f"statusses {device.serial}"
            try:
                data = await self._api.async_fetch_device_statusses(device)
                await self._async_process_device_status_data(device, data)
                await self._async_update_cache(context, data)
                ex = None
            except Exception as e:
                if any(status.serial==device.serial for status in self._status_map.values()):
                    # Ignore problems if this is just a refresh
                    ex = None
                else:
                    # Try next alternative while remembering original exception
                    ex = e

            if ex:
                # Next try from (outdated) persisted cache if this is the initial retrieve.
                # However, we will then set all values to unknown.
                try:
                    data = await self._async_fetch_from_cache(context)
                    await self._async_process_device_status_data(device, data, expired_values=True)
                    ex = None
                except Exception:
                    # Try next alternative while remembering original exception
                    pass

            if ex:
                # Force retry in calling function by raising original exception
                raise ex

        # If we reach this point, then all device statusses have been fetched/refreshed
        self._status_map_ts = datetime.now()


    async def _async_detect_strings(self):
        """
        Attempt to refresh the list of translations (once a day)
        """
        if (datetime.now() - self._string_map_ts).total_seconds() < 86400:
            # Not yet expired
            return
        
        context = f"localization_{self.language}"
        try:
            data = await self._api.async_fetch_strings(self.language)
            await self._async_process_strings_data(data)
            await self._async_update_cache(context, data)
            ex = None
        except Exception as e:
            if len(self._string_map) > 0:
                # Ignore problems if this is just a refresh
                ex = None
            else:
                # Try next alternative while remembering original exception
                ex = e
                
        if ex:
            # Next, try from persisted cache if this is the initial retrieve
            try:
                data = await self._async_fetch_from_cache(context)
                await self._async_process_strings_data(data)
                ex = None
            except Exception:
                # Try next alternative while remembering original exception
                pass

        if ex:
            # Force retry in calling function by raising original exception
            raise ex

        # If we reach this point, then all strings have been fetched/refreshed 
        self._string_map_ts = datetime.now()


    async def _async_detect_installations(self, ignore_exception=False):
        """
        Attempt to refresh the list of installations (once a day, just for diagnostocs)
        """
        if (datetime.now() - self._install_map_ts).total_seconds() < 86400:
            # Not yet expired
            return
        
        # First try to retrieve from API.
        context = f"installation list"
        try:
            data = await self._api.async_fetch_install_list()
            await self._async_process_install_list(data)
            await self._async_update_cache(context, data)
            ex = None
        except Exception as e:
            if ignore_exception:
                # Ignore problems
                ex = None
            else:
                # Try next alternative while remembering original exception
                ex = e

        if ex:
            # Force retry in calling function by raising original exception
            raise ex

        # If we reach this point, then installation list been fetched/refreshed/ignored
        self._install_map_ts = datetime.now()


    async def _async_process_install_list(self, data):
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

        # Sanity check. # Never overwrite a known install_map with empty lists
        if len(install_map)==0:
            raise DabPumpsDataError(f"No installations found in data")

        # Remember this data
        self._install_map_ts = datetime.now()
        self._install_map = install_map


    async def _async_process_install_data(self, data):
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
        installation = data
        installation_id = installation.get('installation_id', '')
        if installation_id != install_id_org: 
            raise DabPumpsDataError(f"Expected installation id {install_id_org} was not found in returned installation details")

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
            
        # Also detect the user role within this installation
        user_role = installation.get('user_role', 'CUSTOMER')

        # Cleanup device config and device statusses to only keep values that are still part of a device in this installation
        config_map = { k: v for k, v in self._config_map.items() if v.id in config_list }
        status_map = { k: v for k, v in self._status_map.items() if v.serial in serial_list }

        # Sanity check. # Never overwrite a known device_map, config_map or status_map with empty lists
        if len(device_map) == 0:
            raise DabPumpsDataError(f"No devices found for installation id {install_id_org}")
        
        # Remember/update the found maps.
        self._device_map_ts = datetime.now()
        self._device_map = device_map
        self._config_map = config_map
        self._status_map = status_map

        self._user_role_ts = datetime.now()
        self._user_role = user_role


    async def _async_process_device_config_data(self, device, data):
        """
        Update device config for the installation
        """
        config_map = {}

        conf_id = data.get('configuration_id', '')
        conf_name = data.get('name') or f"config{conf_id}"
        conf_label = data.get('label') or f"config{conf_id}"
        conf_descr = data.get('description') or f"config {conf_id}"
        conf_params = {}

        if conf_id != device.config_id: 
            raise DabPumpsDataError(f"Expected configuration id {device.config_id} was not found in returned configuration data")
            
        meta = data.get('metadata') or {}
        meta_params = meta.get('params') or []
        
        for meta_param_idx, meta_param in enumerate(meta_params):
            # get param details
            param_name = meta_param.get('name') or f"param{meta_param_idx}"
            param_type = meta_param.get('type') or ''
            param_unit = meta_param.get('unit')
            param_weight = meta_param.get('weight')
            param_min = meta_param.get('min') or meta_param.get('warn_low')
            param_max = meta_param.get('max') or meta_param.get('warn_hi')
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


    async def _async_process_device_status_data(self, device, data, expired_values=False):
        """
        Process status data for a device
        """
        status_map = {}
        status = data.get('status') or "{}"
        values = json.loads(status)
        
        for item_key, item_val in values.items():
            # the value 'h' is used when a property is not available/supported
            if item_val=='h':
                continue

            # If the data is regarded as expired then set all values to unknown.
            # This is used to be able to initialize the integration from persited cached values
            # during startup, even if communication to DAB Pumps fails.
            if expired_values:
                item_val = None
            
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

        if len(status_map) == 0:
             raise DabPumpsDataError(f"No statusses found for '{device.name}'")
        
        _LOGGER.debug(f"DAB Pumps statusses found for '{device.name}' with {len(status_map)} values")

        # Merge with statusses from other devices
        self._status_map_ts = datetime.now()
        self._status_map.update(status_map)


    async def _async_process_strings_data(self, data):
        """
        Get translated strings from data
        """
        language = data.get('bundle', DEFAULT_LANGUAGE)
        messages = data.get('messages', {})
        string_map = { k: v for k, v in messages.items() }
        
        # Sanity check. # Never overwrite a known string_map with empty lists
        if len(string_map) == 0:
            raise DabPumpsDataError(f"No strings found in data")

        _LOGGER.debug(f"DAB Pumps strings found: {len(string_map)} in language '{language}'")
        
        self._string_map_ts = datetime.now() if len(string_map) > 0 else datetime.min
        self._string_map_lang = language
        self._string_map = string_map


    async def _async_update_cache(self, context, data):
        """
        Update the memory cache.
        Persisted cache is saved periodicaly by another function
        """
        if self._cache:
            data["ts"] = datetime.now()
            self._cache[context] = data


    async def _async_fetch_from_cache(self, context):
        """
        Fetch from the memory cache
        """
        if self._cache:
            _LOGGER.debug(f"Fetch from cache: {context}")
            return self._cache.get(context, {})
        else:
            return {}

    
    async def async_get_diagnostics(self) -> dict[str, Any]:
        install_map = { k: v._asdict() for k,v in self._install_map.items() }
        device_map = { k: v._asdict() for k,v in self._device_map.items() }
        config_map = { k: v._asdict() for k,v in self._config_map.items() }
        status_map = { k: v._asdict() for k,v in self._status_map.items() }
        
        for cmk,cmv in self._config_map.items():
            config_map[cmk]['meta_params'] = { k: v._asdict() for k,v in cmv.meta_params.items() }
            
        retries_total = sum(self._diag_retries.values()) or 1
        retries_counter = dict(sorted(self._diag_retries.items()))
        retries_percent = { key: round(100.0 * n / retries_total, 2) for key,n in retries_counter.items() }

        durations_total = sum(self._diag_durations.values()) or 1
        durations_counter = dict(sorted(self._diag_durations.items()))
        durations_percent = { key: round(100.0 * n / durations_total, 2) for key, n in durations_counter.items() }

        api_calls_total = sum([ n for key, n in self._diag_api_counters.items() ]) or 1
        api_calls_counter = { key: n for key, n in self._diag_api_counters.items() }
        api_calls_percent = { key: round(100.0 * n / api_calls_total, 2) for key, n in self._diag_api_counters.items() }

        return {
            "diagnostics_ts": datetime.now(),
            "diagnostics": {
                "retries": {
                    "counter": retries_counter,
                    "percent": retries_percent,
                },
                "durations": {
                    "counter": durations_counter,
                    "percent": durations_percent,
                },
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
            "cache": self._cache,
            "api": {
                "data": self._diag_api_data,
                "calls": {
                    "counter": api_calls_counter,
                    "percent": api_calls_percent,
                },                
                "history": async_redact_data(self._diag_api_history, DIAGNOSTICS_REDACT),
                "details": async_redact_data(self._diag_api_details, DIAGNOSTICS_REDACT),
            }
        }
    

    def _update_statistics(self, retries = None, duration = None):
        if retries is not None:
            if retries in self._diag_retries:
                self._diag_retries[retries] += 1
            else:
                self._diag_retries[retries] = 1
            
        if duration is not None:
            duration = round(duration.total_seconds(), 0)
            if duration not in self._diag_durations:
                self._diag_durations[duration] = 1
            else:
                self._diag_durations[duration] += 1


    def _diag_api_handler(self, context, item:DabPumpsApiHistoryItem, detail:DabPumpsApiHistoryDetail, data:dict):
        """Handle diagnostics updates from the api"""

        # Call counters
        if context in self._diag_api_counters:
            self._diag_api_counters[context] += 1
        else:
            self._diag_api_counters[context] = 1

        # Call history
        self._diag_api_history.append(item)
        while len(self._diag_api_history) > 64:
            self._diag_api_history.pop(0)

        # Call details
        self._diag_api_details[context] = detail

        # Api data
        self._diag_api_data = self._diag_api_data | data


    @staticmethod
    def create_id(*args):
        str = '_'.join(args).strip('_')
        str = re.sub(' ', '_', str)
        str = re.sub('[^a-z0-9_-]+', '', str.lower())
        return str        


class DabPumpsDataError(Exception):
    """Exception to indicate generic data failure."""    


class DabPumpsCoordinatorStore(Store[dict]):
    
    _STORAGE_VERSION_MAJOR = 1
    _STORAGE_VERSION_MINOR = 0
    _STORAGE_KEY = DOMAIN + ".coordinator"
    
    def __init__(self, hass, store_key):
        super().__init__(
            hass, 
            key=self._STORAGE_KEY, 
            version=self._STORAGE_VERSION_MAJOR, 
            minor_version=self._STORAGE_VERSION_MINOR
        )
        self._store_key = store_key

    
    async def _async_migrate_func(self, old_major_version, old_minor_version, old_data):
        """Migrate the history store data"""

        if old_major_version <= 1:
            # version 1 is the current version. No migrate needed
            data = old_data

        return data
    

    async def async_get_data(self):
        """Load the persisted coordinator_cache file and return the data specific for this coordinator instance"""
        data = await super().async_load() or {}
        data_self = data.get(self._store_key, {})
        return data_self
    

    async def async_set_data(self, data_self):
        """Save the data specific for this coordinator instance into the persisted coordinator_cache file"""
        data = await super().async_load() or {}
        data[self._store_key] = data_self
        await super().async_save(data)
    