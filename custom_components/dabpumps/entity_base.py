import logging

from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.sensor import SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant


from .const import (
    DOMAIN,
    PLATFORMS,
    NAME,
    HELPER,
    CONF_INSTALL_ID,
    CONF_INSTALL_NAME,
    CONF_OPTIONS,
    BINARY_SENSOR_VALUES_ON,
    BINARY_SENSOR_VALUES_OFF,
    BINARY_SENSOR_VALUES_ALL,
    SWITCH_VALUES_ON,
    SWITCH_VALUES_OFF,
    SWITCH_VALUES_ALL,
)


_LOGGER = logging.getLogger(__name__)


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
from homeassistant.core import callback
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

import homeassistant.helpers.entity_registry as entity_registry


from .const import (
    DOMAIN,
    NAME,
    HELPER,
    CONF_INSTALL_ID,
    CONF_INSTALL_NAME,
    CONF_OPTIONS,
    BINARY_SENSOR_VALUES_ON,
    BINARY_SENSOR_VALUES_OFF,
    BINARY_SENSOR_VALUES_ALL,
    SWITCH_VALUES_ON,
    SWITCH_VALUES_OFF,
    SWITCH_VALUES_ALL,
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
        Get existing helper for a config entry, or create a new one if it does not yet exist
        """
    
        # Get properties from the config_entry
        install_id = config_entry.data[CONF_INSTALL_ID]
        install_name = config_entry.data[CONF_INSTALL_NAME]
        options = config_entry.options

        if not HELPER in hass.data[DOMAIN]:
            hass.data[DOMAIN][HELPER] = {}
            
        # already created?
        helper = hass.data[DOMAIN][HELPER].get(install_id, None)
        if not helper:
            # Get an instance of our helper. This is unique to this install_id
            helper = DabPumpsEntityHelper(hass, config_entry, install_id, install_name, options)
            hass.data[DOMAIN][HELPER][install_id] = helper
            
        return helper


class DabPumpsEntityHelper:
    """My custom helper to provide common functions."""
    
    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, install_id, install_name, options):
        self.install_id = install_id
        self.install_name = install_name
        self.options = options

        # Get an instance of the DabPumpsCoordinator for this install_id
        self.coordinator = DabPumpsCoordinatorFactory.create(hass, config_entry)

        # Get entity registry
        self.entity_registry = entity_registry.async_get(hass)
        
    
    async def async_setup_entry(self, target_platform, target_class, async_add_entities: AddEntitiesCallback):
        """
        Setting up the adding and updating of sensor and binary_sensor entities
        """    
        # Get data from the coordinator
        (device_map, config_map, status_map) = self.coordinator.data
        
        if not device_map or not config_map or not status_map:
            # If data returns False or is empty, log an error and return
            _LOGGER.warning(f"Failed to fetch sensor data - authentication failed or no data.")
            return
        
        other_platforms = [p for p in PLATFORMS if p != target_platform]
        
        _LOGGER.debug(f"Create entities for installation '{self.install_name}' ({self.install_id})")

        # Iterate all statusses to create sensor entities
        entities = []
        for object_id, status in status_map.items():
            
            # skip statusses that are not associated with a device in this installation
            device = device_map.get(status.serial, None)
            if not device or device.install_id != self.install_id:
                continue
            
            config = config_map.get(device.config_id, None)
            if not config:
                continue
            
            if not config.meta_params or status.key not in config.meta_params:
                _LOGGER.warning(f"Device metadata holds no info to create a sensor for '{status.key}' with value '{status.val}'.")
                continue
            
            params = config.meta_params[status.key]
            
            if not self._is_entity_whitelisted(params):
                # Some statusses (error1...error64) are deliberately skipped
                continue
            
            platform = self._get_entity_platform(params)
            
            if platform != target_platform:
                # This status will be handled via another platform
                continue
                
            # Create a Sensor, Binary_Sensor, Number, Select, Switch or other entity for this status
            unique_id = DabPumpsCoordinator.create_id(device.name, status.key)
            entity = None                
            try:
                entity = target_class(self.coordinator, self.install_id, object_id, unique_id, device, params, status)
                entities.append(entity)
            except Exception as  ex:
                _LOGGER.warning(f"Could not instantiate {platform} entity class for {object_id}. Details: {ex}")

            # See if new entity already existed under another platform. If so, then remove the old entity.
            if entity:
                for p in other_platforms:
                    try:
                        entity_id = self.entity_registry.async_get_entity_id(p, DOMAIN, entity.unique_id)
                        if entity_id:
                            _LOGGER.info(f"Remove obsolete {entity_id} that is replaced by {platform}.{entity.unique_id}")
                            self.entity_registry.async_remove(entity_id)
                    except Exception as  ex:
                        _LOGGER.warning(f"Could not remove obsolete {p}.{entity.unique_id} entity. Details: {ex}")

        _LOGGER.info(f"Add {len(entities)} {target_platform} entities for installation '{self.install_name}' with {len(device_map)} devices")
        if entities:
            async_add_entities(entities)
    
    
    def _is_entity_whitelisted(self, params):
        """
        Determine whether an entry is whitelisted and should be added as sensor/binary sensor/number/select/switch
        Or is blacklistred and should be ignored
        """
        
        # Whitelisted keys that would otherwise be excluded by blacklisted groups below:
        keys_whitelist = [
            'RamUsed',      # group: Debug
            'RamUsedMax',   # group: Debug
            'PumpDisable',  # group: System Management
            'LatestError'   # group: Errors
        ]

        # Blacklisted keys that would otherwise be included by whitelisted groups below:
        keys_blacklist = []
        
        groups_whitelist = []
        groups_blacklist = [
            'Debug',
            'System Management',
            'ModbusDevice',
            'Errors'
        ]

        # First check if entity is allowed to be viewed according to user_role
        if self.coordinator.user_role not in params.view:
            return False
        
        # Then check individual keys
        if params.key in keys_whitelist:
            return True
        
        if params.key in keys_blacklist:
            #_LOGGER.debug(f"Skip create sensor for '{params.key}'; it is blacklisted'.")
            return False
        
        # Then check groups
        if params.group in groups_whitelist:
            return True

        if params.group in groups_blacklist:
            #_LOGGER.debug(f"Skip create sensor for '{params.key}'; its group '{params.group}' is blacklisted'.")
            return False
        
        # If not blacklisted by any rule above, then it is whitelisted
        return True
        
        
    def _get_entity_platform(self, params):
        """
        Determine what platform an entry should be added into
        """
        
        # Is it a switch/select/number config or control entity? 
        # Needs to have change rights the user role
        # And needs to be in group 'Extra Comfort' or be a specific key
        # that would otherwise be excluded as group
        keys_config = [
            'PumpDisable',
        ]
        groups_config = [
            'Extra Comfort',
            'Setpoint',
        ]
        is_config = False
        if self.coordinator.user_role in params.change:
            if params.key in keys_config:
                is_config = True
            elif params.group in groups_config:
                is_config = True
        
        if is_config:
            if params.type == 'enum':
                # With exactly 2 possible values that are of ON/OFF type it becomes a switch
                if len(params.values or []) == 2:
                    if all(k in SWITCH_VALUES_ALL and v in SWITCH_VALUES_ALL for k,v in params.values.items()):
                        return Platform.SWITCH
                    
                # With more values or not of ON/OFF type it becomes a Select
                return Platform.SELECT
                
            # Is it a numeric type?
            elif params.type == 'measure':
                return Platform.NUMBER
        
        # Is it a binary sensor?
        if params.type == 'enum' and len(params.values or []) == 2:
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


    def _get_string(self, str):
        # return 'translated' string or original string if not found
        return self._coordinator.string_map.get(str, str)


    def _convert_to_unit(self):
        """Convert from DAB Pumps units to Home Assistant units"""
        match self._params.unit:
            case '°C':          return '°C' 
            case '°F':          return '°F'
            case 'bar':         return 'bar'
            case 'psi':         return 'psi'
            case 'mc':          return 'm³'
            case 'l':           return 'L'
            case 'l/min':       return 'L/min'
            case 'gall':        return 'gal'
            case 'gall/min':    return 'gal/min'
            case 'gpm':         return 'gal/min'
            case 'cm':          return 'cm'
            case 'inch':        return 'in'
            case 'ms':          return 'ms'
            case 's':           return 's'
            case 'secondi':     return 's'
            case 'min':         return 'min'
            case 'h':           return 'h'
            case 'rpm':         return 'rpm'
            case 'B':           return 'B'
            case 'kB':          return 'kB'
            case 'KB':          return 'kB'
            case 'MByte':       return 'MB'
            case '%':           return '%'
            case 'V':           return 'V'
            case 'A':           return 'A'
            case 'W':           return 'W'
            case 'kW':          return 'kW'
            case 'kWh':         return 'kWh'
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
        groups_none = ['Modbus', 'Extra Comfort']
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
            'System Management',
            'Setpoint'
        ]
        if self._params.group in groups_config and 'I' in self._params.change:
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

