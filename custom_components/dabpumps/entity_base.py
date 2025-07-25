import logging
import async_timeout

from datetime import timedelta
from typing import Any

from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.sensor import SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.const import Platform
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
from homeassistant.core import callback
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

import homeassistant.helpers.entity_registry as entity_registry


from .const import (
    DOMAIN,
    PLATFORMS,
    NAME,
    CONF_INSTALL_ID,
    CONF_INSTALL_NAME,
    CONF_OPTIONS,
    BINARY_SENSOR_VALUES_ALL,
    SWITCH_VALUES_ALL,
    BUTTON_VALUES_ALL,
)

from .coordinator import (
    DabPumpsCoordinatorFactory,
    DabPumpsCoordinator
)


_LOGGER = logging.getLogger(__name__)


class DabPumpsEntityHelperFactory:
    
    @staticmethod
    def create(hass: HomeAssistant, config_entry: ConfigEntry):
        """
        Get entity helper for a config entry.
        The entry is short lived (only during init) and does not contain state data,
        therefore no need to cache it in hass.data
        """
    
        # Get an instance of the DabPumpsCoordinator for this install_id
        coordinator = DabPumpsCoordinatorFactory.create(hass, config_entry)
    
        # Get an instance of our helper
        return DabPumpsEntityHelper(hass, coordinator)


