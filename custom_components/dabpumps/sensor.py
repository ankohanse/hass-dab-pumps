import asyncio
import logging
import math

from homeassistant import config_entries
from homeassistant import exceptions
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.sensor import SensorStateClass
from homeassistant.components.sensor import ENTITY_ID_FORMAT
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
from homeassistant.helpers.significant_change import check_percentage_change

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
    STATUS_VALIDITY_PERIOD,
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
    Setting up the adding and updating of sensor entities
    """
    helper = DabPumpsEntityHelperFactory.create(hass, config_entry)
    await helper.async_setup_entry(Platform.SENSOR, DabPumpsSensor, async_add_entities)


class DabPumpsSensor(CoordinatorEntity, SensorEntity, DabPumpsEntity):
    """
    Representation of a DAB Pumps Sensor.
    
    Could be a sensor that is part of a pump like ESybox, Esybox.mini
    Or could be part of a communication module like DConnect Box/Box2
    """
    
    def __init__(self, coordinator: DabPumpsCoordinator, object_id: str, device: DabPumpsDevice, params: DabPumpsParams, status: DabPumpsStatus) -> None:
        """ 
        Initialize the sensor. 
        """

        CoordinatorEntity.__init__(self, coordinator)
        DabPumpsEntity.__init__(self, coordinator, object_id, device, params)
        
        # The unique identifiers for this sensor within Home Assistant
        self.entity_id = ENTITY_ID_FORMAT.format(self._attr_unique_id) # Device.name + params.key
        
        _LOGGER.debug(f"Create entity '{self.entity_id}'")
        
        # update creation-time only attributes
        self._attr_state_class = self.get_sensor_state_class()
        self._attr_entity_category = self.get_entity_category()

        self._attr_device_class = self.get_sensor_device_class() 
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
          
        # Is the status expired?
        if not status.status_ts or status.status_ts+timedelta(seconds=STATUS_VALIDITY_PERIOD) > datetime.now(timezone.utc):
            attr_val = status.value
        else:
            attr_val = None

        # Gather other attributes
        attr_unit = self.get_unit();

        match self._params.type:
            case 'measure':
                if self._params.weight and self._params.weight != 0:
                    attr_precision = int(math.floor(math.log10(1.0 / self._params.weight)))
                else:
                    attr_precision = 0;
            
            case 'enum' | 'label' | _:
                attr_precision = None

                # Sensors with device_class enum cannot have a unit of measurement
                # Instead include the unit with the value. Occurs for Easybox Diver 'tankminlev'.
                if attr_val and attr_unit:
                    attr_val = f"{attr_val} {attr_unit}"
                attr_unit = None

        # additional checks for TOTAL and TOTAL_INCREASING values
        if self._attr_state_class in [SensorStateClass.TOTAL, SensorStateClass.TOTAL_INCREASING]:

            # ignore decreases that are not significant (less than 50% change)
            if self._attr_native_value is not None and \
               attr_val is not None and \
               attr_val < self._attr_native_value and \
               not check_percentage_change(self._attr_native_value, attr_val, 50):
            
                _LOGGER.debug(f"Ignore non-significant decrease in sensor '{status.key}' ({self.unique_id}) from {self._attr_native_value} to {attr_val}")
                attr_val = self._attr_native_value

        # update value if it has changed
        changed = super()._update_attributes(status, force)

        if force or self._attr_native_value != attr_val:

            self._attr_native_value = attr_val
            self._attr_native_unit_of_measurement = attr_unit
            self._attr_suggested_display_precision = attr_precision
            
            self._attr_icon = self.get_icon()
            changed = True
        
        return changed
    
