import asyncio
import logging
import math

from homeassistant import config_entries
from homeassistant import exceptions
from homeassistant.components.button import ButtonEntity
from homeassistant.components.button import ENTITY_ID_FORMAT
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

from aiodabpumps import (
    DabPumpsDevice,
    DabPumpsParams,
    DabPumpsStatus
)

from .coordinator import (
    DabPumpsCoordinator,
)

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
    await helper.async_setup_entry(Platform.BUTTON, DabPumpsButton, async_add_entities)


class DabPumpsButton(CoordinatorEntity, ButtonEntity, DabPumpsEntity):
    """
    Representation of a DAB Pumps Button Entity.
    
    Could be a configuration setting that is part of a pump like ESybox, Esybox.mini
    Or could be part of a communication module like DConnect Box/Box2
    """
    
    def __init__(self, coordinator: DabPumpsCoordinator, install_id: str, object_id: str, device: DabPumpsDevice, params: DabPumpsParams, status: DabPumpsStatus) -> None:
        """ 
        Initialize the sensor. 
        """

        CoordinatorEntity.__init__(self, coordinator)
        DabPumpsEntity.__init__(self, coordinator, params)

        #AJH
        _LOGGER.debug(f"button init for {object_id}")
        
        # Sanity check
        if params.type != 'enum':
            _LOGGER.error(f"Unexpected parameter type ({params.type}) for a button entity")

        # The unique identifiers for this sensor within Home Assistant
        unique_id = self.coordinator.create_id(device.name, status.key)
        
        self.object_id = object_id                          # Device.serial + status.key
        self.entity_id = ENTITY_ID_FORMAT.format(unique_id) # Device.name + status.key
        self.install_id = install_id

        self._coordinator = coordinator
        self._device = device
        self._params = params
        self._key = params.key
        self._dict = { k: self._get_string(v) for k,v in params.values.items() }

        # update creation-time only attributes
        _LOGGER.debug(f"Create entity '{self.entity_id}'")
        
        self._attr_unique_id = unique_id

        self._attr_has_entity_name = True
        self._attr_name = self._get_string(status.key)
        self._name = status.key

        self._attr_icon = self.get_icon()

        self._attr_entity_category = self.get_entity_category()
        self._attr_device_class = None

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
        """Handle updated data from the coordinator."""
        
        # find the correct status corresponding to this sensor
        (_, _, status_map) = self._coordinator.data
        status = status_map.get(self.object_id)
        if not status:
            return

        # Update any attributes
        if self._update_attributes(status):
            self.async_write_ha_state()
    
    
    def _update_attributes(self, status: DabPumpsStatus, force:bool=False):
        """
        Set entity value, unit and icon
        """
        
        # Button has no value to update
        # No changes
        return False
    
    
    async def async_press(self) -> None:
        """
        Press the button
        """

        # Pass the status.code and not the translated status.value
        code = next((code for code,value in self._dict.items()), None)
        if code:
            # AJH
            _LOGGER.debug(f"async_press: code={code} ({type(code)})")

            success = await self._coordinator.async_modify_data(self.object_id, self.entity_id, code=code)
            if success:
                self.async_write_ha_state()
