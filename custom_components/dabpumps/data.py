"""
Data definitions for Smart Water and Gallagher Water integrations.

Note that this file is shared as is between the two integrations. 
Do not place code that is specific to only one of these integration in here!
"""
import logging

from dataclasses import asdict, dataclass
from enum import StrEnum

from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
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


# Define logger
_LOGGER = logging.getLogger(__name__)


class ParamCategory(StrEnum):
    SENSOR = "sens"
    CONTROL = "ctrl"
    CONFIG = "conf"
    DIAGNOSTICS = "diag"

class ParamStateClass(StrEnum):
    NONE = "none"
    TOTAL = "total"
    TOTAL_INC = "total_inc"
    MEASUREMENT = "meas"


@dataclass
class PI:
    grp: str                    # Parameter Group
    key: str                    # Parameter Key
    vis: bool                   # True=vis, False=suppressed
    mod: bool = False           # Is the parameter allowed to be edited or always rendered as read-only sensor
    cat: ParamCategory = None   # Entity Category (Sensors/Controls/Config/Diagnostics)
    cls: ParamStateClass = None # State Class (None, Total, Total Increasing, Measurement)

PARAM_INFOS = [
    # The first match on group and key is leading. Subsequent matches are ignored.

    # Groups that are normally visible and can be modified (with exceptions for specific keys)
    PI(grp="Extra Comfort",        key="",                                vis=True,  mod=True,  cat="ctrl", cls="meas"),
    PI(grp="Setpoint",             key="",                                vis=True,  mod=True,  cat="conf", cls="meas"),

    PI(grp="System Management",    key="FactoryDefault",                  vis=False),
#    PI(grp="System Management",    key="IdentifyDevice",                  vis=False),
    PI(grp="System Management",    key="PumpDisable",                     vis=True,  mod=True,  cat="conf", cls="meas"),
    PI(grp="System Management",    key="",                                vis=True,  mod=True,  cat="diag", cls="meas"),

#    PI(grp="Advanced",             key="Identify",                        vis=False),
    PI(grp="Advanced",             key="UpdateSystem",                    vis=False),
    PI(grp="Advanced",             key="",                                vis=True,  mod=True,  cat="diag", cls="meas"),

    # Groups that are normally visible but presented as readonly (with exceptions for specific keys)
    PI(grp="Group Status",         key="",                                vis=True,  mod=False, cat="sens", cls="meas"),
    PI(grp="I/O",                  key="",                                vis=True,  mod=False, cat="sens", cls="meas"),
    PI(grp="IO",                   key="",                                vis=True,  mod=False, cat="sens", cls="meas"),
    PI(grp="Installer",            key="",                                vis=True,  mod=False, cat="diag", cls="meas"),
    PI(grp="DConnect",             key="",                                vis=True,  mod=False, cat="diag", cls="meas"),
    PI(grp="PLC",                  key="",                                vis=True,  mod=False, cat="diag", cls="meas"),
    PI(grp="Modbus",               key="",                                vis=True,  mod=False, cat="diag", cls="none"),
    PI(grp="Updates",              key="",                                vis=True,  mod=False, cat="diag", cls="meas"),
    PI(grp="Version",              key="",                                vis=True,  mod=False, cat="diag", cls="meas"),

    PI(grp="Status",               key="LastErrorOccurrency",             vis=True,  mod=False, cat="diag", cls="meas"),
    PI(grp="Status",               key="LastErrorTimePowerOn",            vis=True,  mod=False, cat="diag", cls="meas"),
    PI(grp="Status",               key="ucVersion",                       vis=True,  mod=False, cat="diag", cls="meas"),
    PI(grp="Status",               key="Image",                           vis=True,  mod=False, cat="diag", cls="meas"),

    PI(grp="Status",               key="Actual_Period_Flow_Counter",      vis=True,  mod=False, cat="sens", cls="total_inc"),
    PI(grp="Status",               key="Actual_Period_Flow_Counter_Gall", vis=True,  mod=False, cat="sens", cls="total_inc"),
    PI(grp="Status",               key="Actual_Period_Energy_Counter",    vis=True,  mod=False, cat="sens", cls="total_inc"),
    PI(grp="Status",               key="FCp_Partial_Delivered_Flow_Gall", vis=True,  mod=False, cat="sens", cls="total_incti"),
    PI(grp="Status",               key="FCp_Partial_Delivered_Flow_mc",   vis=True,  mod=False, cat="sens", cls="total_inc"),
    PI(grp="Status",               key="FCt_Total_Delivered_Flow_Gall",   vis=True,  mod=False, cat="sens", cls="total_inc"),
    PI(grp="Status",               key="FCt_Total_Delivered_Flow_mc",     vis=True,  mod=False, cat="sens", cls="total_inc"),
    PI(grp="Status",               key="HO_PowerOnHours",                 vis=True,  mod=False, cat="sens", cls="total_inc"),
    PI(grp="Status",               key="HO_PumpRunHours",                 vis=True,  mod=False, cat="sens", cls="total_inc"),
    PI(grp="Status",               key="PartialEnergy",                   vis=True,  mod=False, cat="sens", cls="total_inc"),
    PI(grp="Status",               key="SO_PowerOnSeconds",               vis=True,  mod=False, cat="sens", cls="total_inc"),
    PI(grp="Status",               key="SO_PumpRunSeconds",               vis=True,  mod=False, cat="sens", cls="total_inc"),
    PI(grp="Status",               key="StartNumber",                     vis=True,  mod=False, cat="sens", cls="total_inc"),
    PI(grp="Status",               key="TotalEnergy",                     vis=True,  mod=False, cat="sens", cls="total_inc"),
    PI(grp="Status",               key="UpTime",                          vis=True,  mod=False, cat="sens", cls="total_inc"),
    PI(grp="Status",               key="WlanRx",                          vis=True,  mod=False, cat="sens", cls="total_inc"),
    PI(grp="Status",               key="WlanTx",                          vis=True,  mod=False, cat="sens", cls="total_inc"),
    PI(grp="Status",               key="Last_Period_Flow_Counter",        vis=True,  mod=False, cat="sens", cls="none"),
    PI(grp="Status",               key="Last_Period_Flow_Counter_Gall",   vis=True,  mod=False, cat="sens", cls="none"),
    PI(grp="Status",               key="Last_Period_Energy_Counter",      vis=True,  mod=False, cat="sens", cls="none"),
    PI(grp="Status",               key="",                                vis=True,  mod=False, cat="sens", cls="meas"),
    
    PI(grp="Sensors Status",       key="Fluid_Remain",                    vis=True,  mod=False, cat="sens", cls="none"),
    PI(grp="Sensors Status",       key="Fluid_Remain_inch",               vis=True,  mod=False, cat="sens", cls="none"),
    PI(grp="Sensors Status",       key="",                                vis=True,  mod=False, cat="sens", cls="meas"),

    PI(grp="Firmware Updates",     key="UpdateFirmware",                  vis=False),
    PI(grp="Firmware Updates",     key="UpdateProgress",                  vis=False),
    PI(grp="Firmware Updates",     key="ForceDownload",                   vis=False),
    PI(grp="Firmware Updates",     key="",                                vis=True,  mod=False, cat="diag", cls="meas"),

    PI(grp="Technical Assistance", key="PW_ModifyPassword",               vis=False),
    PI(grp="Technical Assistance", key="",                                vis=True,  mod=False, cat="diag", cls="meas"),

    # Groups that are explicitly not visible (with exceptions for specific keys)
    PI(grp="Debug",                key="RamUsed",                         vis=True,  mod=False, cat="diag", cls="meas"),
    PI(grp="Debug",                key="RamUsedMax",                      vis=True,  mod=False, cat="diag", cls="meas"),
    PI(grp="Debug",                key="",                                vis=False),   

    PI(grp="Errors",               key="LatestError",                     vis=True,  mod=False, cat="diag", cls="meas"),
    PI(grp="Errors",               key="RF_EraseHistoricalFault",         vis=True,  mod=True,  cat="diag", cls="meas"),
    PI(grp="Errors",               key="",                                vis=False),

    PI(grp="ModbusDevice",         key="",                                vis=False),

    # All other groups not yet known will be made visible
    PI(grp="",                     key="",                                vis=True,  mod=False, cat="sens", cls="meas"),
]


