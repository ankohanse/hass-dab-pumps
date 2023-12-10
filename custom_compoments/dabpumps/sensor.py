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
)

from .dabpumpsapi import DabPumpsApi


_LOGGER = logging.getLogger(__name__)


# Setting up the adding and updating of sensor entities
async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback):

    # Retrieve the devices and their data via the coordinator
    coordinator: DabPumpsCoordinator = hass.data[DOMAIN][COORDINATOR]
    (device_map, status_map) = coordinator.data

    if not device_map or not status_map:
        # If data returns False or is empty, log an error and return
        _LOGGER.warning(f"DAB Pumps: Failed to fetch sensor data - authentication failed or no data.")
        return
    
    # Iterate all statusses to create sensor entities
    sensors = []
    for object_id, status in status_map.items():

        # only process statusses that we know can be transformed into a sensor
        if (status.key not in SENSOR_FIELDS.keys()):
            continue
        
        device = device_map.get(status.serial, None)
        if not device:
            continue
            
        # Instantiate a DabPumpsSensor
        sensor = DabPumpsSensor(coordinator, device, status, object_id)
        sensors.append(sensor)
    
    _LOGGER.info(f"Setup integration with {len(device_map)} devices and {len(sensors)} sensors")
    
    if sensors:
        async_add_entities(sensors)


