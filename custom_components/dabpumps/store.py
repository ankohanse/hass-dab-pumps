import asyncio
import logging
import os

from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

from homeassistant.helpers.storage import Store
from homeassistant.helpers.storage import STORAGE_DIR

from .const import (
    DOMAIN,
    STORE_KEY_CACHE,
)

_LOGGER = logging.getLogger(__name__)

# Define helper functions
utcnow = lambda: datetime.now(timezone.utc)


class DabPumpsStore(Store[dict]):
    """
    Data store that is persisted into a file under .storage
    """

    # Keep track of each single Store instance per store_key
    _instances = {}
    
    _STORAGE_VERSION_MAJOR = 3
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
        Migrate the store data
        """
        if old_major_version <= 2:
            # version 1 and 2 contained Dab Pumps raw http responses.
            # version 3 has no direct relation to this, just remove everything from the cache
            return {}

        else: 
            # version 3 is the current version. No migrate needed
            return old_data


    async def _async_migrate_file(self):
        """
        Migrate from legacy dabpumps.coordinator file into dabpumps.cache if needed
        """
        try:
            if self._migrate_file_checked:
                return

            if self._store_key == STORE_KEY_CACHE:
                # This migrate is only applicable for the 'cache' store
                await self._async_migrate_cache_file()

        except Exception as ex:
            _LOGGER.warning(f"Exception while migrating persisted {self.key}: {ex}")
            self._store_data = {}
            self._last_read = utcnow()

        finally:
            self._migrate_file_checked = True


    async def _async_migrate_cache_file(self):
        """
        Remove legacy dabpumps.coordinator file if needed
        """    
        async with self._migrate_file_lock:

            key_old = DabPumpsStore.make_key("coordinator")
            path_old = self.hass.config.path(STORAGE_DIR, key_old)

            try:
                if os.path.isfile(path_old):
                    _LOGGER.info(f"Remove legacy {key_old}")

                    with suppress(FileNotFoundError):
                        await self.hass.async_add_executor_job(os.unlink, path_old)

            except Exception as e:
                _LOGGER.debug(f"Exception: {e}")


    async def async_read(self):
        """
        Load the persisted storage file and return its data
        """

        # Migrate from old dabpumps.coordinator file if needed
        await self._async_migrate_file()

        try:
            # Persisted file already read?
            if self._last_read > datetime.min:
                return 
            
            # Read the persisted file
            _LOGGER.info(f"Read persisted {self.key}")
            self._store_data = await super().async_load() or {}

        except Exception as ex:
            _LOGGER.warning(f"Exception while reading persisted {self.key}: {ex}")
            self._store_data = {}

        finally:
            self._last_read = utcnow()


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
            
                if (utcnow() - self._last_write).total_seconds() < self._write_period:
                    # Not long enough since last write
                    return        

            _LOGGER.info(f"Write persisted {self.key}")
            await super().async_save(self._store_data)

        except Exception as ex:
            _LOGGER.warning(f"Exception while writing persisted {self.key}: {ex}")

        finally:
            self._last_write = utcnow()


    def get(self, item_key: str, item_default: Any = None):
        """
        Get an item from the store data
        """
        _LOGGER.debug(f"Try fetch from {self.key}: {item_key}")
        return self._store_data.get(item_key, item_default)
    

    def set(self, item_key: str, item_val: Any):
        """
        Set an item into the store data
        """
        self._store_data[item_key] = item_val
        self._last_change = utcnow()


    async def async_get_diagnostics(self):
        """
        Return cache properties. Used for diagnostics
        """
        return {
            "version": self.version,
            "minor_version": self.minor_version,
            "key": self.key,
            "last_read": self._last_read,
            "last_write": self._last_write,
            "last_change": self._last_change,
            "data": self._store_data,
        }
    