class DabPumpsEntityHelper:
    """My custom helper to provide common functions."""
    
    def __init__(self, hass: HomeAssistant, coordinator: DabPumpsCoordinator):
        self._coordinator = coordinator
        self._entity_registry = entity_registry.async_get(hass)
        
    
    async def async_setup_entry(self, target_platform, target_class, async_add_entities: AddEntitiesCallback):
        """
        Setting up the adding and updating of sensor and binary_sensor entities
        """    
        # Get data from the coordinator
        (device_map, config_map, status_map) = self._coordinator.data
        
        if not device_map or not config_map or not status_map:
            # If data returns False or is empty, log an error and return
            _LOGGER.warning(f"Failed to fetch sensor data - authentication failed or no data.")
            return
        
        other_platforms = [p for p in PLATFORMS if p != target_platform]
        
        _LOGGER.debug(f"Create {target_platform} entities for installation '{self._coordinator.install_name}'")

        # Iterate all statusses to create sensor entities
        entities = []
        valid_unique_ids: list[str] = []

        for object_id, status in status_map.items():

            # skip statusses that are not associated with a device in this installation
            device = device_map.get(status.serial, None)
            if not device or device.install_id != self._coordinator.install_id:
                continue
            
            config = config_map.get(device.config_id, None)
            if not config:
                continue
            
            params = config.meta_params.get(status.key, None) if config.meta_params else None
            if not params:
                continue

            if not self._is_entity_whitelisted(params):
                # Some statusses (error1...error64) are deliberately skipped
                continue
            
            platform = self._get_entity_platform(params)
            if platform != target_platform:
                # This status will be handled via another platform
                continue
                
            # Create a Sensor, Binary_Sensor, Number, Select, Switch or other entity for this status
            entity = None                
            try:
                entity = target_class(self._coordinator, object_id, device, params, status)
                entities.append(entity)
                
                valid_unique_ids.append(entity.unique_id)

            except Exception as  ex:
                _LOGGER.warning(f"Could not instantiate {platform} entity class for {object_id}. Details: {ex}")

        # Remember valid unique_ids per platform so we can do an entity cleanup later
        self._coordinator.set_valid_unique_ids(target_platform, valid_unique_ids)

        # Now add the entities to the entity_registry
        _LOGGER.info(f"Add {len(entities)} {target_platform} entities for installation '{self._coordinator.install_name}'")
        if entities:
            async_add_entities(entities)
    
    
    def _is_entity_whitelisted(self, params):
        """
        Determine whether an entry is whitelisted and should be added as sensor/binary sensor/number/select/switch
        Or is blacklistred and should be ignored
        """
        
        # Whitelisted keys that would otherwise be excluded by blacklisted groups below:
        keys_whitelist = [
            'RamUsed',                  # group: Debug
            'RamUsedMax',               # group: Debug
            'LatestError',              # group: Errors
            'RF_EraseHistoricalFault',  # group: Errors
        ]

        # Blacklisted keys that would otherwise be included by whitelisted groups below:
        keys_blacklist = [
            'IdentifyDevice',           # group: System Management
            'Identify',                 # group: Advanced
            'Reboot',                   # group: Advanced
            'UpdateSystem',             # group: Advanced
            'UpdateFirmware',           # group: Firmware Updates
            'UpdateProgress',           # group: Firmware Updates
            'PW_ModifyPassword',        # group: Technical Assistance
        ]
        
        groups_whitelist = []
        groups_blacklist = [
            'Debug',
            'ModbusDevice',
            'Errors'
        ]

        # First check if entity is allowed to be viewed according to user_role
        if self._coordinator.user_role not in params.view:
            return False
        
        # Then check individual keys
        if params.key in keys_whitelist:
            return True
        
        if params.key in keys_blacklist:
            return False
        
        # Then check groups
        if params.group in groups_whitelist:
            return True

        if params.group in groups_blacklist:
            return False
        
        # If not blacklisted by any rule above, then it is whitelisted
        return True
        
        
    def _get_entity_platform(self, params):
        """
        Determine what platform an entry should be added into
        """
        
        # Is it a button/switch/select/number config or control entity? 
        # Needs to have change rights for the user role
        # And needs to be in group 'Extra Comfort' or be a specific key
        # that would otherwise be excluded as group
        keys_config = [
            'PumpDisable',
            'RF_EraseHistoricalFault',
        ]
        groups_config = [
            'Extra Comfort',
            'Setpoint',
            'System Management',
        ]
        is_config = False
        if self._coordinator.user_role in params.change:
            if params.key in keys_config:
                is_config = True
            elif params.group in groups_config:
                is_config = True
        
        if is_config:
            if params.type == 'enum':
                # With exactly 1 possible value that are of 'press' type it becomes a button
                if len(params.values or []) == 1:
                    if all(k in BUTTON_VALUES_ALL for k,v in params.values.items()):
                        return Platform.BUTTON

                # With exactly 2 possible values that are of ON/OFF type it becomes a switch
                if len(params.values or []) == 2:
                    if all(k in SWITCH_VALUES_ALL and v in SWITCH_VALUES_ALL for k,v in params.values.items()):
                        return Platform.SWITCH
                    
                # With more values or not of ON/OFF type it becomes a Select
                return Platform.SELECT
                
            # Is it a numeric type?
            elif params.type == 'measure':
                if params.unit == 's':
                    return Platform.TIME
                else:
                    return Platform.NUMBER
        
        # Only view rights or does not fit in one of the modifyable entities
        if params.type == 'enum':
            # Suppress buttons if we only have view rights
            if len(params.values or []) == 1:
                if all(k in BUTTON_VALUES_ALL for k,v in params.values.items()):
                    return None
    
            # Is it a binary sensor?
            if len(params.values or []) == 2:
                if all(k in BINARY_SENSOR_VALUES_ALL and v in BINARY_SENSOR_VALUES_ALL for k,v in params.values.items()):
                    return Platform.BINARY_SENSOR
    
        # Everything else will become a regular sensor
        return Platform.SENSOR
    

class DabPumpsEntity(Entity):
    """
    Common funcionality for all DabPumps Entities:
    (DabPumpsSensor, DabPumpsBinarySensor, DabPumpsNumber, DabPumpsSelect, DabPumpsSwitch)
    """
    
    def __init__(self, coordinator, params):
        self._coordinator = coordinator
        self._params = params
        self._attr_unit = self._convert_to_unit()


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
        if self._params.group in groups_config and 'I' in self._params.change:
            return EntityCategory.CONFIG

        # Return CONFIG for some specific entries associated with others that are CONFIG
        keys_config = [
            'PumpDisable',
        ]
        if self._params.key in keys_config and 'I' in self._params.change:
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