#This is the actual instance of SensorEntity class
class DabPumpsSensor(CoordinatorEntity, SensorEntity):
    """Representation of a DAB Pumps Sensor."""
    
    def __init__(self, coordinator, device, status, object_id) -> None:
        """ Initialize the sensor. """
        super().__init__(coordinator)
        
        # The unique identifier for this sensor within Home Assistant
        self.object_id = object_id
        self.entity_id = ENTITY_ID_FORMAT.format(status.unique_id)
        
        self._coordinator = coordinator
        self._device = device
        self._status = status
        
        # Create all attributes
        self._update_attributes(device, status, True)
    

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
        
        (device_map, status_map) = self._coordinator.data
        
        # find the correct device and status corresponding to this sensor
        device = device_map.get(self._device.serial)
        status = status_map.get(self.object_id)
        
        # Update any attributes
        if device and status:
            if self._update_attributes(device, status, False):
                self.async_write_ha_state()

    
    def _update_attributes(self, device, status, is_create):
        
        # Lookup the definition for this status/sensor
        field = SENSOR_FIELDS.get(status.key, None)
        if not field:
            return False
        
        # Transform values according to the definition
        match (field.type):
            case 'float': 
                field_precision = int(math.floor(math.log10(field.scale)))
                field_val = round(float(status.val) / field.scale, field_precision)
            case 'int':    
                field_precision = 0
                field_val = int(round(float(status.val) / field.scale, 0))
            case 'enum':
                field_precision = None
                field_val = self._get_enum_value(field, status.key, status.val)
            case 'string' | _: 
                field_precision = None
                field_val = str(status.val)
                
        # Process any changes
        changed = False
        
        # update creation-time only attributes
        if is_create:
            self._attr_unique_id = status.unique_id
            
            self._attr_has_entity_name = True
            self._attr_name = field.friendly
            self._name = status.key
            
            self._attr_state_class = self._get_state_class(field)
            self._attr_device_class = self._get_device_class(field) 
            self._attr_entity_category = self._get_entity_category(field)
            changed = True
        
        # update value if it has changed
        if is_create or self._attr_native_value != field_val:
            self._attr_native_value = field_val
            self._attr_native_unit_of_measurement = field.unit
            self._attr_suggested_display_precision = field_precision

            self._attr_icon = self._get_icon(field, field_val)
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
    
    
    def _get_device_class(self, field):
        if field.type == 'enum':
            return SensorDeviceClass.ENUM
            
        match field.unit:
            case '°C':      return SensorDeviceClass.TEMPERATURE
            case '°F':      return SensorDeviceClass.TEMPERATURE
            case 'bar':     return SensorDeviceClass.PRESSURE
            case 'psi':     return SensorDeviceClass.PRESSURE
            case 'm³':      return SensorDeviceClass.WATER
            case 'L':       return SensorDeviceClass.WATER
            case 'gal':     return SensorDeviceClass.WATER
            case 'l/m':     return None
            case 'gal/m':   return None
            case 's':       return SensorDeviceClass.DURATION
            case 'h':       return None
            case 'B':       return SensorDeviceClass.DATA_SIZE
            case 'kB':      return SensorDeviceClass.DATA_SIZE
            case 'MB':      return SensorDeviceClass.DATA_SIZE
            case 'kB/s':    return SensorDeviceClass.DATA_RATE
            case '%':       return SensorDeviceClass.POWER_FACTOR
            case 'A ':      return SensorDeviceClass.CURRENT
            case 'W ':      return SensorDeviceClass.POWER
            case 'Wh':      return SensorDeviceClass.ENERGY
            case _:         return None
    
    
    def _get_icon(self, field, field_val):
        match field.unit:
            case '°C':      return 'mdi:thermometer'
            case '°F':      return 'mdi:thermometer'
            case 'bar':     return 'mdi:water-pump'
            case 'psi':     return 'mdi:water-pump'
            case 'm³':      return 'mdi:water'
            case 'L':       return 'mdi:water'
            case 'gal':     return 'mdi:water'
            case 'L/m':     return 'mdi:hydro-power'
            case 'gal/m':   return 'mdi:hydro-power'
            case 's':       return 'mdi:timer-sand'
            case 'h':       return 'mdi:timer'
            case 'B':       return 'mdi:memory'
            case 'kB':      return 'mdi:memory'
            case 'MB':      return 'mdi:memory'
            case 'kB/s':    return 'mdi:memory-arrow-down'
            case '%':       return 'mdi:percent'
            case 'A':       return 'mdi:lightning-bolt'
            case 'W':       return 'mdi:power-plug'
            case 'Wh':      return 'mdi:lightning'
            case _:         return None
    
    
    def _get_state_class(self, field):
        match field.sc:
            case 'm':       return SensorStateClass.MEASUREMENT
            case 't':       return SensorStateClass.TOTAL
            case 'ti':      return SensorStateClass.TOTAL_INCREASING
            case _:         return None
    
    
    def _get_entity_category(self, field):
        match field.ec:
            case 'd':       return EntityCategory.DIAGNOSTIC
            case 'c':       return EntityCategory.CONFIG
            case _:         return None
            
    
    def _get_enum_value(self, field, status_key, status_val):
        match status_key:
            case 'AD_AddressConfig': 
                dict = {
                    '1': 'Automatic',
                    '2': '1',
                    '3': '2',
                    '4': '3',
                    '5': '4',
                }
            
            case 'AE_AntiLock' | 'AF AntiFreeze' | 'AY_AntiCycling': 
                dict = {
                    '0': 'Enabled',
                    '1': 'Disabled',
                }
            
            case 'FirmwareStatus': 
                dict = {
                    '0': 'Update available',
                    '1': 'Already updated',
                }
            
            case 'LA_Language': 
                dict = {
                    '0': 'Italian',
                    '1': 'English',
                    '2': 'French',
                    '3': 'German',
                    '4': 'Spanish',
                    '5': 'Dutch',
                    '6': 'Swedish',
                    '7': 'Turkish',
                    '8': 'Slovenian',
                    '9': 'Romanian',
                    '10': 'Chech',
                    '11': 'Polish',
                    '12': 'Russian',
                    '13': 'Thai',
                }
            
            case 'MS_MeasureSystem': 
                dict = {
                    '0': 'International',
                    '1': 'Imperial',
                }
            
            case 'OD_PlantType': 
                dict = {
                    '0': 'Elastic',
                    '1': 'Rigid',
                }
            
            case 'PumpStatus': 
                dict = {
                    '0': 'StandBy',
                    '1': 'Go',
                }
            
            case 'SystemStatus':
                dict = {
                    '0': 'Ok'
                }
            
            case 'In1' | 'Out1': 
                dict = {
                    '0': 'Inactive',
                    '1': 'Active',
                }
            
            case 'ModbusBaudRate':
                dict = {
                    '0': '1200 bit/s',
                    '1': '2400 bit/s',
                    '2': '4800 bit/s',
                    '3': '9600 bit/s',
                    '4': '19200 bit/s',
                    '5': '38400 bit/s',
                }
                
            case 'ModbusParity':
                dict = {
                    '0': 'No parity',
                    '1': 'Even parity',
                    '2': 'Odd parity',
                }
                
            case 'ModbusStopBit':
                dict = {
                    '1': '1 bit',
                    '2': '2 bit',
                }
            
            case 'PLCStatus':
                dict = {
                    '0': 'Associated',
                    '1': 'Not associated',
                }
                
            case 'PLCUpdatingStatus':
                dict = {
                    '0': 'Not updating',
                    '1': 'Updating',
                }
                
            case 'SampleRate':
                dict = {
                    '0': '20 seconds',
                    '1': '5 seconds',
                }
            
            case 'WifiMode':
                dict = {
                    '0': 'Operative',
                    '1': 'Disconnected',
                }

            case _: dict = {}

        # lookup the dict string for the value and otherwise return the value itself
        return dict.get(status_val, default=status_val)


