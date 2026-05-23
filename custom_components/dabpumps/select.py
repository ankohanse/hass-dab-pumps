import logging

from homeassistant.components.select import SelectEntity
from homeassistant.components.select import ENTITY_ID_FORMAT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from datetime import timedelta

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
from .entity import (
    DabPumpsEntity,
)
from .helper import (
    DabPumpsEntityHelper,
)


_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """
    Setting up the adding and updating of select entities
    """
    await DabPumpsEntityHelper(hass, config_entry).async_setup_entry(Platform.SELECT, DabPumpsSelect, async_add_entities)


class DabPumpsSelect(CoordinatorEntity, SelectEntity, DabPumpsEntity):
    """
    Representation of a DAB Pumps Select Entity.
    
    Could be a configuration setting that is part of a pump like ESybox, Esybox.mini
    Or could be part of a communication module like DConnect Box/Box2
    """
    
    def __init__(self, coordinator: DabPumpsCoordinator, status_key: str, device: DabPumpsDevice, params: DabPumpsParams, status: DabPumpsStatus) -> None:
        """ 
        Initialize the sensor. 
        """

        CoordinatorEntity.__init__(self, coordinator)
        DabPumpsEntity.__init__(self, coordinator, status_key, device, params)
        
        # Sanity check
        if params.type != 'enum':
            _LOGGER.error(f"Unexpected parameter type ({params.type}) for a select entity")

        # The unique identifiers for this sensor within Home Assistant
        self.entity_id = ENTITY_ID_FORMAT.format(self._attr_unique_id) # Device.name + params.key
        
        # Reduce tracing during startup. Can enable for specific development debugging
        #_LOGGER.debug(f"Create entity '{self.entity_id}'")
        
        # update creation-time only attributes
        self._dict = { k: v for k,v in params.values.items() }

        self._attr_options = list(self._dict.values())
        self._attr_current_option = None
        
        self._attr_entity_category = self.get_entity_category()
        self._attr_device_class = None

        # Create all value related attributes
        self._update_attributes(status, force=True)
    
    
    @callback
    def _handle_coordinator_update(self) -> None:
        """
        Handle updated data from the coordinator.
        """
        
        # find the correct status corresponding to this sensor
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
            attr_val = status.value
        else:
            attr_val = None

        # update value if it has changed
        changed = super()._update_attributes(status, force)

        if force or self._attr_current_option != attr_val:

            self._attr_current_option = attr_val
            self._attr_unit_of_measurement = self.get_unit()
            self._attr_icon = self.get_icon()
            changed = True
        
        return changed
    
    
    async def async_select_option(self, option: str) -> None:
        """
        Change the selected option
        """

        # Pass the status.code and not just the translated status.value
        (code,value) = next(( (code,value) for code,value in self._dict.items() if value == option), None)
        if code is None:
            return

        status = await self._coordinator.async_modify_data(self._status_key, self.entity_id, code=code, value=value)
        if status is not None:
            self._update_attributes(status, force=True)
            self.async_write_ha_state()
    
    