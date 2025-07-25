import asyncio
import logging
import math

from homeassistant import config_entries
from homeassistant import exceptions
from homeassistant.components.switch import SwitchDeviceClass
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.switch import ENTITY_ID_FORMAT
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

from homeassistant.const import (
    STATE_ON,
    STATE_OFF,
)

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

from .coordinator import (
    DabPumpsCoordinator,
)

from .const import (
    DOMAIN,
    SWITCH_VALUES_ON,
    SWITCH_VALUES_OFF,
    STATUS_VALIDITY_PERIOD,
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
    await helper.async_setup_entry(Platform.SWITCH, DabPumpsSwitch, async_add_entities)


class DabPumpsSwitch(CoordinatorEntity, SwitchEntity, DabPumpsEntity):
    """
    Representation of a DAB Pumps Switch Entity.
    
    Could be a configuration setting that is part of a pump like ESybox, Esybox.mini
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
            _LOGGER.error(f"Unexpected parameter type ({params.type}) for a select entity")

        # The unique identifiers for this sensor within Home Assistant
        unique_id = self._coordinator.create_id(device.name, status.key)
        
        self.object_id = object_id                          # Device.serial + status.key
        self.entity_id = ENTITY_ID_FORMAT.format(unique_id) # Device.name + status.key

        self._coordinator = coordinator
        self._device = device
        self._params = params
        self._key = params.key
        self._dict = { k: v for k,v in params.values.items() }

        # update creation-time only attributes
        _LOGGER.debug(f"Create entity '{self.entity_id}'")
        
        self._attr_unique_id = unique_id

        self._attr_has_entity_name = True
        self._attr_name = status.name
        self._name = status.key
        
        self._attr_entity_category = self.get_entity_category()
        self._attr_device_class = SwitchDeviceClass.SWITCH

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
        
        # Is the status expired?
        if not status.status_ts or status.status_ts+timedelta(seconds=STATUS_VALIDITY_PERIOD) > datetime.now(timezone.utc):

            # Use original status.code, not translated status.value to compare
            if status.code in SWITCH_VALUES_ON:
                attr_is_on = True
                attr_state = STATE_ON
                
            elif status.code in SWITCH_VALUES_OFF:
                attr_is_on = False
                attr_state = STATE_OFF

            else:
                attr_is_on = None
                attr_state = None
        else:
            attr_is_on = None
            attr_state = None

        # update value if it has changed
        if self._attr_is_on != attr_is_on or force:

            self._attr_is_on = attr_is_on
            self._attr_state = attr_state
            self._attr_unit_of_measurement = self.get_unit()
            
            self._attr_icon = self.get_icon()
            
            return True
            
        # No changes
        return False
    
    
    async def async_turn_on(self, **kwargs) -> None:
        """
        Turn the entity on.
        """

        # Pass the status.code and not the translated status.value
        code = next((code for code,value in self._dict.items() if code in SWITCH_VALUES_ON or value in SWITCH_VALUES_ON), None)
        if code:
            success = await self._coordinator.async_modify_data(self.object_id, self.entity_id, code=code)
            if success:
                self._attr_is_on = True
                self._attr_state = STATE_ON
                self.async_write_ha_state()
    
    
    async def async_turn_off(self, **kwargs) -> None:
        """
        Turn the entity off.
        """

        # Pass the status.code and not the translated status.value
        code = next((code for code,value in self._dict.items() if code in SWITCH_VALUES_OFF or value in SWITCH_VALUES_OFF), None)
        if code:
            success = await self._coordinator.async_modify_data(self.object_id, self.entity_id, code=code)
            if success:
                self._attr_is_on = False
                self._attr_state = STATE_OFF
                self.async_write_ha_state()
    
