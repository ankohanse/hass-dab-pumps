"""config_flow.py: Config flow for DAB Pumps integration."""
from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
import homeassistant.helpers.config_validation as cv

from homeassistant import config_entries, exceptions

from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.exceptions import IntegrationError

from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
)

from .const import (
    DOMAIN,
    DEFAULT_USERNAME,
    DEFAULT_PASSWORD,
    DEFAULT_POLLING_INTERVAL,
)

from .dabpumpsapi import (
    DabPumpsApi,
    DabPumpsApiError,
    DabPumpsApiAuthError,
)

_LOGGER = logging.getLogger(__name__)


DEFAULT_USERNAME = "user@mydomain.com"
DEFAULT_PASSWORD = ""

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema({
            vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
            vol.Required(CONF_PASSWORD, default=DEFAULT_PASSWORD): str,
        })
    },
	extra=vol.ALLOW_EXTRA,
)


@config_entries.HANDLERS.register("dabpumps")
class ConfigFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    # Make sure user input data is passed from one step to the next using user_input_from_step_user
    def __init__(self):
        self._username = DEFAULT_USERNAME
        self._password = DEFAULT_PASSWORD
        self._errors = None


    async def async_try_connection(self):
        _LOGGER.debug("DAB Pumps trying connection...")

        dabpumps_api = DabPumpsApi(self._username, self._password)
        try:
            # Call the DabPumpsApi with the detect_device method
            device_map = await dabpumps_api.async_detect_devices()
            
        except DabPumpsApiError as e:
            return f"Failed to connect to DABPumps website: {e}"
            
        except DabPumpsApiAuthError as e:
            return f"Authentication failed: {e}"

        _LOGGER.debug("DAB Pumps successfull connection!")
        return None
        

    # This is step 1 for the user/pass function.
    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle the initial step."""

        if user_input is not None:
            _LOGGER.debug(f"DAB Pumps config flow handle user input")
            
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]
            
            self._errors = await self.async_try_connection()
            if not self._errors:
                # Attach a unique ID to this config flow to avoid the same hub being setup twice
                entry_unique_id = self._username.strip().lower()
                
                await self.async_set_unique_id(entry_unique_id)
                self._abort_if_unique_id_configured()
                
                # Create the integration entry
                return self.async_create_entry(
                    title = entry_unique_id, 
                    data = {
                        CONF_USERNAME: self._username,
                        CONF_PASSWORD: self._password,
                    }
                )
            
        # Show the form with the username+password
        _LOGGER.debug(f"DAB Pumps config flow show user input form")
        
        return self.async_show_form(
            step_id = "user", 
            data_schema = CONFIG_SCHEMA,
            errors = self._errors
        )
        

class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handles options flow for the component."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry
        self._errors = None


    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            _LOGGER.debug(f"DAB Pumps options flow handle user input")
            self._errors = []
            
            if not errors:
                # Value of data will be set on the options property of the config_entry instance.
                self.async_update_entry(
                    self.config_entry, 
                    data = {
                        'polling_interval': user_input['polling interval']
                    }, 
                    options=self.config_entry.options
                )
                return self.async_create_entry(title="", data={})
                
            _LOGGER.error(f"Error: {self._errors}")
            
        # Show the form with the options
        _LOGGER.debug(f"DAB Pumps options flow show user input form")
        
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required("polling interval", default=self.config_entry.options.get("polling_interval", DEFAULT_POLLING_INTERVAL)): 
                    vol.All(vol.Coerce(int), vol.Range(min=5))
            }),
            errors = self._errors
        )
 