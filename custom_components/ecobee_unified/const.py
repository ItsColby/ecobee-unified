"""Constants for Ecobee Unified."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ecobee_unified"
NAME: Final = "Ecobee Unified"
PLATFORMS: Final = ["climate", "number", "sensor"]

CONF_MAPPINGS: Final = "mappings"
CONF_MAPPING_ID: Final = "mapping_id"
CONF_NAME: Final = "name"
CONF_HOMEKIT_ENTITY: Final = "homekit_entity"
CONF_HOMEKIT_PRESET_ENTITY: Final = "homekit_preset_entity"
CONF_HOMEKIT_CLEAR_HOLD_ENTITY: Final = "homekit_clear_hold_entity"
CONF_ECOBEE_ENTITY: Final = "ecobee_entity"
CONF_ECOBEE_AQI_ENTITY: Final = "ecobee_aqi_entity"
CONF_ECOBEE_CO2_ENTITY: Final = "ecobee_co2_entity"
CONF_ECOBEE_VOC_ENTITY: Final = "ecobee_voc_entity"
CONF_ADD_ANOTHER: Final = "add_another"
CONF_CONFIRM_CHANGE: Final = "confirm_change"

CONF_ECOBEE_STALE_SECONDS: Final = "ecobee_stale_seconds"
CONF_CONFIRMATION_SECONDS: Final = "confirmation_seconds"

DEFAULT_ECOBEE_STALE_SECONDS: Final = 900
DEFAULT_CONFIRMATION_SECONDS: Final = 660

SERVICE_RESUME_PROGRAM: Final = "resume_program"
SERVICE_CREATE_VACATION: Final = "create_vacation"
SERVICE_DELETE_VACATION: Final = "delete_vacation"
SERVICE_SET_OCCUPANCY_MODES: Final = "set_occupancy_modes"
SERVICE_SET_SENSORS_USED_IN_CLIMATE: Final = "set_sensors_used_in_climate"
SOURCE_HOMEKIT: Final = "homekit"
SOURCE_ECOBEE: Final = "ecobee"
SIGNAL_SNAPSHOT_UPDATED: Final = f"{DOMAIN}_snapshot_updated"

SUFFIX_MINIMUM_FAN_RUNTIME: Final = "minimum_fan_runtime"
SUFFIX_EQUIPMENT_STAGE: Final = "equipment_stage"
SUFFIX_AIR_QUALITY_INDEX: Final = "air_quality_index"
SUFFIX_CO2: Final = "co2"
SUFFIX_VOC: Final = "voc"

MAX_ATTRIBUTE_ITEMS: Final = 8
MAX_ATTRIBUTE_TEXT: Final = 64
