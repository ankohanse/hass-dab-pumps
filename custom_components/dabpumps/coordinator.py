import logging

from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.core import async_get_hass
from homeassistant.helpers import device_registry
from homeassistant.helpers import entity_registry
from homeassistant.helpers.device_registry import DeviceRegistry
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_LANGUAGE,
)

from pydabpumps import (
    DabPumpsStatus,
    DabPumpsUserRole,
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
    CONF_POLLING_INTERVAL,
    COORDINATOR_RELOAD_DELAY,
    COORDINATOR_RELOAD_DELAY_MAX,
    utcnow,
)
from .api import (
    DabPumpsApiFactory,
    DabPumpsApiWrap,
    DabPumpsFetchOrder,
)

# Define logger
_LOGGER = logging.getLogger(__name__)

class DabPumpsCoordinatorFactory:
    """Factory to help create the Coordinator"""

    @staticmethod
    def create(hass: HomeAssistant, config_entry: ConfigEntry, force_create: bool = False):
        """
        Get existing Coordinator for a config entry, or create a new one if it does not yet exist
        """
    
        # Get properties from the config_entry
        configs = config_entry.data
        options = config_entry.options

        username = configs.get(CONF_USERNAME, None)
        password = configs.get(CONF_PASSWORD, None)
        install_id = configs.get(CONF_INSTALL_ID, None)
        install_name = configs.get(CONF_INSTALL_NAME, None)

        language = options.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
        if language == LANGUAGE_AUTO:
            system_lang = DabPumpsCoordinator.system_language()
            language = system_lang if system_lang in LANGUAGE_MAP else LANGUAGE_AUTO_FALLBACK

        reload_count = 0
        
        # Sanity check
        if not DOMAIN in hass.data:
            hass.data[DOMAIN] = {}
        if not COORDINATOR in hass.data[DOMAIN]:
            hass.data[DOMAIN][COORDINATOR] = {}
            
        # already created?
        coordinator = hass.data[DOMAIN][COORDINATOR].get(install_id, None)
        if coordinator:
            # check for an active reload and copy reload settings when creating a new coordinator
            reload_count = coordinator.reload_count

            # Forcing a new coordinator?
            if force_create:
                coordinator = None

            # Verify that config and options are still the same (== and != do a recursive dict compare)
            elif coordinator.configs != configs or coordinator.options != options:
                # Not the same; force recreate of the coordinator
                _LOGGER.debug(f"Settings have changed; force use of new coordinator")
                coordinator = None

        if not coordinator:
            _LOGGER.debug(f"Create coordinator for installation '{install_name}' ({install_id}) from account '{username}'")

            # Get an instance of the DabPumpsApi for these credentials
            # This instance may be shared with other coordinators that use the same credentials
            api = DabPumpsApiFactory.create(hass, username, password, language)
        
            # Get an instance of our coordinator. This is unique to this install_id
            coordinator = DabPumpsCoordinator(hass, config_entry.entry_id, api, configs, options)

            # Apply reload settings if needed
            coordinator.reload_count = reload_count

            hass.data[DOMAIN][COORDINATOR][install_id] = coordinator
        else:
            _LOGGER.debug(f"Reuse coordinator for installation '{install_name}' ({install_id})")
            
        return coordinator


    @staticmethod
    def create_temp(username: str, password: str):
        """
        Get temporary Coordinator for a given username+password.
        This coordinator will only provide limited functionality
        """
    
        # Get properties from the config_entry
        hass = async_get_hass()
        configs = {
            CONF_USERNAME: username,
            CONF_PASSWORD: password,
        }
        options = {}
        language = DabPumpsCoordinator.system_language
        
        # Get a temporary instance of the DabPumpsApi for these credentials
        api = DabPumpsApiFactory.create_temp(hass, username, password, language)
        
        # Get an instance of our coordinator. This is unique to this install_id
        _LOGGER.debug(f"create temp coordinator for account '{username}'")
        coordinator = DabPumpsCoordinator(hass, None, api, configs, options)
        return coordinator
    

