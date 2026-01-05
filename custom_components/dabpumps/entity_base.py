from dataclasses import dataclass
from datetime import datetime
import logging

from typing import Any, Self

from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.const import PERCENTAGE
from homeassistant.const import REVOLUTIONS_PER_MINUTE
from homeassistant.const import UnitOfInformation
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.const import UnitOfElectricPotential
from homeassistant.const import UnitOfEnergy
from homeassistant.const import UnitOfLength
from homeassistant.const import UnitOfPower
from homeassistant.const import UnitOfPressure
from homeassistant.const import UnitOfVolume
from homeassistant.const import UnitOfVolumeFlowRate
from homeassistant.const import UnitOfTemperature
from homeassistant.const import UnitOfTime
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from .const import (
    ATTR_STORED_CODE,
    ATTR_STORED_VALUE,
    ATTR_STORED_TS,
    utcnow,
)
from .coordinator import (
    DabPumpsCoordinator,
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
    
    def __init__(self, coordinator: DabPumpsCoordinator, object_id: str, device: DabPumpsDevice, params: DabPumpsParams):
        self._coordinator = coordinator
        self._device = device
        self._params = params
        self._attr_unit = self._convert_to_unit()

        # The unique identifiers for this sensor within Home Assistant
        self.object_id = object_id                                                  # Device.serial + params.key
        self._attr_unique_id = self._coordinator.create_id(device.name, params.key) # Device.name + params.key
        
        self._attr_has_entity_name = True
        self._attr_name = params.name
        self._name = params.key

        # Attributes to be restored in the next HA run
        self._status_code: str = None
        self._status_value: str = None
        self._status_ts: datetime = None


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
                serial = self._device.serial,
                key = self._params.key,
                name = self.name,
                code = dict_extra.get(ATTR_STORED_CODE),
                value = dict_extra.get(ATTR_STORED_VALUE),
                unit = self._params.unit,
                status_ts = dict_extra.get(ATTR_STORED_TS),
                update_ts = None,
            )

            _LOGGER.debug(f"Restore entity '{self.entity_id}' value to {last_state.state} ({status.code}) with ts: {status.status_ts}")
        
            self._update_attributes(status, force=True)
    

    def _update_attributes(self, status: DabPumpsStatus, force:bool=False) -> bool:
        """
        Process any changes in value
        
        To be extended by derived entities
        """
        changed = False

        if self._status_code != status.code or self._status_value != status.value or self._status_ts != status.status_ts:
            self._status_code = status.code
            self._status_value = status.value
            self._status_ts = status.status_ts
            changed = True

        return changed

    
    def _convert_to_unit(self):
        """Convert from DAB Pumps units to Home Assistant units"""
        match self._params.unit:
            case '°C':          return UnitOfTemperature.CELSIUS
            case '°F':          return UnitOfTemperature.FAHRENHEIT
            case 'bar':         return UnitOfPressure.BAR
            case 'psi':         return UnitOfPressure.PSI
            case 'mc':          return UnitOfVolume.CUBIC_METERS
            case 'l':           return UnitOfVolume.LITERS
            case 'l/min':       return UnitOfVolumeFlowRate.LITERS_PER_MINUTE
            case 'gall':        return UnitOfVolume.GALLONS
            case 'gall/min':    return UnitOfVolumeFlowRate.GALLONS_PER_MINUTE
            case 'gpm':         return UnitOfVolumeFlowRate.GALLONS_PER_MINUTE
            case 'cm':          return UnitOfLength.CENTIMETERS
            case 'inch':        return UnitOfLength.INCHES
            case 'ms':          return UnitOfTime.MILLISECONDS
            case 's':           return UnitOfTime.SECONDS
            case 'secondi':     return UnitOfTime.SECONDS
            case 'min':         return UnitOfTime.MINUTES
            case 'h':           return UnitOfTime.HOURS
            case 'rpm':         return REVOLUTIONS_PER_MINUTE
            case 'B':           return UnitOfInformation.BYTES
            case 'kB':          return UnitOfInformation.KILOBYTES
            case 'KB':          return UnitOfInformation.KILOBYTES
            case 'MByte':       return UnitOfInformation.MEGABYTES
            case '%':           return PERCENTAGE
            case 'V':           return UnitOfElectricPotential.VOLT
            case 'A':           return UnitOfElectricCurrent.AMPERE
            case 'W':           return UnitOfPower.WATT
            case 'kW':          return UnitOfPower.KILO_WATT
            case 'kWh':         return UnitOfEnergy.KILO_WATT_HOUR
            case 'Address':     return None
            case 'SW. Vers.':   return None
            case '':            return None
            case 'None' | None: return None
            
            case _:
                _LOGGER.warning(f"DAB Pumps encountered a unit or measurement '{self._params.unit}' for '{self._params.key}' that may not be supported by Home Assistant. Please contact the integration developer to have this resolved.")
                return self._params.unit
    
    
    def get_unit(self):
        return self._attr_unit
        
    
    def get_icon(self):
        """Convert from HA unit to icon"""
        match self._attr_unit:
            case '°C':      return 'mdi:thermometer'
            case '°F':      return 'mdi:thermometer'
            case 'bar':     return 'mdi:water-pump'
            case 'psi':     return 'mdi:water-pump'
            case 'm³':      return 'mdi:water'
            case 'L':       return 'mdi:water'
            case 'gal':     return 'mdi:water'
            case 'L/min':   return 'mdi:hydro-power'
            case 'gal/min': return 'mdi:hydro-power'
            case 'mm':      return 'mdi:waves-arrow-up'
            case 'cm':      return 'mdi:waves-arrow-up'
            case 'in':      return 'mdi:waves-arrow-up'
            case 's':       return 'mdi:timer-sand'
            case 'min':     return 'mdi:timer-sand'
            case 'h':       return 'mdi:timer'
            case 'B':       return 'mdi:memory'
            case 'kB':      return 'mdi:memory'
            case 'MB':      return 'mdi:memory'
            case 'kB/s':    return 'mdi:memory-arrow-down'
            case '%':       return 'mdi:percent'
            case 'A':       return 'mdi:lightning-bolt'
            case 'V':       return 'mdi:lightning-bolt'
            case 'W':       return 'mdi:power-plug'
            case 'kW':      return 'mdi:power-plug'
            case 'Wh':      return 'mdi:lightning'
            case 'kWh':     return 'mdi:lightning'
            case _:         return None
    
    
    def get_number_device_class(self):
        """Convert from HA unit to NumberDeviceClass"""
        if self._params.type == 'enum':
            return NumberDeviceClass.ENUM
            
        match self._attr_unit:
            case '°C':      return NumberDeviceClass.TEMPERATURE
            case '°F':      return NumberDeviceClass.TEMPERATURE
            case 'bar':     return NumberDeviceClass.PRESSURE
            case 'psi':     return NumberDeviceClass.PRESSURE
            case 'm³':      return NumberDeviceClass.WATER
            case 'L':       return NumberDeviceClass.WATER
            case 'gal':     return NumberDeviceClass.WATER
            case 'l/m':     return NumberDeviceClass.VOLUME_FLOW_RATE
            case 'gal/m':   return NumberDeviceClass.VOLUME_FLOW_RATE
            case 'mm':      return NumberDeviceClass.DISTANCE
            case 'cm':      return NumberDeviceClass.DISTANCE
            case 'in':      return NumberDeviceClass.DISTANCE
            case 's':       return NumberDeviceClass.DURATION
            case 'min':     return None
            case 'h':       return None
            case 'rpm':     return None
            case 'B':       return NumberDeviceClass.DATA_SIZE
            case 'kB':      return NumberDeviceClass.DATA_SIZE
            case 'MB':      return NumberDeviceClass.DATA_SIZE
            case 'kB/s':    return NumberDeviceClass.DATA_RATE
            case '%':       return NumberDeviceClass.POWER_FACTOR
            case 'A':       return NumberDeviceClass.CURRENT
            case 'V':       return NumberDeviceClass.VOLTAGE
            case 'W':       return NumberDeviceClass.POWER
            case 'kW':      return NumberDeviceClass.POWER
            case 'Wh':      return NumberDeviceClass.ENERGY
            case 'kWh':     return NumberDeviceClass.ENERGY
            case _:         return None
    
    
    def get_sensor_device_class(self):
        """Convert from HA unit to SensorDeviceClass"""
        if self._params.type == 'enum':
            return SensorDeviceClass.ENUM
            
        match self._attr_unit:
            case '°C':      return SensorDeviceClass.TEMPERATURE
            case '°F':      return SensorDeviceClass.TEMPERATURE
            case 'bar':     return SensorDeviceClass.PRESSURE
            case 'psi':     return SensorDeviceClass.PRESSURE
            case 'm³':      return SensorDeviceClass.WATER
            case 'L':       return SensorDeviceClass.WATER
            case 'gal':     return SensorDeviceClass.WATER
            case 'l/min':   return SensorDeviceClass.VOLUME_FLOW_RATE
            case 'gal/min': return SensorDeviceClass.VOLUME_FLOW_RATE
            case 'mm':      return SensorDeviceClass.DISTANCE
            case 'cm':      return SensorDeviceClass.DISTANCE
            case 'in':      return SensorDeviceClass.DISTANCE
            case 's':       return SensorDeviceClass.DURATION
            case 'min':     return None
            case 'h':       return None
            case 'rpm':     return None
            case 'B':       return SensorDeviceClass.DATA_SIZE
            case 'kB':      return SensorDeviceClass.DATA_SIZE
            case 'MB':      return SensorDeviceClass.DATA_SIZE
            case 'kB/s':    return SensorDeviceClass.DATA_RATE
            case '%':       return SensorDeviceClass.POWER_FACTOR
            case 'A':       return SensorDeviceClass.CURRENT
            case 'V':       return SensorDeviceClass.VOLTAGE
            case 'W':       return SensorDeviceClass.POWER
            case 'kW':      return SensorDeviceClass.POWER
            case 'Wh':      return SensorDeviceClass.ENERGY
            case 'kWh':     return SensorDeviceClass.ENERGY
            case _:         return None
    
    
    def get_sensor_state_class(self):
        # Return StateClass=None for Enum or Label
        if self._params.type != 'measure':
            return None
        
        # Return StateClass=None for params that are a setting, unlikely to change often
        if self._params.change:
            return None
        
        # Return StateClass=None for diagnostics kind of parameters
        groups_none = ['Modbus']
        if self._params.group in groups_none:
            return None
        
        # Return StateClass=None for some specific fields
        keys_none = [
            'Last_Period_Flow_Counter',
            'Last_Period_Flow_Counter_Gall',
            'Last_Period_Energy_Counter',
            'Fluid_Remain',
            'Fluid_Remain_inch',
        ]
        if self._params.key in keys_none:
            return None
            
        keys_t = []
        keys_ti = [
            'Actual_Period_Flow_Counter',
            'Actual_Period_Flow_Counter_Gall',
            'Actual_Period_Energy_Counter',
            'FCp_Partial_Delivered_Flow_Gall',
            'FCp_Partial_Delivered_Flow_mc',
            'FCt_Total_Delivered_Flow_Gall',
            'FCt_Total_Delivered_Flow_mc',
            'HO_PowerOnHours',
            'HO_PumpRunHours',
            'PartialEnergy',
            'SO_PowerOnSeconds',
            'SO_PumpRunSeconds',
            'StartNumber',
            'TotalEnergy',
            'UpTime',
            'WlanRx',
            'WlanTx',
        ]
        
        if self._params.key in keys_t:
            return SensorStateClass.TOTAL
            
        elif self._params.key in keys_ti:
            return SensorStateClass.TOTAL_INCREASING
            
        else:
            return SensorStateClass.MEASUREMENT
    
    
    def get_entity_category(self):
        
        # Return None for some specific groups we always want as sensors 
        # even if they would fail some of the tests below
        groups_none = [
            'I/O', 
        ]
        if self._params.group in groups_none:
            return None
            
        # Return None for params in groups associated with Control
        # and that a customer is allowed to change.
        # Leads to the entities being added under 'Controls'
        groups_control = [
            'Extra Comfort',
        ]
        if self._params.group in groups_control and 'C' in self._params.change:
            return None
        
        # Return CONFIG for params in groups associated with configuration
        # and that an installer is allowed to change
        # Leads to the entities being added under 'Configuration'
        # Typically intended for restart or update functionality
        groups_config = [
            'Setpoint'
        ]
        if self._params.group in groups_config and 'I' in self._params.change and self._coordinator.user_role in self._params.change:
            return EntityCategory.CONFIG

        # Return CONFIG for some specific entries associated with others that are CONFIG
        keys_config = [
            'PumpDisable',
        ]
        if self._params.key in keys_config and 'I' in self._params.change and self._coordinator.user_role in self._params.change:
            return EntityCategory.CONFIG
            
        # Return DIAGNOSTIC for params in groups associated with diagnostics
        groups_diag = [
            'Debug', 
            'Errors',
            'Extra Comfort', 
            'Firmware Updates', 
            'I/O', 
            'Installer', 
            'Modbus', 
            'ModbusDevice', 
            'PLC', 
            'System Management',
            'Technical Assistance',
            'Version',
        ]
        if self._params.group in groups_diag:
            return EntityCategory.DIAGNOSTIC
            
        # Return DIAGNOSTIC for some specific entries associated with others that are DIAGNOSTIC
        keys_diag = [
            'LastErrorOccurrency',
            'LastErrorTimePowerOn',
        ]
        if self._params.key in keys_diag:
            return EntityCategory.DIAGNOSTIC
        
        # Return DIAGNOSTIC for params that are a setting, unlikely to change often
        if self._params.change:
            return EntityCategory.DIAGNOSTIC
            
        # Return DIAGNOSTIC for params that are not visible for Customer or Installer (i.e. only visible for Service or R&D)
        if 'C' not in self._params.view and 'I' not in self._params.view:
            return EntityCategory.DIAGNOSTIC
        
        if 'C' not in self._params.view and self._params.family == 'gear':
            return EntityCategory.DIAGNOSTIC
        
        # Return None for all others
        return None
    
    
    def get_number_step(self):
        match self._attr_unit:
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
    

