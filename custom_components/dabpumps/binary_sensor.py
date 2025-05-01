import asyncio
import logging
import math
import voluptuous as vol

from homeassistant import config_entries
from homeassistant import exceptions
from homeassistant.components.binary_sensor import PLATFORM_SCHEMA as PARENT_PLATFORM_SCHEMA
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.binary_sensor import ENTITY_ID_FORMAT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.const import CONF_UNIQUE_ID
from homeassistant.const import EntityCategory
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.exceptions import IntegrationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import async_get
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from datetime import datetime
from datetime import timezone
from datetime import timedelta

from collections import defaultdict
from collections import namedtuple

from aiodabpumps import (
    DabPumpsDevice,
    DabPumpsParams,
    DabPumpsStatus
)

from .const import (
    DOMAIN,
    BINARY_SENSOR_VALUES_ON,
    BINARY_SENSOR_VALUES_OFF,
    STATUS_VALIDITY_PERIOD,
)

from .coordinator import (
    DabPumpsCoordinator,
)

from .entity_base import (
    DabPumpsEntityHelperFactory,
    DabPumpsEntityHelper,
    DabPumpsEntity,
)


_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = PARENT_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Optional(CONF_UNIQUE_ID): cv.string,
    }
)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """
    Setting up the adding and updating of binary_sensor entities
    """
    helper = DabPumpsEntityHelperFactory.create(hass, config_entry)
    await helper.async_setup_entry(Platform.BINARY_SENSOR, DabPumpsBinarySensor, async_add_entities)


class DabPumpsBinarySensor(CoordinatorEntity, BinarySensorEntity, DabPumpsEntity):
    """
    Representation of a DAB Pumps Binary Sensor.
    
    Could be a sensor that is part of a pump like ESybox, Esybox.mini
    Or could be part of a communication module like DConnect Box/Box2
    """
    def __init__(self, coordinator: DabPumpsCoordinator, object_id: str, device: DabPumpsDevice, params: DabPumpsParams, status: DabPumpsStatus) -> None:
        """ 
        Initialize the sensor. 
        """

        CoordinatorEntity.__init__(self, coordinator)
        DabPumpsEntity.__init__(self, coordinator, params)
        
        # Sanity check
        if params.type != 'enum':
            _LOGGER.error(f"Unexpected parameter type ({self._params.type}) for a binary sensor")
            
        if len(params.values or []) != 2:
            _LOGGER.error(f"Unexpected parameter values ({self._params.values}) for a binary sensor")
            
        # The unique identifiers for this sensor within Home Assistant
        unique_id = self._coordinator.create_id(device.name, status.key)
        
        self.object_id = object_id                          # Device.serial + status.key
        self.entity_id = ENTITY_ID_FORMAT.format(unique_id) # Device.name + status.key
        
        self._device = device
        self._params = params
        
        # update creation-time only attributes
        _LOGGER.debug(f"Create entity '{self.entity_id}'")
        
        self._attr_unique_id = unique_id
        
        self._attr_has_entity_name = True
        self._attr_name = self._get_string(status.key)
        self._name = status.key
        
        self._attr_device_class = self._get_device_class()

        self._attr_device_info = DeviceInfo(
            identifiers = {(DOMAIN, self._device.serial)},
        )

        # Create all value related attributes
        self._update_attributes(status, force=True)
    
    
    @property
    def suggested_object_id(self) -> str | None:
        """Return input for object id."""
        return self.object_id
    
    
    @property
    def unique_id(self) -> str:
        """Return a unique ID for use in home assistant."""
        return self._attr_unique_id
    
    
    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return self._attr_name
    
    
    @callback
    def _handle_coordinator_update(self) -> None:
        """
        Handle updated data from the coordinator.
        """

        # find the correct status corresponding to this sensor
        (_, _, status_map) = self._coordinator.data
        status = status_map.get(self.object_id)
        if not status:
            return

        # Update any attributes
        if self._update_attributes(status):
            self.async_write_ha_state()
    
    
    def _update_attributes(self, status: DabPumpsStatus, force: bool = False):
        """
        Set entity value, unit and icon
        """

        # Is the status expired?
        if not status.status_ts or status.status_ts+timedelta(seconds=STATUS_VALIDITY_PERIOD) > datetime.now(timezone.utc):
        
            # Use original status.code, not translated status.value to compare
            if status.code in BINARY_SENSOR_VALUES_ON:
                is_on = True
            elif status.code in BINARY_SENSOR_VALUES_OFF:
                is_on = False
            else:
                is_on = None
        else:
            is_on = None
            
        # update value if it has changed
        if (self._attr_is_on != is_on) or force:
            
            self._attr_is_on = is_on
            return True
        
        # No changes
        return False
    
    
    def _get_device_class(self):
        """Return one of the BinarySensorDeviceClass.xyz or None"""
        return None