class ParamInfo(PI):
    def __init__(self, pi: PI):
        super().__init__(**asdict(pi))


    @staticmethod
    def find(group: str, key: str) -> 'ParamInfo':

        # Loop over list to find the first match
        return next( (ParamInfo(pi) for pi in PARAM_INFOS if pi.grp in [group,""] and pi.key in [key,""]), None )

    

@dataclass
class UI:
    dp_unit: str                # DabPumps unit
    ha_unit: str                # Home Assistant unit
    icon: str                   # Icon
    num_cls: NumberDeviceClass  # DeviceClass for Number entities
    sen_cls: SensorDeviceClass  # DeviceClass for sensor entities

UNIT_INFOS = [
    UI(dp_unit='°C',        ha_unit=UnitOfTemperature.CELSIUS,               icon='mdi:thermometer',    num_cls=NumberDeviceClass.TEMPERATURE,      sen_cls=SensorDeviceClass.TEMPERATURE),
    UI(dp_unit='Ã‚Â°C',     ha_unit=UnitOfTemperature.CELSIUS,               icon='mdi:thermometer',    num_cls=NumberDeviceClass.TEMPERATURE,      sen_cls=SensorDeviceClass.TEMPERATURE),
    UI(dp_unit='°F',        ha_unit=UnitOfTemperature.FAHRENHEIT,            icon='mdi:thermometer',    num_cls=NumberDeviceClass.TEMPERATURE,      sen_cls=SensorDeviceClass.TEMPERATURE),
    UI(dp_unit='Ã‚Â°F',     ha_unit=UnitOfTemperature.FAHRENHEIT,            icon='mdi:thermometer',    num_cls=NumberDeviceClass.TEMPERATURE,      sen_cls=SensorDeviceClass.TEMPERATURE),
    UI(dp_unit='bar',       ha_unit=UnitOfPressure.BAR,                      icon='mdi:water-pump',     num_cls=NumberDeviceClass.PRESSURE,         sen_cls=SensorDeviceClass.PRESSURE),
    UI(dp_unit='psi',       ha_unit=UnitOfPressure.PSI,                      icon='mdi:water-pump',     num_cls=NumberDeviceClass.PRESSURE,         sen_cls=SensorDeviceClass.PRESSURE),
    UI(dp_unit='mc',        ha_unit=UnitOfVolume.CUBIC_METERS,               icon='mdi:water',          num_cls=NumberDeviceClass.WATER,            sen_cls=SensorDeviceClass.WATER),
    UI(dp_unit='l',         ha_unit=UnitOfVolume.LITERS,                     icon='mdi:water',          num_cls=NumberDeviceClass.WATER,            sen_cls=SensorDeviceClass.WATER),
    UI(dp_unit='gall',      ha_unit=UnitOfVolume.GALLONS,                    icon='mdi:water',          num_cls=NumberDeviceClass.WATER,            sen_cls=SensorDeviceClass.WATER),
    UI(dp_unit='l/min',     ha_unit=UnitOfVolumeFlowRate.LITERS_PER_MINUTE,  icon='mdi:hydro-power',    num_cls=NumberDeviceClass.VOLUME_FLOW_RATE, sen_cls=SensorDeviceClass.VOLUME_FLOW_RATE),
    UI(dp_unit='gall/min',  ha_unit=UnitOfVolumeFlowRate.GALLONS_PER_MINUTE, icon='mdi:hydro-power',    num_cls=NumberDeviceClass.VOLUME_FLOW_RATE, sen_cls=SensorDeviceClass.VOLUME_FLOW_RATE),
    UI(dp_unit='gpm',       ha_unit=UnitOfVolumeFlowRate.GALLONS_PER_MINUTE, icon='mdi:hydro-power',    num_cls=NumberDeviceClass.VOLUME_FLOW_RATE, sen_cls=SensorDeviceClass.VOLUME_FLOW_RATE),
    UI(dp_unit='cm',        ha_unit=UnitOfLength.CENTIMETERS,                icon='mdi:waves-arrow-up', num_cls=NumberDeviceClass.DISTANCE,         sen_cls=SensorDeviceClass.DISTANCE),
    UI(dp_unit='inch',      ha_unit=UnitOfLength.INCHES,                     icon='mdi:waves-arrow-up', num_cls=NumberDeviceClass.DISTANCE,         sen_cls=SensorDeviceClass.DISTANCE),
    UI(dp_unit='ms',        ha_unit=UnitOfTime.MILLISECONDS,                 icon=None,                 num_cls=NumberDeviceClass.DURATION,         sen_cls=SensorDeviceClass.DURATION),
    UI(dp_unit='s',         ha_unit=UnitOfTime.SECONDS,                      icon='mdi:timer-sand',     num_cls=NumberDeviceClass.DURATION,         sen_cls=SensorDeviceClass.DURATION),
    UI(dp_unit='secondi',   ha_unit=UnitOfTime.SECONDS,                      icon='mdi:timer-sand',     num_cls=NumberDeviceClass.DURATION,         sen_cls=SensorDeviceClass.DURATION),
    UI(dp_unit='min',       ha_unit=UnitOfTime.MINUTES,                      icon='mdi:timer-sand',     num_cls=None,                               sen_cls=None),
    UI(dp_unit='h',         ha_unit=UnitOfTime.HOURS,                        icon='mdi:timer',          num_cls=None,                               sen_cls=None),
    UI(dp_unit='rpm',       ha_unit=REVOLUTIONS_PER_MINUTE,                  icon=None,                 num_cls=None,                               sen_cls=None),
    UI(dp_unit='B',         ha_unit=UnitOfInformation.BYTES,                 icon='mdi:memory',         num_cls=NumberDeviceClass.DATA_SIZE,        sen_cls=SensorDeviceClass.DATA_SIZE),
    UI(dp_unit='kB',        ha_unit=UnitOfInformation.KILOBYTES,             icon='mdi:memory',         num_cls=NumberDeviceClass.DATA_SIZE,        sen_cls=SensorDeviceClass.DATA_SIZE),
    UI(dp_unit='KB',        ha_unit=UnitOfInformation.KILOBYTES,             icon='mdi:memory',         num_cls=NumberDeviceClass.DATA_SIZE,        sen_cls=SensorDeviceClass.DATA_SIZE),
    UI(dp_unit='MByte',     ha_unit=UnitOfInformation.MEGABYTES,             icon='mdi:memory',         num_cls=NumberDeviceClass.DATA_SIZE,        sen_cls=SensorDeviceClass.DATA_SIZE),
    UI(dp_unit='%',         ha_unit=PERCENTAGE,                              icon='mdi:percent',        num_cls=NumberDeviceClass.POWER_FACTOR,     sen_cls=SensorDeviceClass.POWER_FACTOR),
    UI(dp_unit='V',         ha_unit=UnitOfElectricPotential.VOLT,            icon='mdi:lightning-bolt', num_cls=NumberDeviceClass.VOLTAGE,          sen_cls=SensorDeviceClass.VOLTAGE),
    UI(dp_unit='A',         ha_unit=UnitOfElectricCurrent.AMPERE,            icon='mdi:lightning-bolt', num_cls=NumberDeviceClass.CURRENT,          sen_cls=SensorDeviceClass.CURRENT),
    UI(dp_unit='W',         ha_unit=UnitOfPower.WATT,                        icon='mdi:power-plug',     num_cls=NumberDeviceClass.POWER,            sen_cls=SensorDeviceClass.POWER),
    UI(dp_unit='kW',        ha_unit=UnitOfPower.KILO_WATT,                   icon='mdi:power-plug',     num_cls=NumberDeviceClass.POWER,            sen_cls=SensorDeviceClass.POWER),
    UI(dp_unit='Wh',        ha_unit=UnitOfEnergy.WATT_HOUR,                  icon='mdi:lightning',      num_cls=NumberDeviceClass.ENERGY,           sen_cls=SensorDeviceClass.ENERGY),
    UI(dp_unit='kWh',       ha_unit=UnitOfEnergy.KILO_WATT_HOUR,             icon='mdi:lightning',      num_cls=NumberDeviceClass.ENERGY,           sen_cls=SensorDeviceClass.ENERGY),
    UI(dp_unit='Address',   ha_unit=None,                                    icon=None,                 num_cls=None,                               sen_cls=None),
    UI(dp_unit='SW. Vers.', ha_unit=None,                                    icon=None,                 num_cls=None,                               sen_cls=None),
    UI(dp_unit='',          ha_unit=None,                                    icon=None,                 num_cls=None,                               sen_cls=None),
    UI(dp_unit=None,        ha_unit=None,                                    icon=None,                 num_cls=None,                               sen_cls=None),
]
            

class UnitInfo(UI):
    def __init__(self, ui: UI):
        super().__init__(**asdict(ui))


    @staticmethod
    def find_by_dabpumps_unit(unit: str) -> 'UnitInfo':

        # Loop over list to find the first match
        return next( (UnitInfo(ui) for ui in UNIT_INFOS if ui.dp_unit in [unit,None]), None )
