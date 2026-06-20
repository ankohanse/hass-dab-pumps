import logging

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.components.datetime import ENTITY_ID_FORMAT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from datetime import datetime, timedelta
from datetime import timezone

from pydabpumps import (
    DabPumpsDevice,
    DabPumpsParams,
    DabPumpsParamType,
    DabPumpsStatus
)

from .const import (
    STATUS_VALIDITY_PERIOD,
    utcnow,
)
from .coordinator import (
    DabPumpsCoordinator,
)
from .entity import (
    DabPumpsEntity,
)
from .helper import (
    DabPumpsEntityHelper,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """
    Setting up the adding and updating of number entities
    """
    await DabPumpsEntityHelper(hass, config_entry).async_setup_entry(Platform.DATETIME, DabPumpsDateTime, async_add_entities)



class DabPumpsDateTime(CoordinatorEntity, DateTimeEntity, DabPumpsEntity):
    """
    Representation of a DAB Pumps Time Entity.
    
    Could be a configuration setting that is part of a pump like ESybox, Esybox.mini
    Or could be part of a communication module like DConnect Box/Box2
    """
    
    def __init__(self, coordinator: DabPumpsCoordinator, status_key: str, device: DabPumpsDevice, params: DabPumpsParams, status: DabPumpsStatus) -> None:
        """ Initialize the sensor. """
        CoordinatorEntity.__init__(self, coordinator)
        DabPumpsEntity.__init__(self, coordinator, status_key, device, params)
        
        # Sanity check
        if params.type != DabPumpsParamType.SETTINGS:
            _LOGGER.error(f"Unexpected parameter type ({params.type}) for a datetime entity")

        # The unique identifier for this sensor within Home Assistant
        self.entity_id = ENTITY_ID_FORMAT.format(self._attr_unique_id) # Device.name + params.key
        
        #_LOGGER.debug(f"Create entity '{self.entity_id}'")
        
        # update creation-time only attributes
        self._attr_device_class = None
        self._attr_entity_category = self.get_entity_category()

        # Create all value related attributes
        self._update_attributes(status, force=True)
    
    
    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # find the correct status corresponding to this entity
        (_, _, status_map) = self._coordinator.data
        
        status = status_map.get(self._status_key) if status_map is not None else None
        if status is None:
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
            # DAB Pumps value is an Iso string in local time
            if status.value is not None:
                attr_val = datetime.fromisoformat(status.value).astimezone()
            else:
                attr_val = None
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


    async def async_set_value(self, value: datetime) -> None:
        """Change the date/time"""
        
        # DAB Pumps value is an Iso string in local time
        entity_value = value.astimezone().replace(tzinfo=None).isoformat()
        trace_value = value

        _LOGGER.info(f"Set {self.entity_id} to {trace_value} ({entity_value})")
        
        status = await self._coordinator.async_modify_data(self._status_key, self.entity_id, value=entity_value)
        if status is not None:
            self._update_attributes(status, force=True)
            self.async_write_ha_state()


