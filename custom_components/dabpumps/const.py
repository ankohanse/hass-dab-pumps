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
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
]

HUB = "Hub"
API = "Api"
COORDINATOR = "Coordinator"
HELPER = "Helper"

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


DABPUMPS_SSO_URL = "https://dabsso.dabpumps.com"
DABPUMPS_API_URL = "https://dconnect.dabpumps.com"
DABPUMPS_API_DOMAIN = "dconnect.dabpumps.com"
DABPUMPS_API_TOKEN_COOKIE = "dabcsauthtoken"
DABPUMPS_API_TOKEN_TIME_MIN = 10 # seconds remaining before we re-login

COORDINATOR_RETRY_ATTEMPTS = 10
COORDINATOR_RETRY_DELAY = 5    # seconds

API_LOGIN = types.SimpleNamespace()
API_LOGIN.DABLIVE_APP_0 = 'DabLive_app_0'
API_LOGIN.DABLIVE_APP_1 = 'DabLive_app_1'
API_LOGIN.DCONNECT_APP = 'DConnect_app'
API_LOGIN.DCONNECT_WEB = 'DConnect_web'

API_CLIENT_TIMEOUT = 120.0

# Debug: set this constant to True to simulate a configuration with multiple installations for one DAB account
SIMULATE_MULTI_INSTALL = False
SIMULATE_SUFFIX_ID = "_test"
SIMULATE_SUFFIX_NAME = " (test)"


STARTUP_MESSAGE = f"""
----------------------------------------------------------------------------
{NAME}
Domain: {DOMAIN}
----------------------------------------------------------------------------
"""