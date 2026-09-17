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
    UnitOfDensity,
    UnitOfRatio,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    SUFFIX_AIR_QUALITY_INDEX,
    SUFFIX_CO2,
    SUFFIX_EQUIPMENT_STAGE,
    SUFFIX_VOC,
)
from .datapoint_entity import UnifiedDatapointSensor
from .datapoints import BINARY_KINDS
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
    options: tuple[str, ...] | None = None


EQUIPMENT_STAGE_OPTIONS = (
    "idle",
    "fan",
    "cooling",
    "heating",
    "drying",
    "defrosting",
    "preheating",
    "cool_stage_1",
    "cool_stage_2",
    "heat_pump_stage_1",
    "heat_pump_stage_2",
    "heat_pump_stage_3",
    "aux_heat_stage_1",
    "aux_heat_stage_2",
    "aux_heat_stage_3",
    "humidifying",
    "dehumidifying",
    "ventilating",
    "multiple",
    "unknown",
)


PROJECTIONS = {
    SUFFIX_EQUIPMENT_STAGE: Projection(
        suffix=SUFFIX_EQUIPMENT_STAGE,
        translation_key="equipment_stage",
        value=lambda snapshot: snapshot.equipment_stage,
        device_class=SensorDeviceClass.ENUM,
        options=EQUIPMENT_STAGE_OPTIONS,
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
        UnitOfRatio.PARTS_PER_MILLION,
        SensorStateClass.MEASUREMENT,
    ),
    SUFFIX_VOC: Projection(
        SUFFIX_VOC,
        "voc",
        lambda snapshot: snapshot.voc,
        SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS,
        UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
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
    entities: list[SensorEntity] = []
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
    datapoints = entry.runtime_data.datapoints
    if datapoints is not None:
        entities.extend(
            UnifiedDatapointSensor(datapoints, config)
            for config in datapoints.configs
            if config.kind not in BINARY_KINDS and config.kind != "weather"
        )
    async_add_entities(entities)


class EcobeeCloudSensor(EcobeeUnifiedEntity, SensorEntity):
    """One no-I/O projection from the mapping snapshot."""

    _unrecorded_attributes = frozenset({"action_reported_at", "equipment_reported_at"})

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
        self._attr_options = list(projection.options) if projection.options else None

    @property
    @override
    def available(self) -> bool:
        return self.native_value is not None

    @property
    @override
    def native_value(self) -> str | float | None:
        return self._projection.value(self._snapshot)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, object] | None:
        if self._projection.suffix != SUFFIX_EQUIPMENT_STAGE:
            return None
        snapshot = self._snapshot
        return {
            "selected_source": snapshot.provenance.get("equipment_stage"),
            "action_source": snapshot.provenance.get("hvac_action"),
            "detail_status": snapshot.equipment_detail_status,
            "reported_equipment_stage": snapshot.reported_equipment_stage,
            "action_reported_at": snapshot.action_reported_at,
            "equipment_reported_at": snapshot.equipment_reported_at,
            "time_basis": "current_action_with_qualified_reported_detail",
        }
