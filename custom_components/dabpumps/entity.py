from dataclasses import dataclass
from datetime import datetime
import logging

from typing import Any, Self

from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from .const import (
    DOMAIN,
    ATTR_STORED_CODE,
    ATTR_STORED_VALUE,
    ATTR_STORED_TS,
    utcnow,
)
from .coordinator import (
    DabPumpsCoordinator,
)
from .data import (
    ParamCategory,
    ParamInfo,
    ParamStateClass,
    UnitInfo,
)
from pydabpumps import (
    DabPumpsDevice,
    DabPumpsParams,
    DabPumpsStatus,
)

# Define logger
_LOGGER = logging.getLogger(__name__)


@dataclass
class DabPumpsEntityExtraData(ExtraStoredData):
    """Object to hold extra stored data."""

    code: str = None
    value: str = None
    ts: datetime = None

    def as_dict(self) -> dict[str, Any]:
        """Return a dict representation of the sensor data."""
        return {
            ATTR_STORED_CODE: self.code,
            ATTR_STORED_VALUE: self.value,
            ATTR_STORED_TS: self.ts,
        }

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> Self | None:
        """Initialize a stored sensor state from a dict."""

        return cls(
            code = restored.get(ATTR_STORED_CODE),
            value = restored.get(ATTR_STORED_VALUE),
            ts = restored.get(ATTR_STORED_TS),
        )


class DabPumpsEntity(RestoreEntity):
    """
    Common funcionality for all DabPumps Entities:
    (DabPumpsSensor, DabPumpsBinarySensor, DabPumpsNumber, DabPumpsSelect, DabPumpsSwitch)
    """
    
    def __init__(self, coordinator: DabPumpsCoordinator, status_key: str, device: DabPumpsDevice, params: DabPumpsParams):
        self._coordinator = coordinator
        self._device = device
        self._params = params
        self._param_info = ParamInfo.find(params.group, status_key)
        self._unit_info = UnitInfo.find_by_dabpumps_unit(params.unit)

        # The unique identifiers for this sensor within Home Assistant
        self._status_key = status_key      # Key for lookup of status in the API
        self._attr_object_id = self._coordinator.create_id(device.serial, status_key) # Device.serial + status_key
        self._attr_unique_id = self._coordinator.create_id(device.name, status_key)   # Device.name + status_key
        
        self._attr_has_entity_name = True
        self._attr_name = params.name
        self._name = status_key

        self._attr_device_info = DeviceInfo(
            identifiers = {(DOMAIN, coordinator.create_id(self._device.serial))},
        )

        # Attributes to be restored in the next HA run
        self._status_code: str = None
        self._status_value: str = None
        self._status_ts: datetime = None


    @property
    def suggested_object_id(self) -> str | None:
        """Return input for object id."""
        return self._attr_object_id
    
    
    @property
    def unique_id(self) -> str:
        """Return a unique ID for use in home assistant."""
        return self._attr_unique_id
    
    
    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return self._attr_name
    

    @property
    def extra_restore_state_data(self) -> DabPumpsEntityExtraData | None:
        """
        Return entity specific state data to be restored on next HA run.
        """
        return DabPumpsEntityExtraData(
            code = self._status_code,
            value = self._status_value,
            ts = self._status_ts,
        )
    

    async def async_added_to_hass(self) -> None:
        """
        Handle when the entity has been added
        """
        await super().async_added_to_hass()

        # Get last data from previous HA run                      
        last_state = await self.async_get_last_state()
        last_extra = await self.async_get_last_extra_data()
        
        if last_state and last_extra:
            # Set entity value from restored data
            dict_extra = last_extra.as_dict()

            status = DabPumpsStatus(
                code = dict_extra.get(ATTR_STORED_CODE),
                value = dict_extra.get(ATTR_STORED_VALUE),
            )
            status_ts = dict_extra.get(ATTR_STORED_TS)

            if status_ts and isinstance(status_ts, str):
                status_ts = datetime.fromisoformat(status_ts)

            # Reduce tracing during startup. Can enable for specific development debugging
            #_LOGGER.debug(f"Restore entity '{self.entity_id}' value to {last_state.state} ({status.code}) with ts: {status.status_ts}")
        
            self._update_attributes(status, status_ts, force=True)
    

    def _update_attributes(self, status: DabPumpsStatus, status_ts: datetime, force:bool=False) -> bool:
        """
        Process any changes in value
        
        To be extended by derived entities
        """
        changed = False

        if self._status_code != status.code or self._status_value != status.value or self._status_ts != status_ts:
            self._status_code = status.code
            self._status_value = status.value
            self._status_ts = status_ts
            changed = True

        return changed


    def get_unit(self):
        """Return Home Assistant compatible unit of measurement"""
        return self._unit_info.ha_unit
        
    
    def get_icon(self):
        """Derive a suitable icon from the unit of measurement"""
        return self._unit_info.icon
    
    
    def get_number_device_class(self):
        """Derive the number entity device class from the param type or param unit of measurement"""
        if self._params.type == 'enum':
            return NumberDeviceClass.ENUM
        else:
            return self._unit_info.num_cls
    
    
    def get_sensor_device_class(self):
        """Derive the sensor entity device class from the param type or param unit of measurement"""
        if self._params.type == 'enum':
            return SensorDeviceClass.ENUM
        else:
            return self._unit_info.sen_cls   
    
    
    def get_sensor_state_class(self):
        """Derive the sensor state class from the param type or param info"""

        # Return StateClass=None for Enum or Label
        if self._params.type != 'measure':
            return None
        
        # Return StateClass=None for params that are a setting, unlikely to change often
        if self._params.change:
            return None
        
        # Return StateClass=None, TOTAL, TOTAL_INCREASING or MEASUREMENT depending param info
        # (controlled by group and key)
        match self._param_info.cls:
            case ParamStateClass.NONE:          return None
            case ParamStateClass.TOTAL:         return SensorStateClass.TOTAL
            case ParamStateClass.TOTAL_INC:     return SensorStateClass.TOTAL_INCREASING
            case ParamStateClass.MEASUREMENT:   return SensorStateClass.MEASUREMENT
            case _:                             return SensorStateClass.MEASUREMENT
    
    
    def get_entity_category(self):
        """Derive the sensor state class from the param info (controlled by group and key)"""

        match self._param_info.cat:
            case ParamCategory.SENSOR:          return None
            case ParamCategory.CONTROL:         return None            
            case ParamCategory.CONFIG:          return EntityCategory.CONFIG if self._coordinator.user_role in self._params.change else EntityCategory.DIAGNOSTIC
            case ParamCategory.DIAGNOSTICS:     return EntityCategory.DIAGNOSTIC
            case _:                             return None
    
    
    def get_number_step(self):
        """Determine a suitable number entity step size"""
        
        match self._unit_info.ha_unit:
            case 's':
                candidates = [3600, 60, 1]
            case 'min':
                candidates = [60, 1]
            case 'h':
                candidates = [24, 1]
            case _:
                candidates = [1000, 100, 10, 1]
                
        # find first candidate where min, max and diff are all dividable by (without remainder)
        if self._params.min is not None and self._params.max is not None:
            min = int(self._params.min)
            max = int(self._params.max)
            diff = max - min
            
            for c in candidates:
                if (min % c == 0) and (max % c == 0) and (diff % c == 0):
                    return c
                
        return None
    

