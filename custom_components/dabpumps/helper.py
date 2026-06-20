import logging

from dataclasses import dataclass

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
    DabPumpsCoordinator,
)
from .data import (
    ParamInfo,
)


_LOGGER = logging.getLogger(__name__)


class DabPumpsEntityHelper:
    """My custom helper to provide common functions."""
    
    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry):
        """
        Get entity helper for a config entry.
        The entry is short lived (only during init) and does not contain state data,
        therefore no need to cache it in hass.data
        """
        self._coordinator = DabPumpsCoordinatorFactory.create(hass, config_entry)
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
        
        _LOGGER.debug(f"Create {target_platform} entities for installation '{self._coordinator.install_name}'")

        # Iterate all statuses to create sensor entities
        entities = []
        valid_unique_ids: list[str] = []

        for status_key, status in status_map.items():

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

            platform = self._get_entity_platform(params)
            if platform != target_platform:
                # This status will be handled via another platform or is completely suppressed
                continue
                
            # Create a Sensor, Binary_Sensor, Number, Select, Switch or other entity for this status
            entity = None                
            try:
                entity = target_class(self._coordinator, status_key, device, params, status)
                entities.append(entity)
                
                valid_unique_ids.append(entity.unique_id)

            except Exception as  ex:
                _LOGGER.warning(f"Could not instantiate {platform} entity class for {status_key}. Details: {ex}")

        # Remember valid unique_ids per platform so we can do an entity cleanup later
        self._coordinator.set_valid_unique_ids(target_platform, valid_unique_ids)

        # Now add the entities to the entity_registry
        _LOGGER.info(f"Add {len(entities)} {target_platform} entities for installation '{self._coordinator.install_name}'")
        if entities:
            async_add_entities(entities)

        
    def _get_entity_platform(self, params):
        """
        Determine what platform an entry should be added into
        """
        
        # Find the datapoint containing info about how to handle this param
        info = ParamInfo.find(params.group, params.key)

        # Could it be a button/switch/select/number config or control entity? 
        # Needs to have all of:
        # - allowed as visible and modifyable entity in the Datapoints
        # - change rights for the user role
        if info is not None and info.vis and info.mod and self._coordinator.user_role in params.change:

            # Is it a a button/switch/select ?
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

            elif params.type == 'settings':
                if params.key in ['HolidayModeLocalTimeStart', 'HolidayModeLocalTimeEnd']:
                    return Platform.DATETIME
        
        # Could it be a sensor or binary sensor entity? 
        # Needs to have all of:
        # - not fit or fall through the tests for a button/switch/select/number config or control entity
        # - allowed as visible entity in the Datapoints
        # 
        # Note: no need to check view rights for the user role; if we get inside this function then we have a status value for the entity
        if info is not None and info.vis:

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
        
        # Suppress all params for which we do not have view rights
        return None
    