class DabPumpsCoordinator(DataUpdateCoordinator):
    """My custom coordinator."""

    def __init__(self, hass: HomeAssistant, config_entry_id: str, api: DabPumpsApiWrap, configs: dict[str,Any], options: dict[str,Any]):
        """
        Initialize my coordinator.
        """
        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name=NAME,
            # Polling interval. Will only be polled if there are subscribers.
            update_interval=timedelta(seconds=options.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL)),
            update_method=self._async_update_data,
        )

        self._config_entry_id: str = config_entry_id
        self._api: DabPumpsApiWrap = api
        self._configs: dict[str,Any] = configs
        self._options: dict[str,Any] = options

        self._username = configs.get(CONF_USERNAME, None)
        self._install_id = configs.get(CONF_INSTALL_ID, None)
        self._install_name = configs.get(CONF_INSTALL_NAME, None)

        self._fetch_order = DabPumpsFetchOrder.INIT

        # Keep track of entity and device ids during init so we can cleanup unused ids later
        self._valid_unique_ids: dict[Platform, list[str]] = {} # platform -> entity unique_id
        self._valid_device_ids: dict[str, tuple[str,str]] = {} # serial -> HA device identifier

        # Auto reload when a new device is detected
        self._reload_count: int = 0
        self._reload_time: datetime = utcnow()
        self._reload_delay: int = COORDINATOR_RELOAD_DELAY
        

    @staticmethod
    def system_language() -> str:
        """
        Get HASS system language as set under Settings->System->General.
        Unless that language is not allowed in DConnect DAB LANGUAGE_MAP, in that case fallback to DEFAULT_LANGUAGE
        """
        hass = async_get_hass()
        return hass.config.language.split('-', 1)[0] # split from 'en-GB' to just 'en'


    @property
    def configs(self) -> dict[str,Any]:
        return self._configs
    

    @property
    def options(self) ->dict[str,Any]:
        return self._options
    

    @property
    def install_id(self) -> str:
        return self._install_id
    

    @property
    def install_name(self) -> str:
        return self._install_name
    

    @property
    def user_name(self) -> str:
        return self._username


    @property
    def user_role(self) -> str:
        # Return the user role for this install_id
        # Note: we only use the first character
        if self._install_id in self._api.install_map:
            return self._api.install_map[self._install_id].role[0]
        else:
            return DabPumpsUserRole.CUSTOMER[0]
    

    @property
    def language(self) -> str:
        lang = self._options.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
        if lang == LANGUAGE_AUTO:
            system_lang = DabPumpsCoordinator.system_language()
            lang = system_lang if system_lang in LANGUAGE_MAP else LANGUAGE_AUTO_FALLBACK
    
        return lang
    

    @property
    def reload_count(self) -> int:
        return self._reload_count
    
    @reload_count.setter
    def reload_count(self, count: int):
        # Double the delay on each next reload to prevent enless reloads if something is wrong.
        self._reload_count = count
        self._reload_delay = min( pow(2,count-1)*COORDINATOR_RELOAD_DELAY, COORDINATOR_RELOAD_DELAY_MAX )
    

    async def async_on_unload(self):
        """
        Called when Home Assistant shuts down or config-entry unloads
        """
        _LOGGER.info(f"Unload installation '{self._install_name}'")

        # Do not logout or close the api. Another coordinator/config-entry might still be using it.
        # But do trigger write of cache
        await self._api.async_on_unload(self._install_id)


    def create_id(self, *args):
        return self._api.create_id(*args)


    def set_valid_unique_ids(self, platform: Platform, ids: list[str]):
        """
        Set list of valid entity ids for this installation.
        Called from entity_base when all entities for a platform have been created.
        """
        self._valid_unique_ids[platform] = ids


    async def async_create_devices(self, config_entry: ConfigEntry):
        """
        Add all detected devices to the hass device_registry
        """

        _LOGGER.info(f"Create devices for installation '{self._install_name}'")
        dr: DeviceRegistry = device_registry.async_get(self.hass)
        valid_ids: dict[str, tuple[str,str]] = {}

        install_devices = [ d for d in self._api.device_map.values() if d.install_id == self._install_id ]
        for device in install_devices:
            device_id = self.create_id(device.serial)
            _LOGGER.debug(f"Create device {device_id} ({device.name}) for installation '{self._install_name}'")

            dr.async_get_or_create(
                config_entry_id = config_entry.entry_id,
                identifiers = {(DOMAIN, device_id)},
                connections = {(CONNECTION_NETWORK_MAC, device.mac_address)} if device.mac_address else None,
                name = device.name,
                manufacturer =  device.vendor,
                model = device.product,
                serial_number = device.serial,
                hw_version = device.hw_version,
                sw_version = device.sw_version,
            )
            valid_ids[device_id] = (DOMAIN, device_id)

        # Remember valid device ids so we can do a cleanup of invalid ones later
        self._valid_device_ids = valid_ids


    async def async_cleanup_devices(self, config_entry: ConfigEntry):
        """
        cleanup all devices that are no longer in use
        """
        _LOGGER.info(f"Cleanup devices for installation '{self._install_name}'")
        valid_identifiers = list(self._valid_device_ids.values())

        dr = device_registry.async_get(self.hass)
        registered_devices = device_registry.async_entries_for_config_entry(dr, config_entry.entry_id)

        for device in registered_devices:
            if all(id not in valid_identifiers for id in device.identifiers):
                _LOGGER.info(f"Remove obsolete device {next(iter(device.identifiers))} from installation '{self._install_name}'")
                dr.async_remove_device(device.id)


    async def async_cleanup_entities(self, config_entry: ConfigEntry):
        """
        cleanup all entities within this installation that are no longer in use
        """
        _LOGGER.info(f"Cleanup entities for installation '{self._install_name}'")

        er = entity_registry.async_get(self.hass)
        registered_entities = entity_registry.async_entries_for_config_entry(er, config_entry.entry_id)

        for entity in registered_entities:
            # Retrieve all valid ids matching the platform of this registered entity.
            # Note that platform and domain are mixed up in entity_registry
            valid_unique_ids = self._valid_unique_ids.get(entity.domain, [])

            if entity.unique_id not in valid_unique_ids:
                _LOGGER.info(f"Remove obsolete entity {entity.entity_id} ({entity.unique_id}) from installation '{self._install_name}'")
                er.async_remove(entity.entity_id)


    async def async_config_flow_data(self):
        """
        Fetch installation data from API.
        """
        _LOGGER.debug(f"Config flow data")

        await self._api.async_detect_for_config()  
        
        #_LOGGER.debug(f"install_map: {self._api.install_map}")
        return (self._api.install_map)


    async def async_config_change_role(self, install_id: str, role_old: DabPumpsUserRole, role_new: DabPumpsUserRole):
        """
        Try to update user role via API.
        """
        _LOGGER.debug(f"Config update role for {install_id} from {role_old} to {role_new}")
        success = await self._api.async_change_install_role(install_id, role_old, role_new) 
        if success:
            return role_new
        else:
            return None


    async def _async_update_data(self):
        """
        Fetch sensor data from API.
        
        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        _LOGGER.debug(f"Update data for installation '{self._install_name}'")

        # Fetch the actual data
        # Note: asyncio.TimeoutError and aiohttp.ClientError are already
        # handled by the data update coordinator.
        await self._api.async_detect_data(self._install_id, self._fetch_order)

        # If this was the first fetch, then make sure all next ones use the correct fetch order (web or cache)
        self._fetch_order = DabPumpsFetchOrder.NEXT

        # Periodically detect changes in the installation and trigger reload of the integration if needed.
        await self._async_detect_changes()

        #_LOGGER.debug(f"device_map: {self._api.device_map}")
        #_LOGGER.debug(f"config_map: {self._api.config_map}")
        #_LOGGER.debug(f"status_map: {self._api.status_map}")
        return (self._api.device_map, self._api.config_map, self._api.status_map)
    
    
    async def async_modify_data(self, status_key: str, entity_id: str, code: str|None = None, value: Any|None = None) -> DabPumpsStatus|None:
        """
        Set an entity param via the API.
        """
        status = self._api.status_map.get(status_key)
        if not status:
            # Not found
            return None

        # update the remote value
        success = await self._api.async_change_device_status(status, code=code, value=value)
        if success:
            status.code = code if code != None else status.code
            status.value = value if value != None else status.value
            status.update_ts = utcnow()

            return status
        else:
            return None

    
    async def _async_detect_changes(self):
        """Detect changes in the installation and trigger a integration reload if needed"""

        # Deliberately delay reload checks to prevent enless reloads if something is wrong
        if (utcnow() - self._reload_time).total_seconds() < self._reload_delay:
            return

        # Detect any changes
        reload = await self._async_detect_install_changes()
        if reload:
            self._reload_count += 1
            self.hass.config_entries.async_schedule_reload(self._config_entry_id)

        
    async def _async_detect_install_changes(self)  -> bool:
        """
        Detect any new devices. Returns True if a reload needs to be triggered else False
        """

        # Get list of device serials in HA device registry and as retrieved from Api
        old_serials: set[str] = set(self._valid_device_ids.keys())
        api_serials: set[str] = set([ d.serial for d in self._api.device_map.values() if d.install_id == self._install_id ])
        new_serials: set[str] = api_serials.difference(old_serials)

        for new_serial in new_serials:
            new_device = self._api.device_map.get(new_serial)
            _LOGGER.info(f"Found newly added device {new_device.serial} ({new_device.name}) for installation '{self._install_name}'. Trigger reload of integration.")
            return True
        
        return False


    async def async_get_diagnostics(self) -> dict[str, Any]:
        """
        Get all diagnostics values
        """
        return {
            "data": {
                "install_id": self._install_id,
                "user_name": self.user_name,
                "user_role": self.user_role,
                "language": self.language,
                "language_sys": self.system_language(),
            },
            "diagnostics": {
                "reload_count": self.reload_count,
            },
        }
    
