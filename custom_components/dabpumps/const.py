"""Constants for the DAB Pumps integration."""
import logging

from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
)

_LOGGER: logging.Logger = logging.getLogger(__package__)
#_LOGGER = logging.getLogger("custom_components.dabpumps")

# Base component constants
DOMAIN = "dabpumps"
NAME = "DAB Pumps"
VERSION="2023.12.1"
ISSUE_URL = "https://github.com/ankoh/dabpumps/issues"

HUB = "Hub"
API = "Api"
COORDINATOR = "Coordinator"

DEFAULT_USERNAME = ""
DEFAULT_PASSWORD = ""
DEFAULT_POLLING_INTERVAL = 20

CONF_INSTALL_ID = "install_id"
CONF_INSTALL_NAME = "install_name"
CONF_OPTIONS = "options"
CONF_POLLING_INTERVAL = "polling_interval"

DIAGNOSTICS_REDACT = { CONF_PASSWORD }

ATTR_PRODUCT_DESCRIPTION = "Product Description"
ATTR_DESTINATION_NAME = "Destination Name"
ATTR_LAST_UPDATED = "Last Updated"
ATTR_SOURCE_NAME = "Source Name"
ATTR_UNIQUE_ID = "Internal Unique ID"
ATTR_PRODUCT_NAME = "Device Name"
ATTR_PRODUCT_VENDOR = "Vendor"
ATTR_PRODUCT_SERIAL = "Vendor Product Serial"
ATTR_PRODUCT_VERSION = "Vendor Firmware Version"
ATTR_PRODUCT_BUILD = "Vendor Product Build"
ATTR_PRODUCT_FEATURES = "Vendor Product Features"
ATTR_PRODUCT_INSTALL = "Installation Name"

DABPUMPS_API_URL = "https://dconnect.dabpumps.com"


STARTUP_MESSAGE = f"""
----------------------------------------------------------------------------
{NAME}
Version: {VERSION}
Domain: {DOMAIN}
----------------------------------------------------------------------------
"""