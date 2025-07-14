"""Constants for the DAB Pumps integration."""
import logging
import types

from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
)
from homeassistant.const import Platform

_LOGGER: logging.Logger = logging.getLogger(__package__)
#_LOGGER = logging.getLogger("custom_components.dabpumps")

# Base component constants
DOMAIN = "dabpumps"
NAME = "DAB Pumps"
ISSUE_URL = "https://github.com/ankohanse/hass-dab-pumps/issues"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.TIME,
]

HUB = "Hub"
API = "Api"
COORDINATOR = "Coordinator"

DEFAULT_USERNAME = ""
DEFAULT_PASSWORD = ""
DEFAULT_POLLING_INTERVAL = 20
DEFAULT_LANGUAGE = "auto"

CONF_INSTALL_ID = "install_id"
CONF_INSTALL_NAME = "install_name"
CONF_OPTIONS = "options"
CONF_POLLING_INTERVAL = "polling_interval"

MSG_POLLING_INTERVAL = 'polling_interval'
MSG_LANGUAGE = 'language'

STORE_KEY_CACHE = "cache"
CACHE_WRITE_PERIOD = 300 # seconds

DIAGNOSTICS_REDACT = { CONF_PASSWORD, 'client_secret' }

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

LANGUAGE_AUTO = "auto"
LANGUAGE_AUTO_FALLBACK = "en"
LANGUAGE_MAP = {
    "auto": "Auto",
    "cs": "Czech",
    "nl": "Dutch",
    "en": "English",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pl": "Polish",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovenian",
    "es": "Spanish",
    "sf": "Swedish",
    # "": "Turkish",
    # "": "Thai"
}

LANGUAGE_TEXT_AUTO ="Auto (use system setting: {0})"
LANGUAGE_TEXT_FALLBACK ="Auto (use default: {0})"

BINARY_SENSOR_VALUES_ON = ['1', 'active']
BINARY_SENSOR_VALUES_OFF = ['0', 'disactive', 'inactive']
BINARY_SENSOR_VALUES_ALL = BINARY_SENSOR_VALUES_ON + BINARY_SENSOR_VALUES_OFF

SWITCH_VALUES_ON = ['1', 'Enable']
SWITCH_VALUES_OFF = ['0', 'Disable']
SWITCH_VALUES_ALL = SWITCH_VALUES_ON + SWITCH_VALUES_OFF

BUTTON_VALUES_ALL = ['1']

COORDINATOR_RETRY_ATTEMPTS = 2
COORDINATOR_RETRY_DELAY = 5    # seconds
COORDINATOR_TIMEOUT = 120   # seconds

STATUS_VALIDITY_PERIOD = 15*60 # seconds
