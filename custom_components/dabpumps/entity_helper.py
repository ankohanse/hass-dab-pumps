import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

import homeassistant.helpers.entity_registry as entity_registry

from .const import (
    PLATFORMS,
    BINARY_SENSOR_VALUES_ALL,
    SWITCH_VALUES_ALL,
    BUTTON_VALUES_ALL,
)
from .coordinator import (
    DabPumpsCoordinatorFactory,
    DabPumpsCoordinator
)


_LOGGER = logging.getLogger(__name__)


class DabPumpsEntityHelperFactory:
    
    @staticmethod
    def create(hass: HomeAssistant, config_entry: ConfigEntry):
        """
        Get entity helper for a config entry.
        The entry is short lived (only during init) and does not contain state data,
        therefore no need to cache it in hass.data
        """
    
        # Get an instance of the DabPumpsCoordinator for this install_id
        coordinator = DabPumpsCoordinatorFactory.create(hass, config_entry)
    
        # Get an instance of our helper
        return DabPumpsEntityHelper(hass, coordinator)


class DabPumpsEntityHelper:
    """My custom helper to provide common functions."""
    
    def __init__(self, hass: HomeAssistant, coordinator: DabPumpsCoordinator):
        self._coordinator = coordinator
        self._entity_registry = entity_registry.async_get(hass)
        
    
    async def async_setup_entry(self, target_platform, target_class, async_add_entities: AddEntitiesCallback):
        """
        Setting up the adding and updating of sensor and binary_sensor entities
        """    
        # Get data from the coordinator
        (device_map, config_map, status_map) = self._coordinator.data
        
        if not device_map or not config_map or not status_map:
            # If data returns False or is empty, log an error and return
            _LOGGER.warning(f"Failed to fetch sensor data - authentication failed or no data.")
            return
        
        other_platforms = [p for p in PLATFORMS if p != target_platform]
        
        _LOGGER.debug(f"Create {target_platform} entities for installation '{self._coordinator.install_name}'")

        # Iterate all statuses to create sensor entities
        entities = []
        valid_unique_ids: list[str] = []

        for object_id, status in status_map.items():

            # skip statuses that are not associated with a device in this installation
            device = device_map.get(status.serial, None)
            if not device or device.install_id != self._coordinator.install_id:
                continue
            
            config = config_map.get(device.config_id, None)
            if not config:
                continue
            
            params = config.meta_params.get(status.key, None) if config.meta_params else None
            if not params:
                continue

            if not self._is_entity_whitelisted(params):
                # Some statuses (error1...error64) are deliberately skipped
                continue
            
            platform = self._get_entity_platform(params)
            if platform != target_platform:
                # This status will be handled via another platform
                continue
                
            # Create a Sensor, Binary_Sensor, Number, Select, Switch or other entity for this status
            entity = None                
            try:
                entity = target_class(self._coordinator, object_id, device, params, status)
                entities.append(entity)
                
                valid_unique_ids.append(entity.unique_id)

            except Exception as  ex:
                _LOGGER.warning(f"Could not instantiate {platform} entity class for {object_id}. Details: {ex}")

        # Remember valid unique_ids per platform so we can do an entity cleanup later
        self._coordinator.set_valid_unique_ids(target_platform, valid_unique_ids)

        # Now add the entities to the entity_registry
        _LOGGER.info(f"Add {len(entities)} {target_platform} entities for installation '{self._coordinator.install_name}'")
        if entities:
            async_add_entities(entities)
    
    
    def _is_entity_whitelisted(self, params):
        """
        Determine whether an entry is whitelisted and should be added as sensor/binary sensor/number/select/switch
        Or is blacklistred and should be ignored
        """
        
        # Whitelisted keys that would otherwise be excluded by blacklisted groups below:
        keys_whitelist = [
            'RamUsed',                  # group: Debug
            'RamUsedMax',               # group: Debug
            'LatestError',              # group: Errors
            'RF_EraseHistoricalFault',  # group: Errors
        ]

        # Blacklisted keys that would otherwise be included by whitelisted groups below:
        keys_blacklist = [
            'IdentifyDevice',           # group: System Management
            'Identify',                 # group: Advanced
            'Reboot',                   # group: Advanced
            'UpdateSystem',             # group: Advanced
            'UpdateFirmware',           # group: Firmware Updates
            'UpdateProgress',           # group: Firmware Updates
            'PW_ModifyPassword',        # group: Technical Assistance
        ]
        
        groups_whitelist = []
        groups_blacklist = [
            'Debug',
            'ModbusDevice',
            'Errors'
        ]

        # First check if entity is allowed to be viewed according to user_role
        if self._coordinator.user_role not in params.view:
            return False
        
        # Then check individual keys
        if params.key in keys_whitelist:
            return True
        
        if params.key in keys_blacklist:
            return False
        
        # Then check groups
        if params.group in groups_whitelist:
            return True

        if params.group in groups_blacklist:
            return False
        
        # If not blacklisted by any rule above, then it is whitelisted
        return True
        
        
    def _get_entity_platform(self, params):
        """
        Determine what platform an entry should be added into
        """
        
        # Is it a button/switch/select/number config or control entity? 
        # Needs to have change rights for the user role
        # And needs to be in group 'Extra Comfort' or be a specific key
        # that would otherwise be excluded as group
        keys_config = [
            'PumpDisable',
            'RF_EraseHistoricalFault',
        ]
        groups_config = [
            'Extra Comfort',
            'Setpoint',
            'System Management',
        ]
        is_config = False
        if self._coordinator.user_role in params.change:
            if params.key in keys_config:
                is_config = True
            elif params.group in groups_config:
                is_config = True
        
        if is_config:
            if params.type == 'enum':
                # With exactly 1 possible value that are of 'press' type it becomes a button
                if len(params.values or []) == 1:
                    if all(k in BUTTON_VALUES_ALL for k,v in params.values.items()):
                        return Platform.BUTTON

                # With exactly 2 possible values that are of ON/OFF type it becomes a switch
                if len(params.values or []) == 2:
                    if all(k in SWITCH_VALUES_ALL and v in SWITCH_VALUES_ALL for k,v in params.values.items()):
                        return Platform.SWITCH
                    
                # With more values or not of ON/OFF type it becomes a Select
                return Platform.SELECT
                
            # Is it a numeric type?
            elif params.type == 'measure':
                if params.unit == 's':
                    return Platform.TIME
                else:
                    return Platform.NUMBER
        
        # Only view rights or does not fit in one of the modifyable entities
        if params.type == 'enum':
            # Suppress buttons if we only have view rights
            if len(params.values or []) == 1:
                if all(k in BUTTON_VALUES_ALL for k,v in params.values.items()):
                    return None
    
            # Is it a binary sensor?
            if len(params.values or []) == 2:
                if all(k in BINARY_SENSOR_VALUES_ALL and v in BINARY_SENSOR_VALUES_ALL for k,v in params.values.items()):
                    return Platform.BINARY_SENSOR
    
        # Everything else will become a regular sensor
        return Platform.SENSOR
    

