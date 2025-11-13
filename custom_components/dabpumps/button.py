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

from pydabpumps import (
    DabPumpsDevice,
    DabPumpsParams,
    DabPumpsStatus
)

from .const import (
    DOMAIN,
    COORDINATOR,
    CONF_INSTALL_ID,
    CONF_INSTALL_NAME,
    CONF_OPTIONS,
)
from .coordinator import (
    DabPumpsCoordinator,
)
from .entity_base import (
    DabPumpsEntity,
)
from .entity_helper import (
    DabPumpsEntityHelperFactory,
    DabPumpsEntityHelper,
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
    
    def __init__(self, coordinator: DabPumpsCoordinator, object_id: str, device: DabPumpsDevice, params: DabPumpsParams, status: DabPumpsStatus) -> None:
        """ 
        Initialize the sensor. 
        """

        CoordinatorEntity.__init__(self, coordinator)
        DabPumpsEntity.__init__(self, coordinator, object_id, device, params)

        # Sanity check
        if params.type != 'enum':
            _LOGGER.error(f"Unexpected parameter type ({params.type}) for a button entity")

        # The unique identifiers for this sensor within Home Assistant
        self.entity_id = ENTITY_ID_FORMAT.format(self._attr_unique_id) # Device.name + params.key

        _LOGGER.debug(f"Create entity '{self.entity_id}'")
        
        # update creation-time only attributes
        self._dict = { k: v for k,v in params.values.items() }

        self._attr_icon = self.get_icon()

        self._attr_entity_category = self.get_entity_category()
        self._attr_device_class = None

        self._attr_device_info = DeviceInfo(
            identifiers = {(DOMAIN, self._device.serial)},
        )
        
        # Create all value related attributes
        self._update_attributes(status, force=True)
    
    
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
    
    
    def _update_attributes(self, status: DabPumpsStatus, force:bool=False) -> bool:
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
        if code is None:
            return
        
        status = await self._coordinator.async_modify_data(self.object_id, self.entity_id, code=code)
        if status is not None:
            self._update_attributes(status, force=True)
            self.async_write_ha_state()
