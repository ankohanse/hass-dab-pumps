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
from homeassistant.helpers.selector import selector

from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
)

from .const import (
    DOMAIN,
    DEFAULT_USERNAME,
    DEFAULT_PASSWORD,
    DEFAULT_POLLING_INTERVAL,
    CONF_INSTALL_ID,
    CONF_INSTALL_NAME,
    CONF_POLLING_INTERVAL,
)

from .api import (
    DabPumpsApiFactory,
    DabPumpsApi,
    DabPumpsApiError,
    DabPumpsApiAuthError,
)

_LOGGER = logging.getLogger(__name__)


@config_entries.HANDLERS.register("dabpumps")
class ConfigFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""
    
    VERSION = 1
    
    def __init__(self):
        """Initialize config flow."""
        self._username = None
        self._password = None
        self._install_map = {}
        self._install_id = None
        self._install_name = None
        self._errors = None
    
    
    async def async_try_connection(self):
        """Test the username and password by connecting to the DConnect website"""
        _LOGGER.info("Trying connection...")
        
        dabpumpsapi = DabPumpsApiFactory.create(None, self._username, self._password)
        try:
            # Call the DabPumpsApi with the detect_device method
            self._install_map = await dabpumpsapi.async_detect_installs()
            
            _LOGGER.info("Successfully connected!")
            _LOGGER.debug(f"install_map: {self._install_map}")
                
            return True
        
        except DabPumpsApiError as e:
            self._errors = f"Failed to connect to DAB Pumps DConnect website: {e}"
            return False
        
        except DabPumpsApiAuthError as e:
            self._errors = f"Authentication failed: {e}"
            return False
    
    
    # This is step 1 for the user/pass function.
    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle a flow initialized by the user."""
        
        if user_input is not None:
            _LOGGER.debug(f"Config flow handle username+password input")
            
            self._username = user_input.get(CONF_USERNAME, '')
            self._password = user_input.get(CONF_PASSWORD, '')
            
            # test the username+password and retrieve installations available to this user
            await self.async_try_connection()
            
            if not self._errors:
                # go to the second step to choose which installation to use
                return await self.async_step_install()
        
        # Show the form with the username+password and optionally a list of installations
        _LOGGER.debug(f"Config flow show username+password input form")
        
        return self.async_show_form(
            step_id = "user", 
            data_schema = vol.Schema({
                vol.Required(CONF_USERNAME, description={"suggested_value": DEFAULT_USERNAME}): str,
                vol.Required(CONF_PASSWORD, description={"suggested_value": DEFAULT_PASSWORD}): str,
            }),
            errors = self._errors
        )
    
    
    async def async_step_install(self, user_input=None) -> FlowResult:
        """Second step im config flow to choose which installation to use"""
        
        # if there is only one installation found, then automatically select it and skip display of form
        if self._install_map and len(self._install_map)==1:
            _LOGGER.info(f"Auto select the only installation available")
            user_input = {
                CONF_INSTALL_NAME: next( (install.name for install in self._install_map.values()), None)
            }
        
        if user_input is not None:
            _LOGGER.debug(f"Config flow handle installation input")
            
            self._install_name = user_input.get(CONF_INSTALL_NAME, None)
            self._install_id = next( (install.id for install in self._install_map.values() if install.name == self._install_name), None)

            # Do we have everything we need?
            if not self._errors and self._install_id and self._install_name:

                # Use install_id as unique_id for this config flow to avoid the same hub being setup twice
                await self.async_set_unique_id(self._install_id)
                self._abort_if_unique_id_configured()
            
                # Create the integration entry
                return self.async_create_entry(
                    title = self._install_name, 
                    data = {
                        CONF_USERNAME: self._username,
                        CONF_PASSWORD: self._password,
                        CONF_INSTALL_ID: self._install_id,
                        CONF_INSTALL_NAME: self._install_name,
                    },
                    options = {
                        CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
                    }
                )

        # Show a form with the list of installations
        _LOGGER.debug(f"Config flow show installation input form")
        
        return self.async_show_form(
            step_id = "install", 
            data_schema = vol.Schema({
                vol.Required(CONF_INSTALL_NAME): selector({
                   "select": {
                      "options": [ install.name for install in self._install_map.values() ]
                   }
                })
            }),
            errors = self._errors
        )
    
    
    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handles options flow for the component."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry
        self._errors = None


    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            _LOGGER.debug(f"Options flow handle user input")
            self._errors = []
            
            if not errors:
                # Value of data will be set on the options property of the config_entry instance.
                return self.async_create_entry(
                    title="",
                    data = {
                        CONF_POLLING_INTERVAL: user_input['polling interval']
                    }
                )

            _LOGGER.error(f"Error: {self._errors}")
            
        # Show the form with the options
        _LOGGER.debug(f"Options flow show user input form")
        
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_POLLING_INTERVAL, default=self.config_entry.options.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL)): 
                    vol.All(vol.Coerce(int), vol.Range(min=5))
            }),
            errors = self._errors
        )
 