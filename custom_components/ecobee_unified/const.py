"""Constants for Ecobee Unified."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ecobee_unified"
NAME: Final = "Ecobee Unified"
PLATFORMS: Final = ["climate"]

CONF_MAPPINGS: Final = "mappings"
CONF_MAPPING_ID: Final = "mapping_id"
CONF_NAME: Final = "name"
CONF_HOMEKIT_ENTITY: Final = "homekit_entity"
CONF_ECOBEE_ENTITY: Final = "ecobee_entity"
CONF_SCHEDULED_PROFILE_ENTITY: Final = "scheduled_profile_entity"
CONF_NEXT_TRANSITION_ENTITY: Final = "next_transition_entity"
CONF_ADD_ANOTHER: Final = "add_another"
CONF_CONFIRM_CHANGE: Final = "confirm_change"

CONF_HOMEKIT_STALE_SECONDS: Final = "homekit_stale_seconds"
CONF_ECOBEE_STALE_SECONDS: Final = "ecobee_stale_seconds"
CONF_BEESTAT_STALE_SECONDS: Final = "beestat_stale_seconds"
CONF_CONFIRMATION_SECONDS: Final = "confirmation_seconds"

DEFAULT_HOMEKIT_STALE_SECONDS: Final = 300
DEFAULT_ECOBEE_STALE_SECONDS: Final = 900
DEFAULT_BEESTAT_STALE_SECONDS: Final = 21_600
DEFAULT_CONFIRMATION_SECONDS: Final = 660

SERVICE_RESUME_PROGRAM: Final = "resume_program"
SERVICE_SET_MINIMUM_FAN_RUNTIME: Final = "set_minimum_fan_runtime"
ATTR_RESUME_ALL: Final = "resume_all"
ATTR_MINUTES: Final = "minutes"

SOURCE_HOMEKIT: Final = "homekit"
SOURCE_ECOBEE: Final = "ecobee"
SOURCE_BEESTAT: Final = "beestat"

SIGNAL_SNAPSHOT_UPDATED: Final = f"{DOMAIN}_snapshot_updated"

MAX_ATTRIBUTE_ITEMS: Final = 8
MAX_ATTRIBUTE_TEXT: Final = 64
