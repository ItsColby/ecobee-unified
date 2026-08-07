"""Ecobee cloud-only read projections."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_MILLION,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    SUFFIX_AIR_QUALITY_INDEX,
    SUFFIX_CO2,
    SUFFIX_EQUIPMENT_STAGE,
    SUFFIX_VOC,
)
from .entity import EcobeeUnifiedEntity
from .manager import MappingManager
from .models import MappingConfig, NormalizedSnapshot
from .runtime import EcobeeUnifiedConfigEntry


@dataclass(frozen=True, slots=True)
class Projection:
    """One bounded cloud-only sensor projection."""

    suffix: str
    translation_key: str
    value: Callable[[NormalizedSnapshot], str | float | None]
    device_class: SensorDeviceClass | None = None
    unit: str | None = None
    state_class: SensorStateClass | None = None


PROJECTIONS = {
    SUFFIX_EQUIPMENT_STAGE: Projection(
        SUFFIX_EQUIPMENT_STAGE,
        "equipment_stage",
        lambda snapshot: equipment_stage(snapshot.equipment_running),
    ),
    SUFFIX_AIR_QUALITY_INDEX: Projection(
        SUFFIX_AIR_QUALITY_INDEX,
        "air_quality_index",
        lambda snapshot: snapshot.air_quality_index,
        SensorDeviceClass.AQI,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SUFFIX_CO2: Projection(
        SUFFIX_CO2,
        "co2",
        lambda snapshot: snapshot.co2,
        SensorDeviceClass.CO2,
        CONCENTRATION_PARTS_PER_MILLION,
        SensorStateClass.MEASUREMENT,
    ),
    SUFFIX_VOC: Projection(
        SUFFIX_VOC,
        "voc",
        lambda snapshot: snapshot.voc,
        SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS,
        CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        SensorStateClass.MEASUREMENT,
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcobeeUnifiedConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up only justified, non-duplicate cloud projections."""

    manager = entry.runtime_data.manager
    entities: list[EcobeeCloudSensor] = []
    for mapping in manager.mappings:
        suffixes = [SUFFIX_EQUIPMENT_STAGE]
        if mapping.ecobee_aqi_entity:
            suffixes.append(SUFFIX_AIR_QUALITY_INDEX)
        if mapping.ecobee_co2_entity:
            suffixes.append(SUFFIX_CO2)
        if mapping.ecobee_voc_entity:
            suffixes.append(SUFFIX_VOC)
        entities.extend(
            EcobeeCloudSensor(manager, mapping, PROJECTIONS[suffix])
            for suffix in suffixes
        )
    async_add_entities(entities)


class EcobeeCloudSensor(EcobeeUnifiedEntity, SensorEntity):
    """One no-I/O projection from the mapping snapshot."""

    def __init__(
        self, manager: MappingManager, mapping: MappingConfig, projection: Projection
    ) -> None:
        super().__init__(
            manager, mapping, projection.suffix, projection.translation_key
        )
        self._projection = projection
        self._attr_device_class = projection.device_class
        self._attr_native_unit_of_measurement = projection.unit
        self._attr_state_class = projection.state_class

    @property
    @override
    def available(self) -> bool:
        return self.native_value is not None

    @property
    @override
    def native_value(self) -> str | float | None:
        return self._projection.value(self._snapshot)


def equipment_stage(equipment_running: str | None) -> str | None:
    """Normalize Ecobee equipment tokens into a small Recorder-safe state set."""

    if equipment_running is None:
        return None
    tokens = {
        token.strip().lower() for token in equipment_running.split(",") if token.strip()
    }
    if not tokens:
        return "idle"
    stages = {
        stage for token in tokens if (stage := _EQUIPMENT_STAGES.get(token)) is not None
    }
    if "fan" in stages and len(stages) > 1:
        stages.remove("fan")
    unknown = tokens.difference(_EQUIPMENT_STAGES)
    if unknown or len(stages) > 1:
        return "multiple"
    if stages:
        return next(iter(stages))
    return "unknown"


_EQUIPMENT_STAGES = {
    "fan": "fan",
    "compcool1": "cool_stage_1",
    "compcool2": "cool_stage_2",
    "heatpump1": "heat_pump_stage_1",
    "heatpump2": "heat_pump_stage_2",
    "heatpump3": "heat_pump_stage_3",
    "auxheat1": "aux_heat_stage_1",
    "auxheat2": "aux_heat_stage_2",
    "auxheat3": "aux_heat_stage_3",
    "humidifier": "humidifying",
    "dehumidifier": "dehumidifying",
    "ventilator": "ventilating",
}
