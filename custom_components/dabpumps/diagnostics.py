"""Provides diagnostics for custom component."""

import logging

from dataclasses import fields, is_dataclass
from datetime import datetime
from multidict import MultiDict, MultiDictProxy
from types import MappingProxyType, NoneType
from typing import Any, Mapping

from homeassistant.components.diagnostics.util import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from aiodabpumps import (
    DabPumpsHistoryItem, 
    DabPumpsHistoryDetail,
    DabPumpsDictFactory,
)

from .const import (
    CONF_INSTALL_ID,
    CONF_INSTALL_NAME,
    DIAGNOSTICS_REDACT,
)
from .coordinator import (
    DabPumpsCoordinatorFactory,
    DabPumpsCoordinator,
)


_LOGGER = logging.getLogger(__name__)


async def async_get_config_entry_diagnostics(hass: HomeAssistant, config_entry: ConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    install_id = config_entry.data[CONF_INSTALL_ID]
    install_name = config_entry.data[CONF_INSTALL_NAME]
    _LOGGER.info(f"Retrieve diagnostics for install {install_name} ({install_id})")
    
    coordinator: DabPumpsCoordinator = DabPumpsCoordinatorFactory.create(hass, config_entry)
    diagnostics = {
        "config": {
            "data": config_entry.data,
            "options": config_entry.options,
        },
        "coordinator": await coordinator.async_get_diagnostics(),
        "api": await coordinator._api.async_get_diagnostics(), 
    }

    # Convert contents to only contain standard structures: int, float, str, list, dict, ...
    diagnostics_dict = to_dict(diagnostics)

    # Hide passwords etc.
    return async_redact_data(diagnostics_dict, DIAGNOSTICS_REDACT)


# For some specific dataclasses we exclude None values
DATACLASSES_EXCLUDE_NONE = (DabPumpsHistoryItem, DabPumpsHistoryDetail)


def to_dict(obj: Any, dict_factory=dict) -> Any:
        """
        Recursive to dictionary handler that is aware of dataclasses, Mapping and MultiDict proxies at any level in the data structure
        """
        try:
            if isinstance(obj, (int,float,str,NoneType)):
                return obj
            
            elif isinstance(obj, datetime):
                return obj.isoformat()
            
            elif is_dataclass(obj):
                # Not using dataclass.asdict() method, because it does not recurse in to the dataclass field values
                # and convert the values themselves to dicts (using dict_factory).

                df = dict_factory if not isinstance(obj, DATACLASSES_EXCLUDE_NONE) else DabPumpsDictFactory.exclude_none_values

                result = []
                for f in fields(obj):
                    value = to_dict(getattr(obj, f.name), df)
                    result.append((f.name, value))

                return df(result)
                
            elif isinstance(obj, (list,tuple)):
                if hasattr(obj, '_fields'):
                    # namedtuple, Standard asdict will not recurse in to the namedtuple fields and convert them to dicts (using dict_factory).
                    return type(obj)( *[to_dict(v, dict_factory) for v in obj] )

                else:
                    # standard tuple or a list        
                    return type(obj)( to_dict(v, dict_factory) for v in obj )
            
            elif isinstance(obj, dict):
                if hasattr(type(obj), 'default_factory'):
                    # defaultdict has a different constructor from dict
                    result = type(obj)(getattr(obj, 'default_factory'))
                else:
                    result = type(obj)()
            
                for k, v in obj.items():
                    result[k] = to_dict(v, dict_factory)
                return result
            
            elif isinstance(obj, (Mapping, MappingProxyType)):
                 return to_dict(dict(obj), dict_factory)
            
            elif isinstance(obj, (MultiDict, MultiDictProxy)):
                 return to_dict(obj.copy(), dict_factory)
            
            else:
                return f"{type(obj)} {obj}"
            
        except Exception as ex:
            return f"Could not serialize type {type(obj)}: {ex}"
        




