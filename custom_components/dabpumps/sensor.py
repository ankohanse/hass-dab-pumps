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

from datetime import timedelta
from datetime import datetime

from collections import defaultdict
from collections import namedtuple


from .const import (
    DOMAIN,
    COORDINATOR,
    CONF_INSTALL_ID,
    CONF_INSTALL_NAME,
    CONF_OPTIONS,
)

from .helper import (
    DabPumpsHelperFactory,
    DabPumpsHelper
)


_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """
    Setting up the adding and updating of sensor entities
    """
    helper = DabPumpsHelperFactory.create(hass, config_entry)
    await helper.async_setup_entry(Platform.SENSOR, create_entity, async_add_entities)


def create_entity(coordinator, install_id, object_id, device, params, status):
    """
    Create a new DabPumpsSensor instance
    """
    return DabPumpsSensor(coordinator, install_id, object_id, device, params, status)


class DabPumpsSensor(CoordinatorEntity, SensorEntity):
    """
    Representation of a DAB Pumps Sensor.
    
    Could be a sensor that is part of a pump like ESybox, Esybox.mini
    Or could be part of a communication module like DConnect Box/Box2
    """
    
    def __init__(self, coordinator, install_id, object_id, device, params, status) -> None:
        """ Initialize the sensor. """
        super().__init__(coordinator)
        
        # The unique identifier for this sensor within Home Assistant
        self.object_id = object_id
        self.entity_id = ENTITY_ID_FORMAT.format(status.unique_id)
        self.install_id = install_id
        
        self._coordinator = coordinator
        self._device = device
        
        # Create all attributes
        self._update_attributes(device, params, status, True)
    
    
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
        super()._handle_coordinator_update()
        
        (device_map, config_map, status_map) = self._coordinator.data
        
        # find the correct device and status corresponding to this sensor
        device = device_map.get(self._device.serial)
        config = config_map.get(self._device.config_id) or {}
        status = status_map.get(self.object_id)
        params = config.meta_params.get(status.key) or {}

        # Update any attributes
        if device and params and status:
            if self._update_attributes(device, params, status, False):
                self.async_write_ha_state()
    
    
    def _update_attributes(self, device, params, status, is_create):
        
        # Transform values according to the metadata params for this status/sensor
        match params.type:
            case 'measure':
                if params.weight and params.weight != 1 and params.weight != 0:
                    # Convert to float
                    attr_precision = int(math.floor(math.log10(1.0 / params.weight)))
                    attr_unit = self._convert_to_unit(params)
                    attr_val = round(float(status.val) * params.weight, attr_precision)
                else:
                    # Convert to int
                    attr_precision = 0
                    attr_unit = self._convert_to_unit(params)
                    attr_val = int(status.val)
                    
            case 'enum':
                # Lookup the dict string for the value and otherwise return the value itself
                attr_precision = None
                attr_unit = None
                attr_val = self._get_string(params.values.get(status.val, status.val))

            case 'label' | _:
                if params.type != 'label':
                    _LOGGER.warn(f"DAB Pumps encountered an unknown sensor type '{params.type}' for '{params.key}'. Please contact the integration developer to have this resolved.")
                    
                # Convert to string
                attr_precision = None
                attr_unit = self._convert_to_unit(params) or None
                attr_val = self._get_string(str(status.val))
        
        # Process any changes
        changed = False
        
        # update creation-time only attributes
        if is_create:
            _LOGGER.debug(f"Create sensor '{status.key}' ({status.unique_id})")
            
            self._attr_unique_id = status.unique_id
            
            self._attr_has_entity_name = True
            self._attr_name = self._get_string(status.key)
            self._name = status.key
            
            self._attr_state_class = self._get_state_class(params)
            self._attr_entity_category = self._get_entity_category(params)
            self._attr_device_class = self._get_device_class(params, attr_unit) 
            changed = True
        
        # update value if it has changed
        if is_create or self._attr_native_value != attr_val:
            self._attr_native_value = attr_val
            self._attr_native_unit_of_measurement = attr_unit
            self._attr_suggested_display_precision = attr_precision
            
            self._attr_icon = self._get_icon(params, attr_unit)
            changed = True
            
        # update device info if it has changed
        if is_create \
        or (self._device.name != device.name) \
        or (self._device.vendor != device.vendor) \
        or (self._device.serial != device.serial) \
        or (self._device.product != device.product) \
        or (self._device.version != device.version):
                   
            self._device = device
            self._attr_device_info = DeviceInfo(
               identifiers = {(DOMAIN, self._device.serial)},
               name = self._device.name,
               manufacturer =  self._device.vendor,
               model = self._device.product,
               serial_number = self._device.serial,
               sw_version = self._device.version,
            )
            changed = True
        
        return changed
    
    
    def _get_string(self, str):
        # return 'translated' string or original string if not found
        return self._coordinator.string_map.get(str, str)

    
    def _convert_to_unit(self, params):
        """Convert from DAB Pumps units to Home Assistant units"""
        match params.unit:
            case '°C':          return '°C' 
            case '°F':          return '°F'
            case 'bar':         return 'bar'
            case 'psi':         return 'psi'
            case 'mc':          return 'm³'
            case 'l':           return 'L'
            case 'l/min':       return 'L/m'
            case 'gall':        return 'gal'
            case 'gall/min':    return 'gal/m'
            case 'gpm':         return 'gal/m'
            case 'cm':          return 'cm'
            case 'inch':        return 'in'
            case 'ms':          return 'ms'
            case 's':           return 's'
            case 'secondi':     return 's'
            case 'h':           return 'h'
            case 'rpm':         return 'rpm'
            case 'B':           return 'B'
            case 'kB':          return 'kB'
            case 'KB':          return 'kB'
            case 'MByte':       return 'MB'
            case '%':           return '%'
            case 'V':           return 'V'
            case 'A':           return 'A'
            case 'kW':          return 'kW'
            case 'kWh':         return 'kWh'
            case 'Address':     return None
            case 'SW. Vers.':   return None
            case '':            return None
            case 'None' | None: return None
            
            case _:
                _LOGGER.warn(f"DAB Pumps encountered a unit or measurement '{params.unit}' for '{params.key}' that may not be supported by Home Assistant. Please contact the integration developer to have this resolved.")
                return params.unit
        
        
    
    def _get_device_class(self, params, attr_unit):
        """Convert from HA unit to SensorDeviceClass"""
        if params.type == 'enum':
            return SensorDeviceClass.ENUM
            
        match attr_unit:
            case '°C':      return SensorDeviceClass.TEMPERATURE
            case '°F':      return SensorDeviceClass.TEMPERATURE
            case 'bar':     return SensorDeviceClass.PRESSURE
            case 'psi':     return SensorDeviceClass.PRESSURE
            case 'm³':      return SensorDeviceClass.WATER
            case 'L':       return SensorDeviceClass.WATER
            case 'gal':     return SensorDeviceClass.WATER
            case 'l/m':     return None
            case 'gal/m':   return None
            case 'mm':      return SensorDeviceClass.DISTANCE
            case 'cm':      return SensorDeviceClass.DISTANCE
            case 'in':      return SensorDeviceClass.DISTANCE
            case 's':       return SensorDeviceClass.DURATION
            case 'h':       return None
            case 'rpm':     return None
            case 'B':       return SensorDeviceClass.DATA_SIZE
            case 'kB':      return SensorDeviceClass.DATA_SIZE
            case 'MB':      return SensorDeviceClass.DATA_SIZE
            case 'kB/s':    return SensorDeviceClass.DATA_RATE
            case '%':       return SensorDeviceClass.POWER_FACTOR
            case 'A ':      return SensorDeviceClass.CURRENT
            case 'V ':      return SensorDeviceClass.VOLTAGE
            case 'W ':      return SensorDeviceClass.POWER
            case 'Wh':      return SensorDeviceClass.ENERGY
            case 'kWh':     return SensorDeviceClass.ENERGY
            case _:         return None
    
    
    def _get_icon(self, params, attr_unit):
        """Convert from HA unit to icon"""
        match attr_unit:
            case '°C':      return 'mdi:thermometer'
            case '°F':      return 'mdi:thermometer'
            case 'bar':     return 'mdi:water-pump'
            case 'psi':     return 'mdi:water-pump'
            case 'm³':      return 'mdi:water'
            case 'L':       return 'mdi:water'
            case 'gal':     return 'mdi:water'
            case 'L/m':     return 'mdi:hydro-power'
            case 'gal/m':   return 'mdi:hydro-power'
            case 'mm':      return 'mdi:waves-arrow-up'
            case 'cm':      return 'mdi:waves-arrow-up'
            case 'in':      return 'mdi:waves-arrow-up'
            case 's':       return 'mdi:timer-sand'
            case 'h':       return 'mdi:timer'
            case 'B':       return 'mdi:memory'
            case 'kB':      return 'mdi:memory'
            case 'MB':      return 'mdi:memory'
            case 'kB/s':    return 'mdi:memory-arrow-down'
            case '%':       return 'mdi:percent'
            case 'A':       return 'mdi:lightning-bolt'
            case 'V':       return 'mdi:lightning-bolt'
            case 'W':       return 'mdi:power-plug'
            case 'Wh':      return 'mdi:lightning'
            case 'kWh':     return 'mdi:lightning'
            case _:         return None
    
    
    def _get_state_class(self, params):
        # Return StateClass=None for Enum or Label
        if params.type != 'measure':
            return None
            
        # Return StateClass=None for params that are a setting, unlikely to change often
        if params.change:
            return None
            
        # Return StateClass=None for diagnostics kind of parameters
        groups_none = ['Modbus', 'Extra Comfort']
        if params.group in groups_none:
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
            'SO_PowerOnSeconds',
            'SO_PumpRunSeconds',
            'StartNumber',
            'UpTime',
            'WlanRx',
            'WlanTx',
        ]
        
        if params.key in keys_t:
            return SensorStateClass.TOTAL
            
        elif params.key in keys_ti:
            return SensorStateClass.TOTAL_INCREASING
            
        else:
            return SensorStateClass.MEASUREMENT
    
    
    def _get_entity_category(self, params):
        
        # Return None for some specific groups we always want as sensors 
        # even if they would fail some of the tests below
        groups_none = [
            'I/O', 
        ]
        if params.group in groups_none:
            return None
            
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
            'Version'
        ]
        if params.group in groups_diag:
            return EntityCategory.DIAGNOSTIC
            
        # Return DIAGNOSTIC for some specific entries associated with others that are DIAGNOSTIC
        keys_diag = [
            'LastErrorOccurrency',
            'LastErrorTimePowerOn',
        ]
        if params.key in keys_diag:
            return EntityCategory.DIAGNOSTIC
        
        # Return DIAGNOSTIC for params that are a setting, unlikely to change often
        # Note: we do not yet support EntityCategory.CONFIG
        if params.change:
            return EntityCategory.DIAGNOSTIC
            
        # Return DIAGNOSTIC for params that are not visible for Customer or Installer (i.e. only visible for Service or R&D)
        if 'C' not in params.view and 'I' not in params.view:
            return EntityCategory.DIAGNOSTIC
        
        if 'C' not in params.view and params.family == 'gear':
            return EntityCategory.DIAGNOSTIC

        # Return None for all others
        return None
