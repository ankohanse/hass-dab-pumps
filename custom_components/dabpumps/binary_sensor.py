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


from datetime import timedelta
from datetime import datetime

from collections import defaultdict
from collections import namedtuple


from .const import (
    DOMAIN,
    COORDINATOR,
    CONF_INSTALL_ID,
    CONF_INSTALL_NAME,
    CONF_OPTIONS,
    BINARY_SENSOR_VALUES_ON,
    BINARY_SENSOR_VALUES_OFF,
    BINARY_SENSOR_VALUES_ALL,
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
    def __init__(self, coordinator, install_id, object_id, device, params, status) -> None:
        """ Initialize the sensor. """
        CoordinatorEntity.__init__(self, coordinator)
        DabPumpsEntity.__init__(self, coordinator, params)
        
        # The unique identifier for this sensor within Home Assistant
        self.object_id = object_id
        self.entity_id = ENTITY_ID_FORMAT.format(status.unique_id)
        self.install_id = install_id
        
        self._coordinator = coordinator
        self._device = device
        self._params = params
        
        # Create all attributes
        self._update_attributes(status, True)
    
    
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
        """Handle updated data from the coordinator."""

        # find the correct device and status corresponding to this sensor
        (_, _, status_map) = self._coordinator.data
        status = status_map.get(self.object_id)

        # Update any attributes
        if status:
            if self._update_attributes(status, False):
                self.async_write_ha_state()
    
    
    def _update_attributes(self, status, is_create):
        
        # Sanity check
        if self._params.type != 'enum':
            _LOGGER.error(f"Unexpected parameter type ({self._params.type}) for a binary sensor")
            
        if len(self._params.values or []) != 2:
            _LOGGER.error(f"Unexpected parameter values ({self._params.values}) for a binary sensor")
            
        # Lookup the dict string for the value and otherwise return the value itself
        val = self._params.values.get(status.val, status.val)
        if val in BINARY_SENSOR_VALUES_ON:
            is_on = True
        elif val in BINARY_SENSOR_VALUES_OFF:
            is_on = False
        else:
            is_on = None
            
        # Process any changes
        changed = False
        
        # update creation-time only attributes
        if is_create:
            _LOGGER.debug(f"Create binary_sensor '{status.key}' ({status.unique_id})")
            
            self._attr_unique_id = status.unique_id
            
            self._attr_has_entity_name = True
            self._attr_name = self._get_string(status.key)
            self._name = status.key
            
            self._attr_device_class = self._get_device_class()

            self._attr_device_info = DeviceInfo(
               identifiers = {(DOMAIN, self._device.serial)},
            )
            changed = True
        
        # update value if it has changed
        if is_create \
        or (self._attr_is_on != is_on):
            
            self._attr_is_on = is_on
            changed = True
            
        return changed
    
    
    def _get_device_class(self):
        """Return one of the BinarySensorDeviceClass.xyz or None"""
        return None