SF = namedtuple('SF', 'friendly, type, scale, unit, sc, ec')
SENSOR_FIELDS = {
    #
    # Esybox
    #    
    'AD_AddressConfig':                SF(friendly='Address config (AD)',                  type='enum',   scale=1,    unit= None,   sc=None, ec='d'  ),
    'AE_AntiLock':                     SF(friendly='Anti lock (AE)',                       type='enum',   scale=1,    unit= None,   sc=None, ec='d'  ),
    'AF_AntiFreeze':                   SF(friendly='Anti freeze (AF)',                     type='enum',   scale=1,    unit= None,   sc=None, ec='d'  ),
    'AY_AntiCycling':                  SF(friendly='Anti cycling (AY)',                    type='enum',   scale=1,    unit= None,   sc=None, ec='d'  ),
    'ActiveInverterNumber':            SF(friendly='Active inverter number',               type='int',    scale=1,    unit= None,   sc=None, ec=None ),
    'C1_PumpPhaseCurrent':             SF(friendly='Pump phase current (C1)',              type='float',  scale=10,   unit='A',     sc='m',  ec=None ),
    'ContemporaryInverterNumber':      SF(friendly='Contemporary Inverter number',         type='int',    scale=1,    unit= None,   sc=None, ec=None ),
    'EK_LowPressureEnable':            SF(friendly='Low pressure enable (EK)',             type='int',    scale=1,    unit= None,   sc=None, ec='d'  ),
    'ET_ExchangeTime':                 SF(friendly='Exchange time (ET)',                   type='int',    scale=1,    unit= None,   sc=None, ec=None ),
    'FCp_Partial_Delivered_Flow_Gall': SF(friendly='Partial Delived Flow (FCp)',           type='int',    scale=1,    unit='gal',   sc='ti', ec=None ),
    'FCp_Partial_Delivered_Flow_mc':   SF(friendly='Partial delived flow (FCp)',           type='float',  scale=1000, unit='m³',    sc='ti', ec=None ),
    'FCt_Total_Delivered_Flow_Gall':   SF(friendly='Total delived flow (FCt)',             type='int',    scale=1,    unit='gal' ,  sc='ti', ec=None ),
    'FCt_Total_Delivered_Flow_mc':     SF(friendly='Total delived flow (FCt)',             type='float',  scale=1000, unit='m³',    sc='ti', ec=None ),
    'FaultPumpsNumber':                SF(friendly='Fault pumps number',                   type='int',    scale=1,    unit= None,   sc=None, ec='d'  ),
    'FirmwareStatus':                  SF(friendly='Firmware status',                      type='enum',   scale=1,    unit= None,   sc=None, ec='d'  ),
    'GI_IntegralGainElasticPlant':     SF(friendly='Elastic plant integral gain (GI)',     type='float',  scale=10,   unit= None,   sc=None, ec=None ),
    'GI_IntegralGainRigidPlant':       SF(friendly='Rigid plant integral gain (GI)',       type='float',  scale=10,   unit= None,   sc=None, ec=None ),
    'GP_ProportionalGainElasticPlant': SF(friendly='Elastic plant proportional gain (GP)', type='float',  scale=10,   unit= None,   sc=None, ec=None ),
    'GP_ProportionalGainRigidPlant':   SF(friendly='Rigid plant proportional gain (GP)',   type='float',  scale=10,   unit= None,   sc=None, ec=None ),
    'GroupFlowGall':                   SF(friendly='Group flow',                           type='int',    scale=10,   unit='gal/m', sc='m',  ec=None ),
    'GroupFlowLiter':                  SF(friendly='Group flow',                           type='int',    scale=10,   unit='L/m',   sc='m',  ec=None ),
    'GroupPower':                      SF(friendly='Group power',                          type='int',    scale=1,    unit='W',     sc='m',  ec=None ),
    'HO_PowerOnHours':                 SF(friendly='Working hours',                        type='int',    scale=1,    unit='h',     sc='ti', ec=None ),
    'HO_PumpRunHours':                 SF(friendly='Pump running hours (HO)',              type='int',    scale=1,    unit='h',     sc='ti', ec=None ),
    'HvBoardId':                       SF(friendly='Hv board id',                          type='string', scale=1,    unit=None,    sc=None, ec='d'  ),
    'HvFwVersion':                     SF(friendly='Hv firmware version',                  type='string', scale=0,    unit=None,    sc=None, ec='d'  ),
    'IC_InverterConfig':               SF(friendly='Inverter config (IC)',                 type='string', scale=1,    unit=None,    sc=None, ec='d'  ),
    'InverterPresentNumber':           SF(friendly='Inverter present number',              type='int',    scale=1,    unit=None,    sc=None, ec='d'  ),
    'KernelVersion':                   SF(friendly='Kernel version',                       type='string', scale=1,    unit=None,    sc=None, ec='d'  ),
    'LA_Language':                     SF(friendly='Language (LA)',                        type='enum',   scale=1,    unit=None,    sc=None, ec='d'  ),
    'LastErrorOccurency':              SF(friendly='Last error occurency',                 type='string', scale=1,    unit=None,    sc=None, ec='d'  ),
    'LastErrorTimePowerOn':            SF(friendly='Last error time',                      type='int',    scale=1,    unit='h',     sc=None, ec='d'  ),
    'LatestError':                     SF(friendly='Latest error',                         type='string', scale=1,    unit=None,    sc=None, ec='d'  ),
    'LvFwVersion':                     SF(friendly='Lv firmware version',                  type='string', scale=0,    unit=None,    sc=None, ec='d'  ),
    'MS_MeasureSystem':                SF(friendly='Measure system (MS)',                  type='enum',   scale=1,    unit=None,    sc=None, ec='d'  ),
    'MainloopMaxTime':                 SF(friendly='Main loop max time',                   type='int',    scale=1,    unit=None,    sc='m',  ec='d'  ),
    'MainloopMinTime':                 SF(friendly='Main loop min time',                   type='int',    scale=1,    unit=None,    sc='m',  ec='d'  ),
    'NA_ActiveInverters':              SF(friendly='Active inverters (NA)',                type='int',    scale=1,    unit=None,    sc=None, ec=None ),
    'NA_ActiveContemporaryInverters':  SF(friendly='Active contemporary inverters (NC)',   type='int',    scale=1,    unit=None,    sc=None, ec=None ),
    'OD_PlantType':                    SF(friendly='Plant type (OD)',                      type='enum',   scale=1,    unit=None,    sc=None, ec='d'  ),
    'P1_Aux1SetpointBar':              SF(friendly='Aux1 setpoint (P1)',                   type='float',  scale=10,   unit='bar',   sc=None, ec='d'  ),
    'P1_Aux1SetpointPsi':              SF(friendly='Aux1 setpoint (P1)',                   type='float',  scale=1,    unit='psi',   sc=None, ec='d'  ),
    'P1_Aux2SetpointBar':              SF(friendly='Aux2 setpoint (P2)',                   type='float',  scale=10,   unit='bar',   sc=None, ec='d'  ),
    'P1_Aux2SetpointPsi':              SF(friendly='Aux2 setpoint (P2)',                   type='float',  scale=1,    unit='psi',   sc=None, ec='d'  ),
    'P1_Aux3SetpointBar':              SF(friendly='Aux3 setpoint (P3)',                   type='float',  scale=10,   unit='bar',   sc=None, ec='d'  ),
    'P1_Aux3SetpointPsi':              SF(friendly='Aux3 setpoint (P3)',                   type='float',  scale=1,    unit='psi',   sc=None, ec='d'  ),
    'P1_Aux4SetpointBar':              SF(friendly='Aux4 setpoint (P4)',                   type='float',  scale=10,   unit='bar',   sc=None, ec='d'  ),
    'P1_Aux4SetpointPsi':              SF(friendly='Aux4 setpoint (P4)',                   type='float',  scale=1,    unit='psi',   sc=None, ec='d'  ),
    'PK_LowPressureThresholdBar':      SF(friendly='Low pressure threshold (PK)',          type='float',  scale=10,   unit='bar',   sc=None, ec='d'  ),
    'PK_LowPressureThresholdPsi':      SF(friendly='Low pressure threshold (PK)',          type='float',  scale=1,    unit='psi',   sc=None, ec='d'  ),
    'PKm_SuctionPressureBar':          SF(friendly='Suction pressure (PKm)',               type='float',  scale=10,   unit='bar',   sc='m',  ec=None ),
    'PKm_SuctionPressurePsi':          SF(friendly='Suction pressure (PKm)',               type='float',  scale=1,    unit='psi',   sc='m',  ec=None ),
    'PO_OutputPower':                  SF(friendly='Output power (PO)',                    type='int',    scale=1,    unit='W',     sc='m',  ec=None ),
    'PR_RemotePressureSensor':         SF(friendly='Remote pressure sensor (PR)',          type='string', scale=1,    unit=None,    sc=None, ec='d'  ),
    'PanelBoardId':                    SF(friendly='Panel board id',                       type='string', scale=1,    unit=None,    sc=None, ec='d'  ),
    'PowerShowerBoost':                SF(friendly='Power shower boost',                   type='int',    scale=1,    unit='%',     sc=None, ec='d'  ),
    'PowerShowerCommand':              SF(friendly='Power shower command',                 type='string', scale=1,    unit=None,    sc=None, ec='d'  ),
    'PowerShowerCountdown':            SF(friendly='Power shower countdown',               type='int',    scale=1,    unit='s',     sc=None, ec='d'  ),
    'PowerShowerDuration':             SF(friendly='Power shower duration',                type='int',    scale=1,    unit='s',     sc=None, ec='d'  ),
    'PowerShowerPressureBar':          SF(friendly='Power shower pressure',                type='float',  scale=10,   unit='bar',   sc=None, ec='d'  ),
    'PowerShowerPressurePsi':          SF(friendly='Power shower pressure',                type='float',  scale=1,    unit='psi',   sc=None, ec='d'  ),
    'PressureTarget':                  SF(friendly='Pressure target',                      type='int',    scale=1,    unit=None,    sc=None, ec='d'  ),
    'ProductType':                     SF(friendly='Product type',                         type='string', scale=1,    unit=None,    sc=None, ec='d'  ),
    'PumpDisable':                     SF(friendly='Pump disable',                         type='string', scale=1,    unit=None,    sc='m',  ec='d'  ),
    'PumpStatus':                      SF(friendly='Pump status',                          type='enum',   scale=1,    unit=None,    sc=None, ec=None ),
    'RM_MaximumSpeed':                 SF(friendly='Maximum speed (RM)',                   type='int',    scale=1,    unit='rpm',   sc=None, ec='d'  ),
    'RP_PressureFallToRestartBar':     SF(friendly='Pressure fall to restart (RP)',        type='float',  scale=10,   unit='bar',   sc=None, ec='d'  ),
    'RP_PressureFallToRestartPsi':     SF(friendly='Pressure fall to restart (RP)',        type='float',  scale=1,    unit='psi',   sc=None, ec='d'  ),
    'RS_RotatingSpeed':                SF(friendly='Rotation speed (RS)',                  type='int',    scale=1,    unit='rpm',   sc='m',  ec=None ),
    'RamUsed':                         SF(friendly='Ram used',                             type='int',    scale=1,    unit='kB',    sc='m',  ec='d'  ),
    'RamUsedMax':                      SF(friendly='Ram used max',                         type='int',    scale=1,    unit='kB',    sc='m',  ec='d'  ),
    'RemotePressureSensorStatus':      SF(friendly='Remote pressure sensor status',        type='string', scale=1,    unit=None,    sc='m',  ec='d'  ),
    'RunningPumpsNumber':              SF(friendly='Running pumps number',                 type='int',    scale=1,    unit=None,    sc='m',  ec=None ),
    'SP_SetpointPressureBar':          SF(friendly='Setpoint pressure (SP)',               type='float',  scale=10,   unit='bar',   sc=None, ec='d'  ),
    'SP_SetpointPressurePsi':          SF(friendly='Setpoint pressure (SP)',               type='float',  scale=1,    unit='psi',   sc=None, ec='d'  ),
    'SleepModeCountdown':              SF(friendly='Sleep mode countdown',                 type='int',    scale=1,    unit='s',     sc=None, ec='d'  ),
    'SleepModeDuration':               SF(friendly='Sleep mode duration',                  type='int',    scale=1,    unit='s',     sc=None, ec='d'  ),
    'SleepModePressureBar':            SF(friendly='Sleep mode pressure',                  type='float',  scale=10,   unit='bar',   sc=None, ec='d'  ),
    'SleepModePressurePsi':            SF(friendly='Sleep mode pressure',                  type='float',  scale=1,    unit='psi',   sc=None, ec='d'  ),
    'SleepModeReduction':              SF(friendly='Sleep mode reduction',                 type='int',    scale=1,    unit='%',     sc=None, ec='d'  ),
    'SleepModeStartTime':              SF(friendly='Sleep mode start time',                type='int',    scale=1,    unit='s',     sc=None, ec='d'  ),
    'StartNumber':                     SF(friendly='Starts number',                        type='int',    scale=1,    unit=None,    sc='ti', ec=None ),
    'SystemStatus':                    SF(friendly='System status',                        type='string', scale=1,    unit=None,    sc=None, ec=None ),
    'T1_LowPressureDelay':             SF(friendly='Low pressure delay (T1)',              type='int',    scale=1,    unit='s',     sc=None, ec='d'  ),
    'T2_SwitchOffDelay':               SF(friendly='Switch off delay (T2)',                type='int',    scale=1,    unit='s',     sc=None, ec='d'  ),
    'TB_DryRunDetectTime':             SF(friendly='Dry run detect time (TB)',             type='int',    scale=1,    unit='s',     sc=None, ec='d'  ),
    'TE_HeatsinkTemperatureC':         SF(friendly='Heatsink temperature (TE)',            type='int',    scale=1,    unit='°C',    sc='m',  ec=None ),
    'TE_HeatsinkTemperatureF':         SF(friendly='Heatsink temperature (TE)',            type='int',    scale=1,    unit='°F',    sc='m',  ec=None ),
    'UpdateFirmware':                  SF(friendly='Firmware update',                      type='int',    scale=1,    unit=None,    sc=None, ec='d'  ),
    'UpdateProgress':                  SF(friendly='Update progress',                      type='string', scale=1,    unit=None,    sc=None, ec='d'  ),
    'UpdateType':                      SF(friendly='Update type',                          type='string', scale=1,    unit=None,    sc=None, ec='d'  ),
    'VF_FlowGall':                     SF(friendly='Flow (VF)',                            type='float',  scale=10,   unit='gal/m', sc='m',  ec=None ),
    'VF_FlowLiter':                    SF(friendly='Flow (VF)',                            type='float',  scale=10,   unit='L/m',   sc='m',  ec=None ),
    'VP_PressureBar':                  SF(friendly='Pressure (VP)',                        type='float',  scale=10,   unit='bar',   sc='m',  ec=None ),
    'VP_PressurePsi':                  SF(friendly='Pressure (VP)',                        type='float',  scale=1,    unit='psi',   sc='m',  ec=None ),
     
    # 
    # DConnect Box2  
    #
    'BootTime':                        SF(friendly='Boot time',                            type='float',  scale=3600, unit='h',     sc='m',  ec=None ),
    'CpuLoad':                         SF(friendly='Cpu load',                             type='int',    scale=1,    unit='%',     sc='m',  ec=None ),
    'DabMgr':                          SF(friendly='Dab manager',                          type='string', scale=0,    unit=None,    sc=None, ec='d'  ),
    'ESSID':                           SF(friendly='Wlan ESSID',                           type='string', scale=0,    unit=None,    sc=None, ec='d'  ),
    'Image':                           SF(friendly='Image',                                type='string', scale=0,    unit=None,    sc=None, ec='d'  ),
    'In1':                             SF(friendly='In1',                                  type='enum',   scale=1,    unit=None,    sc=None, ec=None ),
    'IpExt':                           SF(friendly='External IP',                          type='string', scale=0,    unit=None,    sc=None, ec='d'  ),
    'IpWlan':                          SF(friendly='Wlan IP',                              type='string', scale=0,    unit=None,    sc=None, ec='d'  ),
    'MacWlan':                         SF(friendly='Wlan mac',                             type='string', scale=0,    unit=None,    sc=None, ec='d'  ),
    'MemFree':                         SF(friendly='Memory free',                          type='float',  scale=1000, unit='MB',    sc='m',  ec=None ),
    'ModbusBaudRate':                  SF(friendly='Modbus baudrate',                      type='enum',   scale=1,    unit=None,    sc=None, ec='d'  ),
    'ModbusCountErrMsg':               SF(friendly='Modbus err msg count',                 type='int',    scale=1,    unit=None,    sc=None, ec='d'  ),
    'ModbusCountMsg':                  SF(friendly='Modbus msg count',                     type='int',    scale=1,    unit=None,    sc=None, ec='d'  ),
    'ModbusParity':                    SF(friendly='Modbus parity',                        type='enum',   scale=1,    unit=None,    sc=None, ec='d'  ),
    'ModbusStopBit':                   SF(friendly='Modbus stop bit',                      type='enum',   scale=1,    unit=None,    sc=None, ec='d'  ),
    'Out1':                            SF(friendly='Out1',                                 type='enum',   scale=1,    unit=None,    sc=None, ec=None ),
    'PLCStatus':                       SF(friendly='PLC status',                           type='enum',   scale=1,    unit=None,    sc=None, ec='d'  ),
    'PLCUpdatingStatus':               SF(friendly='PLC updating status',                  type='enum',   scale=1,    unit=None,    sc=None, ec='d'  ),
    'SampleRate':                      SF(friendly='Sample rate',                          type='enum',   scale=1,    unit=None,    sc=None, ec='d'  ),
    'SignLevel':                       SF(friendly='Wlan signal level',                    type='int',    scale=1,    unit='%',     sc='m',  ec=None ),
    'SystemStatus':                    SF(friendly='System status',                        type='enum',   scale=1,    unit=None,    sc=None, ec=None ),
    'Uptime':                          SF(friendly='Up time',                              type='float',  scale=3600, unit='h',     sc='ti', ec=None ),
    'UpdateSystem':                    SF(friendly='Update status',                        type='string', scale=1,    unit=None,    sc=None, ec='d'  ),
    'WifiMode':                        SF(friendly='Wlan mode',                            type='enum',   scale=1,    unit=None,    sc=None, ec=None ),
    'WlanRx':                          SF(friendly='Wlan data rx',                         type='float',  scale=1000, unit='MB',    sc='ti', ec=None ),
    'WlanTx':                          SF(friendly='Wlan data tx',                         type='float',  scale=1000, unit='MB',    sc='ti', ec=None ),
    'ucVersion':                       SF(friendly='Version uc',                           type='string', scale=0,    unit=None,    sc=None, ec='d', ),
}



