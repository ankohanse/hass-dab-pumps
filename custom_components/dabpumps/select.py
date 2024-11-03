import asyncio
import logging
import math

from homeassistant import config_entries
from homeassistant import exceptions
from homeassistant.components.select import SelectEntity
from homeassistant.components.select import ENTITY_ID_FORMAT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.exceptions import IntegrationError
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
)

from .entity_base import (
    DabPumpsEntityHelperFactory,
    DabPumpsEntityHelper,
    DabPumpsEntity,
    
)


_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """
    Setting up the adding and updating of select entities
    """
    helper = DabPumpsEntityHelperFactory.create(hass, config_entry)
    await helper.async_setup_entry(Platform.SELECT, DabPumpsSelect, async_add_entities)


class DabPumpsSelect(CoordinatorEntity, SelectEntity, DabPumpsEntity):
    """
    Representation of a DAB Pumps Select Entity.
    
    Could be a configuration setting that is part of a pump like ESybox, Esybox.mini
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
        self._key = params.key
        self._dict = { k: self._get_string(v) for k,v in params.values.items() }

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
        
        if self._params.type != 'enum':
            _LOGGER.error(f"Unexpected parameter type ({self._params.type}) for a select entity")

        # Process any changes
        changed = False
        attr_val = self._dict.get(status.val, status.val) if status.val!=None else None

        # update creation-time only attributes
        if is_create:
            _LOGGER.debug(f"Create select entity '{status.key}' ({status.unique_id})")
            
            self._attr_unique_id = status.unique_id
            
            self._attr_has_entity_name = True
            self._attr_name = self._get_string(status.key)
            self._name = status.key
            
            self._attr_options = list(self._dict.values())
            self._attr_current_option = None
            
            self._attr_entity_category = self.get_entity_category()
            
            self._attr_device_class = None
            self._attr_device_info = DeviceInfo(
               identifiers = {(DOMAIN, self._device.serial)},
               name = self._device.name,
               manufacturer =  self._device.vendor,
               model = self._device.product,
               serial_number = self._device.serial,
               hw_version = self._device.version,
            )
            changed = True
        
        # update value if it has changed
        if is_create or self._attr_current_option != attr_val:
            self._attr_current_option = attr_val
            self._attr_unit_of_measurement = self.get_unit()
            
            self._attr_icon = self.get_icon()
            changed = True
        
        return changed
    
    
    async def async_select_option(self, option: str) -> None:
        """Change the selected option"""
        data_val = next((k for k,v in self._dict.items() if v == option), None)
        if data_val:
            _LOGGER.info(f"Set {self.entity_id} to {option} ({data_val})")
                
            success = await self._coordinator.async_modify_data(self.object_id, data_val)
            if success:
                self._attr_current_option = option
                self.async_write_ha_state()
    
    