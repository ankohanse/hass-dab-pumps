import asyncio
import copy
import logging
import os

from datetime import datetime, timezone
from typing import Any

from homeassistant.helpers.storage import Store
from homeassistant.helpers.storage import STORAGE_DIR

from .const import (
    DOMAIN,
    STORE_KEY_CACHE,
)

_LOGGER = logging.getLogger(__name__)


class DabPumpsStore(Store[dict]):
    """
    Data store that is persisted into a file under .storage
    """

    # Keep track of each single Store instance per store_key
    _instances = {}
    
    _STORAGE_VERSION_MAJOR = 2
    _STORAGE_VERSION_MINOR = 0

    def __new__(cls, hass, store_key: str, *args, **kwargs):
        """
        Create a new store instance if needed or return existing instance.
        """
        if store_key not in cls._instances:
            # If no instance exists for this key then create a new one
            _LOGGER.debug(f"Create {store_key}")
            instance = super().__new__(cls)
            cls._instances[store_key] = instance
        else:
            _LOGGER.debug(f"Reuse {store_key}")

        return cls._instances[store_key]
    

    def __init__(self, hass, store_key: str, write_period: int):
        """
        Initialize a new store instance
        """
        
        # Initialize only if it really is a new instance
        if not hasattr(self, '_initialized'):

            super().__init__(
                hass, 
                key = DabPumpsStore.make_key(store_key),
                version=self._STORAGE_VERSION_MAJOR, 
                minor_version=self._STORAGE_VERSION_MINOR
            )

            self._write_period = write_period

            self._store_key = store_key            
            self._store_data = {}

            self._last_read = datetime.min
            self._last_write = datetime.min
            self._last_change = datetime.min

            self._migrate_file_checked = False
            self._migrate_file_lock = asyncio.Lock()

            self._initialized = True


    def make_key(store_key: str):
        """Make the key/filename the store is persisted in"""
        return f"{DOMAIN}.{store_key}"
    

    def set_key(self, key: str):
        """Update the 'key' property and force refresh of cached properties that are derived from it"""
        self.key = key
        _LOGGER.debug(f"Set key to {key}")

        # Force a refresh of any cached_property derived from it
        if 'path' in self.__dict__:
            del self.path   
            _LOGGER.debug(f"Set path to {self.path}")


    async def _async_migrate_func(self, old_major_version, old_minor_version, old_data):
        """
        Migrate the history store data
        """
        if old_major_version <= 1:
            # version 1 had a cache per installation. We flatten that structure for version 2
            data = {}
            for install_id in old_data.keys():
                _LOGGER.debug(f"Migrate {install_id} (flatten)")

                if self._store_key in old_data[install_id]:
                    _LOGGER.debug(f"Migrate {install_id} - {self._store_key} (flatten)")
                    store_dict = old_data[install_id][self._store_key]

                    for k,v in store_dict.items():
                        _LOGGER.debug(f"Migrate {install_id} - {self._store_key} - {k} (copy)")
                        data[k] = v

        else: 
            # version 2 is the current version. No migrate needed
            data = old_data

        return data


    async def _async_migrate_file(self):
        """
        Migrate from legacy dabpumps.coordinator file into dabpumps.cache if needed
        """
        try:
            if self._migrate_file_checked:
                return

            if self._store_key != STORE_KEY_CACHE:
                # This migrate is only applicable for the 'cache' store
                return

            async with self._migrate_file_lock:

                key_old = DabPumpsStore.make_key("coordinator")
                key_new = self.key
                store_name_old = self.hass.config.path(STORAGE_DIR, key_old)
                store_name_new = self.hass.config.path(STORAGE_DIR, key_new)

                if not os.path.isfile(store_name_old):
                    # No old file so nothing to migrate
                    return

                if not os.path.isfile(store_name_new):
                    _LOGGER.info(f"Migrate legacy {key_old} storage into {key_new}")

                    # Try to load using the old key
                    try:
                        self.set_key(key_old)
                        self._store_data = await super().async_load()
                    except Exception as e:
                        _LOGGER.debug(f"Exception: {e}")
                    finally:
                        self.set_key(key_new)

                    # Save using the new key and delete old file
                    if self._store_data:
                        await super().async_save(self._store_data)

                else:
                    # Don't delete legacy file yet. We will wait until next release to do this...
                    #
                    # _LOGGER.info(f"Remove legacy {key_old}")
                    # try:
                    #     self.set_key(key_old)
                    #     await super().async_remove()
                    # except Exception as e:
                    #     _LOGGER.debug(f"Exception: {e}")
                    # finally:
                    #     self.set_key(key_new)
                    pass

        except Exception as ex:
            _LOGGER.warning(f"Exception while migrating persisted {self.key}: {ex}")
            self._store_data = {}
            self._last_read = datetime.now()

        finally:
            self._migrate_file_checked = True


    async def async_read(self):
        """
        Load the persisted storage file and return its data
        """

        await self._async_migrate_file()

        try:
            # Migrate from old dabpumps.coordinator file if needed
            if self._last_read > datetime.min:
                return 
            
            # Read the persisted file
            _LOGGER.info(f"Read persisted {self.key}")
            self._store_data = await super().async_load() or {}

        except Exception as ex:
            _LOGGER.warning(f"Exception while reading persisted {self.key}: {ex}")
            self._store_data = {}

        finally:
            self._last_read = datetime.now()


    async def async_write(self, force: bool = False):
        """
        Save the data into the persisted storage file
        """
        try:
            if not force:
                if len(self._store_data) == 0:
                    # Nothing to persist
                    return 
                
                if (self._last_change <= self._last_write):
                    # No changes since last write
                    return
            
                if (datetime.now() - self._last_write).total_seconds() < self._write_period:
                    # Not long enough since last write
                    return        

            _LOGGER.info(f"Write persisted {self.key}")
            await super().async_save(self._store_data)

        except Exception as ex:
            _LOGGER.warning(f"Exception while writing persisted {self.key}: {ex}")

        finally:
            self._last_write = datetime.now()


    def get(self, item_key: str, item_default: Any = None):
        """
        Get an item from the store data
        """
        _LOGGER.debug(f"Try fetch from {self.key}: {item_key}")
        item_val = self._store_data.get(item_key, item_default)

        if isinstance(item_val, dict):
            item_val.pop("ts", None)

        return item_val
    

    def set(self, item_key: str, item_val: Any):
        """
        Set an item into the store data
        """
        if isinstance(item_val, dict):
            store_val = copy.deepcopy(item_val)
            store_val["ts"] = datetime.now(timezone.utc)
        else:
            store_val = item_val

        self._store_data[item_key] = store_val
        self._last_change = datetime.now()


    def items(self):
        """
        Return all data items. Used for diagnostics
        """
        return [ (k,v) for k,v in self._store_data.items() ]
