"""No-I/O Home Assistant entities for explicitly configured datapoints."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .datapoints import DatapointConfig, DatapointManager, DatapointSnapshot


class _UnifiedDatapointEntity(Entity):
    """Attach a composed reading to its existing primary-source device."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({"reported_at"})

    def __init__(self, manager: DatapointManager, config: DatapointConfig) -> None:
        self._manager = manager
        self._config = config
        self._attr_unique_id = manager.unique_id(config.datapoint_id)
        self._attr_name = config.name
        source = (
            None
            if config.weather_config_entry_id
            else manager.resolve_entity_id(config.sources[0].entity)
        )
        self.device_entry = (
            async_entity_id_to_device(manager.hass, source) if source else None
        )

    @property
    def _snapshot(self) -> DatapointSnapshot:
        return self._manager.snapshot(self._config.datapoint_id)

    @property
    @override
    def available(self) -> bool:
        return self._snapshot.available

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose bounded source provenance without growing ages or raw payloads."""
        snapshot = self._snapshot
        attributes: dict[str, Any] = {
            "source": snapshot.selected_source,
            "time_basis": self._config.time_basis,
            "semantic": self._config.semantic or self._config.kind,
            "interval_seconds": self._config.interval_seconds,
            "observed_at": snapshot.observed_at,
            "reported_at": snapshot.reported_at,
            "quality": snapshot.status,
            "fallback_used": snapshot.fallback_used,
            "source_statuses": {
                str(item.index + 1): item.status for item in snapshot.source_statuses
            },
        }
        if self._config.kind == "configured_membership":
            attributes["members"] = (
                list(snapshot.value) if isinstance(snapshot.value, tuple) else None
            )
        if self._config.kind == "profile":
            attributes["value_representation"] = snapshot.value_representation
        if snapshot.source_context is not None:
            attributes["source_context"] = asdict(snapshot.source_context)
        return attributes

    async def async_added_to_hass(self) -> None:
        """Subscribe to the manager's normalized output only."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._manager.signal(self._config.datapoint_id),
                self.async_write_ha_state,
            )
        )


class UnifiedDatapointSensor(_UnifiedDatapointEntity, SensorEntity):
    """An explicitly configured current reading or labeled interval observation."""

    def __init__(self, manager: DatapointManager, config: DatapointConfig) -> None:
        super().__init__(manager, config)
        self._attr_native_unit_of_measurement = config.unit
        self._attr_device_class = {
            "temperature": SensorDeviceClass.TEMPERATURE,
            "humidity": SensorDeviceClass.HUMIDITY,
            "battery": SensorDeviceClass.BATTERY,
            "duration": SensorDeviceClass.DURATION,
        }.get(config.kind)
        self._attr_state_class = (
            SensorStateClass.MEASUREMENT
            if config.time_basis == "current"
            and config.kind in {"temperature", "humidity", "battery"}
            else None
        )

    @property
    @override
    def native_value(self) -> float | str | None:
        value = self._snapshot.value
        if self._config.kind == "configured_membership":
            return len(value) if isinstance(value, tuple) else None
        return value if isinstance(value, str | float) else None


class UnifiedDatapointBinarySensor(_UnifiedDatapointEntity, BinarySensorEntity):
    """One occupancy or motion semantic, never a truthy coercion of raw values."""

    def __init__(self, manager: DatapointManager, config: DatapointConfig) -> None:
        super().__init__(manager, config)
        self._attr_device_class = (
            BinarySensorDeviceClass.OCCUPANCY
            if config.kind == "occupancy"
            else BinarySensorDeviceClass.MOTION
        )

    @property
    @override
    def is_on(self) -> bool | None:
        value = self._snapshot.value
        return value if isinstance(value, bool) else None
