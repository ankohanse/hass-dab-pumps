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

from datetime import timedelta
from datetime import datetime

from collections import defaultdict
from collections import namedtuple

from aiodabpumps import (
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
    DabPumpsEntityHelperFactory,
    DabPumpsEntityHelper,
    DabPumpsEntity,
    
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
    
    def __init__(self, coordinator: DabPumpsCoordinator, install_id: str, object_id: str, device: DabPumpsDevice, params: DabPumpsParams, status: DabPumpsStatus) -> None:
        """ 
        Initialize the sensor. 
        """

        CoordinatorEntity.__init__(self, coordinator)
        DabPumpsEntity.__init__(self, coordinator, params)
        
        # The unique identifiers for this sensor within Home Assistant
        unique_id = self.coordinator.create_id(device.name, status.key)
        
        self.object_id = object_id                          # Device.serial + status.key
        self.entity_id = ENTITY_ID_FORMAT.format(unique_id) # Device.name + status.key
        self.install_id = install_id
        
        self._coordinator = coordinator
        self._device = device
        self._params = params
        
        # update creation-time only attributes
        _LOGGER.debug(f"Create entity '{self.entity_id}'")
        
        self._attr_unique_id = unique_id
        
        self._attr_has_entity_name = True
        self._attr_name = self._get_string(status.key)
        self._name = status.key
        
        self._attr_state_class = self.get_sensor_state_class()
        self._attr_entity_category = self.get_entity_category()

        self._attr_device_class = self.get_sensor_device_class() 
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
    
    
    def _update_attributes(self, status: DabPumpsStatus, force:bool=False):
        """
        Set entity value, unit and icon
        """

        # Gather attributes
        attr_val = status.value
        attr_unit = self.get_unit();

        match self._params.type:
            case 'measure':
                if self._params.weight and self._params.weight != 0:
                    attr_precision = int(math.floor(math.log10(1.0 / self._params.weight)))
                else:
                    attr_precision = 0;
        
            case 'enum' | 'label' | _:
                attr_precision = None

        # additional check for TOTAL and TOTAL_INCREASING values:
        # ignore decreases that are not significant (less than 50% change)
        if self._attr_state_class in [SensorStateClass.TOTAL, SensorStateClass.TOTAL_INCREASING] and \
           self._attr_native_value is not None and \
           attr_val is not None and \
           attr_val < self._attr_native_value and \
           not check_percentage_change(self._attr_native_value, attr_val, 50):
            
            _LOGGER.debug(f"Ignore non-significant decrease in sensor '{status.key}' ({self.unique_id}) from {self._attr_native_value} to {attr_val}")
            attr_val = self._attr_native_value

        # update value if it has changed
        if self._attr_native_value != attr_val or force:

            self._attr_native_value = attr_val
            self._attr_native_unit_of_measurement = attr_unit
            self._attr_suggested_display_precision = attr_precision
            
            self._attr_icon = self.get_icon()
            return True
        
        # No changes
        return False
    
