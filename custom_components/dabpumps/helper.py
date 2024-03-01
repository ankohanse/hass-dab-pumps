import logging
import async_timeout

from datetime import timedelta
from typing import Any

from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.sensor import SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.entity_platform import AddEntitiesCallback


from .const import (
    DOMAIN,
    NAME,
    HELPER,
    CONF_INSTALL_ID,
    CONF_INSTALL_NAME,
    CONF_OPTIONS,
    BINARY_SENSOR_VALUES_ON,
    BINARY_SENSOR_VALUES_OFF,
    BINARY_SENSOR_VALUES_ALL,
    SWITCH_VALUES_ON,
    SWITCH_VALUES_OFF,
    SWITCH_VALUES_ALL,
)

from .coordinator import (
    DabPumpsCoordinatorFactory,
    DabPumpsCoordinator
)


_LOGGER = logging.getLogger(__name__)


class DabPumpsHelperFactory:
    
    @staticmethod
    def create(hass: HomeAssistant, config_entry: ConfigEntry):
        """
        Get existing helper for a config entry, or create a new one if it does not yet exist
        """
    
        # Get properties from the config_entry
        install_id = config_entry.data[CONF_INSTALL_ID]
        install_name = config_entry.data[CONF_INSTALL_NAME]
        options = config_entry.options

        if not HELPER in hass.data[DOMAIN]:
            hass.data[DOMAIN][HELPER] = {}
            
        # already created?
        helper = hass.data[DOMAIN][HELPER].get(install_id, None)
        if not helper:
            # Get an instance of our helper. This is unique to this install_id
            helper = DabPumpsHelper(hass, config_entry, install_id, install_name, options)
            hass.data[DOMAIN][HELPER][install_id] = helper
            
        return helper


class DabPumpsHelper:
    """My custom helper to provide common functions."""
    
    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, install_id, install_name, options):
        self.install_id = install_id
        self.install_name = install_name
        self.options = options

        # Get an instance of the DabPumpsCoordinator for this install_id
        self.coordinator = DabPumpsCoordinatorFactory.create(hass, config_entry)
        
    
    async def async_setup_entry(self, target_platform, create_entity, async_add_entities: AddEntitiesCallback):
        """
        Setting up the adding and updating of sensor and binary_sensor entities
        """    
        # Get data from the coordinator
        (device_map, config_map, status_map) = self.coordinator.data
        
        if not device_map or not config_map or not status_map:
            # If data returns False or is empty, log an error and return
            _LOGGER.warning(f"Failed to fetch sensor data - authentication failed or no data.")
            return
        
        _LOGGER.debug(f"Create entities for installation '{self.install_name}' ({self.install_id})")
        
        # Iterate all statusses to create sensor entities
        entities = []
        for object_id, status in status_map.items():
            
            # skip statusses that are not associated with a device in this installation
            device = device_map.get(status.serial, None)
            if not device or device.install_id != self.install_id:
                continue
            
            config = config_map.get(device.config_id, None)
            if not config:
                continue
            
            if not config.meta_params or status.key not in config.meta_params:
                _LOGGER.warning(f"Device metadata holds no info to create a sensor for '{status.key}' with value '{status.val}'.")
                continue
            
            params = config.meta_params[status.key]
            
            if not self._is_entity_whitelisted(params):
                # Some statusses (error1...error64) are deliberately skipped
                continue
            
            platform = self._get_entity_platform(params)
            
            if platform != target_platform:
                # This status will be handled via another platform
                continue
                
            else:
                # Create a Sensor, Binary_Sensor, or other entity for this status
                entity = create_entity(self.coordinator, self.install_id, object_id, device, params, status)
                entities.append(entity)
        
        _LOGGER.info(f"Add {len(entities)} {target_platform} entities for installation '{self.install_name} with {len(device_map)} devices")
        if entities:
            async_add_entities(entities)
    
    
    def _is_entity_whitelisted(self, params):
        """
        Determine whether an entry is whitelisted and should be added as sensor
        Or is blacklistred and should be ignored
        """
        
        # Whitelisted keys that would otherwise be excluded by blacklisted groups below:
        keys_whitelist = [
            'RamUsed',      # group: Debug
            'RamUsedMax',   # group: Debug
            'PumpDisable',  # group: System Management
            'LatestError'   # group: Errors
        ]
        # Blacklisted keys that would otherwise be included by whitelisted groups below:
        keys_blacklist = []
        
        groups_whitelist = []
        groups_blacklist = [
            'Debug',
            'System Management',
            'ModbusDevice',
            'Errors'
        ]
        
         # First check individual keys
        if params.key in keys_whitelist:
            return True
        
        if params.key in keys_blacklist:
            _LOGGER.debug(f"Skip create sensor for '{params.key}'; it is blacklisted'.")
            return False
        
        # Then check groups
        if params.group in groups_whitelist:
            return True

        if params.group in groups_blacklist:
            _LOGGER.debug(f"Skip create sensor for '{params.key}'; its group '{params.group}' is blacklisted'.")
            return False
        
        # If not blacklisted by any rule above, then it is whitelisted
        return True
        
        
    def _get_entity_platform(self, params):
        """
        Determine what platform an entry should be added into
        """
        
        # Is it a switch/select/number entity? 
        # Needs to have group 'Extra Comfort' and change rights for 'Customer'
        groups_config = [
            'Extra Comfort'
        ]
        if params.group in groups_config and 'C' in params.change:
            if params.type == 'enum':
                if len(params.values or []) == 2:
                    if all(k in SWITCH_VALUES_ALL and v in SWITCH_VALUES_ALL for k,v in params.values.items()):
                        return Platform.SWITCH
                    
                return Platform.SELECT
                
            elif params.type == 'measure' and params.min is not None and params.max is not None:
                return Platform.NUMBER
        
        # Is it a binary sensor?
        if params.type == 'enum' and len(params.values or []) == 2:
            if all(k in BINARY_SENSOR_VALUES_ALL and v in BINARY_SENSOR_VALUES_ALL for k,v in params.values.items()):
                return Platform.BINARY_SENSOR
        
        # Everything else will become a regular sensor
        return Platform.SENSOR
    
