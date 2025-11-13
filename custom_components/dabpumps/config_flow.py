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
    CONF_LANGUAGE,
)

from pydabpumps import (
    DabPumpsUserRole,
    DabPumpsError,
    DabPumpsAuthError,
) 


from .const import (
    DOMAIN,
    DEFAULT_USERNAME,
    DEFAULT_PASSWORD,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_LANGUAGE,
    CONF_INSTALL_ID,
    CONF_INSTALL_NAME,
    CONF_POLLING_INTERVAL,
    MSG_POLLING_INTERVAL,
    MSG_LANGUAGE,
    LANGUAGE_MAP,
    LANGUAGE_AUTO,
    LANGUAGE_AUTO_FALLBACK,
    LANGUAGE_TEXT_AUTO,
    LANGUAGE_TEXT_FALLBACK,
)

from .coordinator import (
    DabPumpsCoordinatorFactory,
    DabPumpsCoordinator,
)

_LOGGER = logging.getLogger(__name__)

# internal consts only used in config flow
CONF_ROLE_MENU = "role_menu"

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
        self._errors = {}

        # Assign the HA configured log level of this module to the v module
        log_level: int = _LOGGER.getEffectiveLevel()
        lib_logger: logging.Logger = logging.getLogger("pydabpumps")
        lib_logger.setLevel(log_level)

        _LOGGER.info(f"Logging at {logging.getLevelName(log_level)}")
    
    
    async def async_try_connection(self):
        """Test the username and password by connecting to the DConnect website"""
        _LOGGER.info("Trying connection...")
        
        self._errors = {}
        coordinator = DabPumpsCoordinatorFactory.create_temp(self._username, self._password)
        try:
            # Call the DabPumpsApi with the detect_device method
            self._install_map = await coordinator.async_config_flow_data()
            
            if self._install_map:
                _LOGGER.info("Successfully connected!")
                _LOGGER.debug(f"install_map: {self._install_map}")
                self._errors = {}
                return True
            else:
                self._errors[CONF_USERNAME] = f"No installations detected"
        
        except DabPumpsError as e:
            self._errors[CONF_PASSWORD] = f"Failed to connect to DAB Pumps DConnect servers"
        except DabPumpsAuthError as e:
            self._errors[CONF_PASSWORD] = f"Authentication failed"
        except Exception as e:
            self._errors[CONF_PASSWORD] = f"Unknown error: {e}"
        
        return False
    

    async def async_try_update_role(self, role_old: DabPumpsUserRole, role_new: DabPumpsUserRole):
        """Try to update the user role to Installer/Professional"""

        _LOGGER.info(f"Trying update of user role from {role_old} to {role_new}...")
        
        self._errors = {}
        coordinator = DabPumpsCoordinatorFactory.create_temp(self._username, self._password)
        try:
            # Call the DabPumpsApi with the update_role
            role_rsp = await coordinator.async_config_change_role(self._install_id, role_old, role_new)
            
            if role_rsp is not None:
                _LOGGER.info("Successfully updated role!")
                self._errors = {}
                return True
            else:
                self._errors[CONF_ROLE_MENU] = f"Role update failed"
        
        except DabPumpsError as e:
            self._errors[CONF_ROLE_MENU] = f"Failed to connect to DAB Pumps DConnect servers"
        except DabPumpsAuthError as e:
            self._errors[CONF_ROLE_MENU] = f"Authentication failed"
        except Exception as e:
            self._errors[CONF_ROLE_MENU] = f"Unknown error: {e}"
        
        return False

    
    # This is step 1 for the user/pass function.
    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle a flow initialized by the user."""
        
        if user_input is not None:
            _LOGGER.debug(f"Step user - handle input {user_input}")
            
            self._username = user_input.get(CONF_USERNAME, '')
            self._password = user_input.get(CONF_PASSWORD, '')
            
            # test the username+password and retrieve installations available to this user
            await self.async_try_connection()
            
            if not self._errors:
                # go to the second step to choose which installation to use
                return await self.async_step_install()
        
        # Show the form with the username+password and optionally a list of installations
        _LOGGER.debug(f"Step user - show form")
        
        return self.async_show_form(
            step_id = "user", 
            data_schema = vol.Schema({
                vol.Required(CONF_USERNAME, description={"suggested_value": self._username or DEFAULT_USERNAME}): str,
                vol.Required(CONF_PASSWORD, description={"suggested_value": self._password or DEFAULT_PASSWORD}): str,
            }),
            errors = self._errors
        )
    
    
    async def async_step_install(self, user_input=None) -> FlowResult:
        """Second step im config flow to choose which installation to use"""
        
        # if there is only one installation found, then automatically select it and skip display of form
        if self._install_map and len(self._install_map)==1:
            _LOGGER.debug(f"Step install - auto select the only installation available")
            user_input = {
                CONF_INSTALL_NAME: next( (install.name for install in self._install_map.values()), None)
            }
        
        if user_input is not None:
            _LOGGER.debug(f"Step install - handle input {user_input}")
            
            self._install_name = user_input.get(CONF_INSTALL_NAME, None)
            self._install_id = next( (install.id for install in self._install_map.values() if install.name == self._install_name), None)

            # Do we have everything we need?
            if not self._errors and self._install_id and self._install_name:
                # go to the second step to check the user role in this install
                return await self.async_step_role();

        # Show a form with the list of installations
        _LOGGER.debug(f"Step install - show form")        

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
    
    
    async def async_step_role(self, user_input=None) -> FlowResult:
        """Third step in config flow to check user role"""
        
        role_old = next( (install.role for install in self._install_map.values() if install.id == self._install_id), DabPumpsUserRole.CUSTOMER)
        _LOGGER.debug(f"Step role - detected current role {role_old}")

        if role_old == DabPumpsUserRole.INSTALLER:
            _LOGGER.debug(f"Step role - auto keep current role {role_old}")
            user_input = {
                CONF_ROLE_MENU: "KEEP"
            }
        
        if user_input is not None:
            _LOGGER.debug(f"Step role - handle input {user_input}")
            
            chosen = user_input.get(CONF_ROLE_MENU, None)
            match chosen:
                case "keep_role":
                    # Keep using the current role
                    pass
            
                case "update_role":
                    # Try and update role to installer. Ignore if this fails
                    role_new = DabPumpsUserRole.INSTALLER
                    await self.async_try_update_role(role_old, role_new)

            # Finish the config flow
            return await self.async_step_finish();

        # Show a form with role choices
        _LOGGER.debug(f"Step role - build schema")
        self._menu_options = {
            "update_role": "update_role",
            "keep_role": "keep_role",
        }
        schema = vol.Schema({
            vol.Required(CONF_ROLE_MENU): selector({
                "select": { 
                    "options": list(self._menu_options.values()),
                    "mode": "list",
                    "translation_key": CONF_ROLE_MENU
                }
            })
        })

        _LOGGER.debug(f"Step role - show form")
        return self.async_show_form(
            step_id = "role", 
            data_schema = schema,
            errors = self._errors,
            last_step = False,
        )
    
    
    async def async_step_finish(self, user_input=None) -> FlowResult:
        """Configuration has finished"""
        
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
                CONF_LANGUAGE: DEFAULT_LANGUAGE,
            }
        )
    

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler()


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handles options flow for the component."""

    def __init__(self) -> None:
        """Initialize options flow."""
        super().__init__()

        self._polling_interval = None
        self._language_code = None
        self._language_name = None
        self._errors = {}

        # Display actual system language name or fallback language name inside the LANGUAGE_MAP options
        self._language_map = LANGUAGE_MAP
        self._language_map[LANGUAGE_AUTO] = self._get_language_auto_text()


    def _get_language_auto_text(self):
        system_language_code = DabPumpsCoordinator.system_language()

        if system_language_code in LANGUAGE_MAP:
            system_language_name = LANGUAGE_MAP[system_language_code]
            return LANGUAGE_TEXT_AUTO.format(system_language_name)
        else:
            fallback_language_name = LANGUAGE_MAP[LANGUAGE_AUTO_FALLBACK]
            return LANGUAGE_TEXT_FALLBACK.format(fallback_language_name)


    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            _LOGGER.debug(f"Options flow handle user input")
            self._errors = {}

            self._polling_interval = user_input[MSG_POLLING_INTERVAL]
            self._language_name = user_input.get(MSG_LANGUAGE, None)
            self._language_code = next( (code for code,name in self._language_map.items() if name == self._language_name), None)

            # Do we have everything we need?
            if not self._errors and self._language_code:

                # Value of data will be set on the options property of the config_entry instance.
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    options = {
                        CONF_POLLING_INTERVAL: self._polling_interval,
                        CONF_LANGUAGE: self._language_code,
                    } 
                )
                return self.async_create_entry(title=None, data=None)

            _LOGGER.error(f"Error: {self._errors}")
        
        else:
            self._polling_interval = self.config_entry.options.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL)
            self._language_code = self.config_entry.options.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
            self._language_name = next( (name for code,name in self._language_map.items() if code == self._language_code), LANGUAGE_MAP[DEFAULT_LANGUAGE])

        # Show the form with the options
        _LOGGER.debug(f"Options flow show user input form")

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(MSG_POLLING_INTERVAL, default=self._polling_interval): 
                    vol.All(vol.Coerce(int), vol.Range(min=5)),
                vol.Required(MSG_LANGUAGE, default=self._language_name): selector({
                   "select": {
                      "options": [ name for name in self._language_map.values() ]
                   }
                })
            }),
            errors = self._errors
        )
 