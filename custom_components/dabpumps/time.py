import asyncio
import logging
import math

from homeassistant import config_entries
from homeassistant import exceptions
from homeassistant.components.time import TimeEntity
from homeassistant.components.time import ENTITY_ID_FORMAT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.exceptions import IntegrationError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import async_get
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from datetime import datetime
from datetime import time
from datetime import timezone
from datetime import timedelta

from collections import defaultdict
from collections import namedtuple

from pydabpumps import (
    DabPumpsDevice,
    DabPumpsParams,
    DabPumpsStatus
)

from .const import (
    DOMAIN,
    STATUS_VALIDITY_PERIOD,
    utcnow,
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
    Setting up the adding and updating of number entities
    """
    helper = DabPumpsEntityHelperFactory.create(hass, config_entry)
    await helper.async_setup_entry(Platform.TIME, DabPumpsTime, async_add_entities)


class DabPumpsTime(CoordinatorEntity, TimeEntity, DabPumpsEntity):
    """
    Representation of a DAB Pumps Time Entity.
    
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
        if params.type != 'measure':
            _LOGGER.error(f"Unexpected parameter type ({params.type}) for a time entity")

        # The unique identifiers for this sensor within Home Assistant
        self.entity_id = ENTITY_ID_FORMAT.format(self._attr_unique_id) # Device.name + params.key
        
        _LOGGER.debug(f"Create entity '{self.entity_id}'")
        
        # Prepare attributes
        attr_min = int(self._params.min) if self._params.min is not None else None
        attr_max = int(self._params.max) if self._params.max is not None else None
        attr_step = self.get_number_step()
        
        # update creation-time only attributes
        self._attr_device_class = None
        self._attr_entity_category = self.get_entity_category()
        if attr_min:
            self._attr_native_min_value = attr_min
        if attr_max:
            self._attr_native_max_value = attr_max
        self._attr_native_step = attr_step
        
        self._attr_device_info = DeviceInfo(
            identifiers = {(DOMAIN, self._device.serial)},
        )

        # Create all value related attributes
        self._update_attributes(status, force=True)
    
    
    @callback
    def _handle_coordinator_update(self) -> None:
        """
        Handle updated data from the coordinator.
        """

        # find the correct status corresponding to this entity
        (_, _, status_map) = self._coordinator.data
        status = status_map.get(self.object_id)
        if not status:
            return

        # Update any attributes
        if self._update_attributes(status):
            self.async_write_ha_state()
    
    
    def _update_attributes(self, status: DabPumpsStatus, force: bool = False) -> bool:
        """
        Set entity value, unit and icon
        """
        
        # Is the status expired?
        if not status.status_ts or status.status_ts+timedelta(seconds=STATUS_VALIDITY_PERIOD) > utcnow():
            # DAB Pumps value is seconds since midnight with values between 0 (00:00) and 86340 (23:59).
            # TimeEntity expects time object and can only be between 00:00 and 23:59
            # We sneakily replace value 1440 (24:00) into 23:59
            if int(status.value) >= 86340:
                attr_val = time(23, 59)
            else:
                hour = int(status.value // 3600)
                minute = int( (status.value % 3600) // 60)
                attr_val = time(hour, minute)
        else:
            attr_val = None

        # update value if it has changed
        changed = super()._update_attributes(status, force)

        if force or self._attr_native_value != attr_val:

            self._attr_state = attr_val
            self._attr_native_value = attr_val

            self._attr_icon = self.get_icon()
            changed = True
        
        return changed
    

    async def async_set_value(self, value: time) -> None:
        """Change the date/time"""
        
        # DAB Pumps value is seconds since midnight with values between 0 (00:00) and 86340 (23:59).
        # TimeEntity expects time object and can only be between 00:00 and 23:59
        entity_value = value.hour * 3600 + value.minute * 60
        trace_value = value

        _LOGGER.info(f"Set {self.entity_id} to {trace_value} ({entity_value})")
        
        status = await self._coordinator.async_modify_data(self.object_id, self.entity_id, value=entity_value)
        if status is not None:
            self._update_attributes(status, force=True)
            self.async_write_ha_state()
