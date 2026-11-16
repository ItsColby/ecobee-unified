"""models.py stays free of Home Assistant imports, so pin its copies to Core."""

from homeassistant.components.climate import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)

from custom_components.ecobee_unified import models


def test_models_copies_match_core_climate_values() -> None:
    assert {mode.value for mode in HVACMode} == models.HVAC_MODES
    assert {action.value for action in HVACAction} == models.HVAC_ACTIONS
    assert ClimateEntityFeature.TARGET_TEMPERATURE == models.TARGET_TEMPERATURE_FEATURE
    assert (
        ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        == models.TARGET_TEMPERATURE_RANGE_FEATURE
    )
    assert ClimateEntityFeature.TARGET_HUMIDITY == models.TARGET_HUMIDITY_FEATURE
