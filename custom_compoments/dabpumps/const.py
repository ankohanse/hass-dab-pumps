"""Constants for the DAB Pumps integration."""
import logging
from homeassistant.const import Platform


_LOGGER: logging.Logger = logging.getLogger(__package__)
#_LOGGER = logging.getLogger("custom_components.dabpumps")

# Base component constants
DOMAIN = "dabpumps"
NAME = "DAB Pumps"
VERSION="2023.12.1"
ISSUE_URL = "https://github.com/ankoh/dabpumps/issues"


PLATFORMS: list[Platform] = [Platform.SENSOR]
HUB = "Hub"
COORDINATOR = "Coordinator"

DEFAULT_USERNAME = "email@mycompany.com"
DEFAULT_PASSWORD = ""
DEFAULT_POLLING_INTERVAL = 20

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