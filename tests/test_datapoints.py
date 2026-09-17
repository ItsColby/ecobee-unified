"""Real HA contracts for configurable read-only Ecobee datapoints."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed_exact,
)

from custom_components.ecobee_unified.datapoint_entity import (
    UnifiedDatapointBinarySensor,
    UnifiedDatapointSensor,
)
from custom_components.ecobee_unified.datapoints import (
    DatapointConfig,
    DatapointManager,
    SourceBinding,
    datapoint_observation_role,
    validate_datapoint,
    validate_datapoint_edit_sources,
)
from custom_components.ecobee_unified.weather_source import WeatherSnapshot


def _source(
    hass: HomeAssistant,
    platform: str,
    serial: str = "SENSORA",
    *,
    kind: str = "humidity",
    unit: str | None = "%",
    domain: str = "sensor",
    device: dr.DeviceEntry | None = None,
    unique_id: str | None = None,
) -> er.RegistryEntry:
    config = MockConfigEntry(domain=platform)
    config.add_to_hass(hass)
    if device is None:
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=config.entry_id,
            identifiers={(platform, serial)},
            serial_number=serial,
            manufacturer="ecobee",
            model="EBERS41" if platform == "homekit_controller" else "Remote sensor",
        )
    return er.async_get(hass).async_get_or_create(
        domain,
        platform,
        unique_id or f"{serial}-{kind}",
        config_entry=config,
        device_id=device.id,
        original_device_class=kind,
        unit_of_measurement=unit,
    )


def _config(*sources: er.RegistryEntry, **changes: Any) -> DatapointConfig:
    values: dict[str, Any] = {
        "datapoint_id": "sensor_a_humidity",
        "name": "Unified humidity",
        "kind": "humidity",
        "unit": "%",
        "sources": tuple(SourceBinding(item.id) for item in sources),
    }
    values.update(changes)
    if values["kind"] == "duration":
        values.setdefault("semantic", "elapsed_duration")
    return DatapointConfig(**values)


async def test_typed_edit_requires_same_subject_and_all_normalized_roles(
    hass: HomeAssistant,
) -> None:
    first = _source(hass, "homekit_controller", domain="climate", unit=None)
    second = _source(hass, "ecobee", domain="climate", unit=None)
    device = dr.async_get(hass).async_get(second.device_id)
    assert isinstance(device, dr.DeviceEntry)
    measured = _source(hass, "ecobee", device=device)
    replacement = _source(
        hass, "ecobee", domain="climate", device=device, unique_id="replacement"
    )
    other = _source(hass, "ecobee", "OTHER", domain="climate", unit=None)
    for source in (first, second, replacement, other):
        hass.states.async_set(
            source.entity_id, "cool", {"current_humidity": 42, "humidity": 55}
        )
    hass.states.async_set(measured.entity_id, "42")
    original = _config(
        sources=(
            SourceBinding(first.id, "current_humidity"),
            SourceBinding(second.id, "current_humidity"),
        )
    )
    validate_datapoint(hass, original)
    for changed in (
        # A different registry UUID and entity form can prove the same measured role.
        (SourceBinding(measured.id), original.sources[0]),
        (SourceBinding(replacement.id, "current_humidity"), original.sources[1]),
    ):
        candidate = replace(original, sources=changed)
        validate_datapoint(hass, candidate)
        validate_datapoint_edit_sources(hass, original, candidate)
    target = SourceBinding(second.id, "humidity")
    for changed, reason in (
        ((original.sources[0], target), "source_observation_role_mismatch"),
        ((*original.sources, target), "source_observation_role_mismatch"),
        ((SourceBinding(first.id, "humidity"), target), "datapoint_meaning_change"),
        (
            (SourceBinding(other.id, "current_humidity"), original.sources[0]),
            "datapoint_meaning_change",
        ),
    ):
        with pytest.raises(ValueError, match=reason):
            validate_datapoint_edit_sources(
                hass, original, replace(original, sources=changed)
            )
    mixed = replace(original, sources=(*original.sources, target))
    with pytest.raises(ValueError, match="datapoint_meaning_change"):
        validate_datapoint_edit_sources(hass, mixed, original)


@pytest.mark.parametrize(
    "primary_status", ["cool", "unavailable", "missing", "disabled"]
)
async def test_known_humidity_contradiction_blocks_admission_and_saved_fallback(
    hass: HomeAssistant, primary_status: str
) -> None:
    first = _source(hass, "homekit_controller", domain="climate", unit=None)
    second = _source(hass, "ecobee", domain="climate", unit=None)
    if primary_status != "missing":
        hass.states.async_set(
            first.entity_id,
            "unavailable" if primary_status == "unavailable" else "cool",
            {"current_humidity": 42},
        )
    if primary_status == "disabled":
        er.async_get(hass).async_update_entity(
            first.entity_id, disabled_by=er.RegistryEntryDisabler.USER
        )
    hass.states.async_set(second.entity_id, "cool", {"humidity": 55})
    config = _config(
        sources=(
            SourceBinding(first.id, "current_humidity"),
            SourceBinding(second.id, "humidity"),
        )
    )
    for candidate in (config, replace(config, sources=tuple(reversed(config.sources)))):
        with pytest.raises(ValueError, match="source_observation_role_mismatch"):
            validate_datapoint(hass, candidate)
        with pytest.raises(ValueError, match="source_observation_role_mismatch"):
            validate_datapoint_edit_sources(hass, config, candidate)
        manager = DatapointManager(hass, "unified", (candidate,))
        await manager.async_start()
        snapshot = manager.snapshot(candidate.datapoint_id)
        assert snapshot.status == "source_observation_role_mismatch"
        assert not snapshot.available and snapshot.value is None
        assert snapshot.selected_source is None and not snapshot.fallback_used
        assert {item.status for item in snapshot.source_statuses} == {
            "source_observation_role_mismatch"
        }
        await manager.async_stop()


@pytest.mark.parametrize("attribute", ["current_humidity", "humidity"])
async def test_homogeneous_humidity_and_known_opaque_equivalence_preserve_fallback(
    hass: HomeAssistant, attribute: str
) -> None:
    first = _source(hass, "homekit_controller", domain="climate", unit=None)
    second = _source(hass, "ecobee", domain="climate", unit=None)
    hass.states.async_set(first.entity_id, "cool", {attribute: 42})
    hass.states.async_set(
        second.entity_id, "cool", {attribute: 55, "opaque_percent": 50}
    )
    for secondary in (
        SourceBinding(second.id, attribute),
        SourceBinding(second.id, "opaque_percent", unit="%"),
    ):
        config = _config(sources=(SourceBinding(first.id, attribute), secondary))
        validate_datapoint(hass, config)
        expected_role = (
            "measured_humidity"
            if attribute == "current_humidity"
            else "target_humidity"
        )
        assert datapoint_observation_role(hass, config) == (
            None if secondary.attribute == "opaque_percent" else expected_role
        )
        manager = DatapointManager(hass, "unified", (config,))
        await manager.async_start()
        assert manager.snapshot(config.datapoint_id).value == 42
        hass.states.async_set(first.entity_id, "unavailable")
        await hass.async_block_till_done()
        snapshot = manager.snapshot(config.datapoint_id)
        assert snapshot.available and snapshot.fallback_used
        assert snapshot.value == (50 if secondary.attribute == "opaque_percent" else 55)
        hass.states.async_set(first.entity_id, "cool", {attribute: 42})
        await hass.async_block_till_done()
        assert manager.snapshot(config.datapoint_id).value == 42
        await manager.async_stop()


async def test_complete_observation_role_requires_every_native_binding_and_subject(
    hass: HomeAssistant,
) -> None:
    first = _source(hass, "homekit_controller", domain="climate", unit=None)
    second = _source(hass, "ecobee", domain="climate", unit=None)
    config = _config(
        sources=(
            SourceBinding(first.id, "current_humidity"),
            SourceBinding(second.id, "current_humidity"),
        )
    )
    # Native roles remain known with no current source state; values are not proof.
    assert datapoint_observation_role(hass, config) == "measured_humidity"
    target = replace(
        config, sources=(config.sources[0], SourceBinding(second.id, "humidity"))
    )
    assert datapoint_observation_role(hass, target) is None
    assert second.device_id is not None
    devices = dr.async_get(hass)
    device = devices.async_get(second.device_id)
    assert isinstance(device, dr.DeviceEntry)
    devices.async_update_device(
        device.id, new_identifiers={("ecobee", "different_subject")}
    )
    assert datapoint_observation_role(hass, config) is None
    devices.async_update_device(device.id, new_identifiers={("ecobee", "SENSORA")})
    assert datapoint_observation_role(hass, config) == "measured_humidity"
    er.async_get(hass).async_remove(first.entity_id)
    assert datapoint_observation_role(hass, config) is None


@pytest.mark.parametrize("kind", ["humidity", "battery", "duration", "occupancy"])
async def test_opaque_typed_roles_require_same_bindings_despite_matching_metadata(
    hass: HomeAssistant, kind: str
) -> None:
    domain = "binary_sensor" if kind == "occupancy" else "sensor"
    unit = None if kind == "occupancy" else "min" if kind == "duration" else "%"
    first = _source(hass, "homekit_controller", kind=kind, unit=unit, domain=domain)
    second = _source(hass, "ecobee", kind=kind, unit=unit, domain=domain)
    device = dr.async_get(hass).async_get(first.device_id)
    assert isinstance(device, dr.DeviceEntry)
    replacement = _source(
        hass,
        "homekit_controller",
        kind=kind,
        unit=unit,
        domain=domain,
        device=device,
        unique_id="other_native_role",
    )
    for source in (first, second, replacement):
        hass.states.async_set(source.entity_id, "on" if kind == "occupancy" else "42")
    original = _config(first, second, kind=kind, unit=unit)
    validate_datapoint(hass, original)
    reordered = replace(original, sources=tuple(reversed(original.sources)))
    validate_datapoint_edit_sources(hass, original, reordered)
    candidate = replace(
        original, sources=(SourceBinding(replacement.id), original.sources[1])
    )
    validate_datapoint(hass, candidate)
    with pytest.raises(ValueError, match="datapoint_meaning_change"):
        validate_datapoint_edit_sources(hass, original, candidate)


async def test_unknown_attribute_and_missing_old_binding_cannot_authorize_replacement(
    hass: HomeAssistant,
) -> None:
    first = _source(hass, "homekit_controller", domain="climate", unit=None)
    second = _source(hass, "ecobee", domain="climate", unit=None)
    for source in (first, second):
        hass.states.async_set(
            source.entity_id, "cool", {"observed_percent": 42, "other_percent": 42}
        )
    original = _config(
        sources=(
            SourceBinding(first.id, "observed_percent", unit="%"),
            SourceBinding(second.id, "observed_percent", unit="%"),
        )
    )
    validate_datapoint(hass, original)
    validate_datapoint_edit_sources(
        hass, original, replace(original, sources=tuple(reversed(original.sources)))
    )
    changed = replace(
        original,
        sources=(
            SourceBinding(first.id, "other_percent", unit="%"),
            original.sources[1],
        ),
    )
    validate_datapoint(hass, changed)
    with pytest.raises(ValueError, match="datapoint_meaning_change"):
        validate_datapoint_edit_sources(hass, original, changed)
    # A same-subject replacement is still not proof of an absent old source's role.
    er.async_get(hass).async_remove(first.entity_id)
    replacement = _source(
        hass, "ecobee", domain="climate", unit=None, unique_id="replacement"
    )
    changed = replace(
        original,
        sources=(
            SourceBinding(replacement.id, "observed_percent", unit="%"),
            original.sources[1],
        ),
    )
    with pytest.raises(ValueError, match="datapoint_meaning_change"):
        validate_datapoint_edit_sources(hass, original, changed)


def _weather_sources(hass: HomeAssistant) -> tuple[er.RegistryEntry, er.RegistryEntry]:
    config = MockConfigEntry(domain="ecobee")
    config.add_to_hass(hass)
    entries = []
    for serial in ("100000000001", "100000000002"):
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=config.entry_id,
            identifiers={("ecobee", serial)},
        )
        entries.append(
            er.async_get(hass).async_get_or_create(
                "weather",
                "ecobee",
                serial,
                config_entry=config,
                device_id=device.id,
            )
        )
    return entries[0], entries[1]


def _weather_attributes(timestamp: datetime, **changes: Any) -> dict[str, Any]:
    return {
        "temperature": 20,
        "temperature_unit": "°C",
        "humidity": 50,
        "pressure": 1013,
        "pressure_unit": "hPa",
        "wind_speed": 4,
        "wind_speed_unit": "km/h",
        "wind_bearing": 90,
        "visibility": 10,
        "visibility_unit": "km",
        "precipitation_unit": "mm",
        "supported_features": 1,
        "attribution": f"Ecobee weather provided by Example Station at {timestamp:%Y-%m-%d %H:%M:%S} UTC",
    } | changes


async def test_primary_order_conversion_invalid_unit_fallback_and_recovery(
    hass: HomeAssistant,
) -> None:
    first = _source(hass, "homekit_controller", kind="temperature", unit="°F")
    second = _source(hass, "ecobee", kind="temperature", unit="°C")
    config = _config(
        first, second, kind="temperature", unit="°C", semantic="physical_temperature"
    )
    hass.states.async_set(first.entity_id, "68")
    hass.states.async_set(second.entity_id, "24")
    validate_datapoint(hass, config)
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    assert manager.snapshot(config.datapoint_id).value == 20
    assert not manager.snapshot(config.datapoint_id).fallback_used

    # Explicit invalid native metadata must not fall back to registry defaults.
    hass.states.async_set(first.entity_id, "68", {"unit_of_measurement": None})
    await hass.async_block_till_done()
    snapshot = manager.snapshot(config.datapoint_id)
    assert snapshot.value == 24
    assert snapshot.fallback_used
    assert snapshot.source_statuses[0].status == "source_unit_mismatch"
    hass.states.async_set(first.entity_id, "-1", {"unit_of_measurement": "K"})
    await hass.async_block_till_done()
    assert manager.snapshot(config.datapoint_id).value == 24
    assert (
        manager.snapshot(config.datapoint_id).source_statuses[0].status
        == "invalid_temperature"
    )
    hass.states.async_set(first.entity_id, "70", {"unit_of_measurement": "°F"})
    await hass.async_block_till_done()
    assert manager.snapshot(config.datapoint_id).value == pytest.approx(21.1111111)
    await manager.async_stop()


async def test_temperature_accepted_range_conversion_endpoints_fallback_and_recovery(
    hass: HomeAssistant,
) -> None:
    first = _source(hass, "homekit_controller", kind="temperature", unit="°F")
    second = _source(hass, "ecobee", kind="temperature", unit="°C")
    config = _config(
        first,
        second,
        kind="temperature",
        unit="°C",
        semantic="physical_temperature",
        minimum_value=15,
        maximum_value=30,
    )
    hass.states.async_set(first.entity_id, "86")
    hass.states.async_set(second.entity_id, "25")
    validate_datapoint(hass, config)
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    snapshot = manager.snapshot(config.datapoint_id)
    assert snapshot.available and snapshot.value == 30
    assert snapshot.selected_source == first.entity_id

    # Inclusive endpoints apply to converted values, not the source's raw unit.
    for primary, expected, fallback in (
        ("86.18", 25, True),
        ("59", 15, False),
        ("58.82", 25, True),
    ):
        hass.states.async_set(first.entity_id, primary)
        await hass.async_block_till_done()
        snapshot = manager.snapshot(config.datapoint_id)
        assert snapshot.available and snapshot.value == expected
        assert snapshot.fallback_used == fallback
        assert snapshot.selected_source == (
            second.entity_id if fallback else first.entity_id
        )
        assert snapshot.source_statuses[0].status == (
            "out_of_range" if fallback else "available"
        )

    hass.states.async_set(second.entity_id, "31")
    await hass.async_block_till_done()
    snapshot = manager.snapshot(config.datapoint_id)
    assert not snapshot.available
    assert snapshot.value is None and snapshot.selected_source is None
    assert snapshot.status == "no_usable_source"
    assert [item.status for item in snapshot.source_statuses] == [
        "out_of_range",
        "out_of_range",
    ]

    hass.states.async_set(first.entity_id, "77")
    await hass.async_block_till_done()
    snapshot = manager.snapshot(config.datapoint_id)
    assert snapshot.available and snapshot.value == 25
    assert snapshot.selected_source == first.entity_id
    assert not snapshot.fallback_used
    await manager.async_stop()


@pytest.mark.parametrize(
    ("native_unit", "output_unit", "raw", "field", "bound", "roundtrip"),
    [
        ("°F", "°C", "65.12", "maximum_value", 18.4, False),
        ("°F", "°C", "64.94", "minimum_value", 18.3, False),
        ("°F", "°C", "32.18", "minimum_value", 0.1, False),
        ("K", "°F", "0", "maximum_value", -459.67, False),
        ("°F", "K", "-459.67", "minimum_value", 0, False),
        ("°C", "°C", "18.3", "maximum_value", 18.3, True),
    ],
)
async def test_temperature_accepted_endpoints_survive_conversion_roundoff(
    hass: HomeAssistant,
    native_unit: str,
    output_unit: str,
    raw: str,
    field: str,
    bound: float,
    roundtrip: bool,
) -> None:
    first = _source(hass, "homekit_controller", kind="temperature", unit=native_unit)
    second = _source(hass, "ecobee", kind="temperature", unit=output_unit)
    if roundtrip:
        bound = TemperatureConverter.convert(
            TemperatureConverter.convert(bound, "°C", "°F"), "°F", "°C"
        )
    config = _config(
        first,
        second,
        kind="temperature",
        unit=output_unit,
        semantic="physical_temperature",
        **{field: bound},
    )
    hass.states.async_set(first.entity_id, raw)
    hass.states.async_set(second.entity_id, str(bound))
    validate_datapoint(hass, config)
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    snapshot = manager.snapshot(config.datapoint_id)
    assert snapshot.available and snapshot.selected_source == first.entity_id
    assert not snapshot.fallback_used
    assert snapshot.source_statuses[0].status == "available"
    # Endpoint equivalence never clips or rounds the published converted value.
    assert snapshot.value == TemperatureConverter.convert(
        float(raw), native_unit, output_unit
    )
    await manager.async_stop()


async def test_temperature_roundoff_guard_rejects_real_outside_values(
    hass: HomeAssistant,
) -> None:
    first = _source(hass, "homekit_controller", kind="temperature", unit="°C")
    second = _source(hass, "ecobee", kind="temperature", unit="°C")
    config = _config(
        first,
        second,
        kind="temperature",
        unit="°C",
        semantic="physical_temperature",
        minimum_value=18.3,
        maximum_value=18.4,
    )
    hass.states.async_set(first.entity_id, "18.35")
    hass.states.async_set(second.entity_id, "18.36")
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    for outside in ("18.299999999", "18.400000001"):
        hass.states.async_set(first.entity_id, outside)
        await hass.async_block_till_done()
        snapshot = manager.snapshot(config.datapoint_id)
        assert snapshot.available and snapshot.value == 18.36
        assert snapshot.fallback_used
        assert snapshot.source_statuses[0].status == "out_of_range"
    await manager.async_stop()


async def test_temperature_range_admission_disabled_fallback_and_default_off(
    hass: HomeAssistant,
) -> None:
    first = _source(hass, "homekit_controller", kind="temperature", unit="°C")
    second = _source(hass, "ecobee", kind="temperature", unit="°C")
    hass.states.async_set(first.entity_id, "100")
    hass.states.async_set(second.entity_id, "25")
    config = _config(
        first,
        second,
        kind="temperature",
        unit="°C",
        semantic="physical_temperature",
        minimum_value=15,
        maximum_value=30,
    )
    strict = replace(config, datapoint_id="strict", fallback=False)
    unbounded = replace(
        config, datapoint_id="unbounded", minimum_value=None, maximum_value=None
    )
    # A valid source outside user policy remains admissible, enabling fallback.
    for candidate in (config, strict, unbounded):
        validate_datapoint(hass, candidate)
    manager = DatapointManager(hass, "unified", (config, strict, unbounded))
    await manager.async_start()
    assert manager.snapshot(config.datapoint_id).value == 25
    assert manager.snapshot(config.datapoint_id).fallback_used
    snapshot = manager.snapshot(strict.datapoint_id)
    assert not snapshot.available and snapshot.value is None
    assert snapshot.selected_source is None and not snapshot.fallback_used
    assert [item.status for item in snapshot.source_statuses] == [
        "out_of_range",
        "available",
    ]
    snapshot = manager.snapshot(unbounded.datapoint_id)
    assert snapshot.available and snapshot.value == 100
    assert snapshot.selected_source == first.entity_id and not snapshot.fallback_used
    await manager.async_stop()


@pytest.mark.parametrize(
    ("minimum", "maximum", "outside"), [(15, None, "14"), (None, 30, "31")]
)
async def test_temperature_one_sided_accepted_range(
    hass: HomeAssistant, minimum: float | None, maximum: float | None, outside: str
) -> None:
    first = _source(hass, "homekit_controller", kind="temperature", unit="°C")
    second = _source(hass, "ecobee", kind="temperature", unit="°C")
    config = _config(
        first,
        second,
        kind="temperature",
        unit="°C",
        semantic="physical_temperature",
        minimum_value=minimum,
        maximum_value=maximum,
    )
    assert DatapointConfig.from_dict(config.as_dict()) == config
    hass.states.async_set(first.entity_id, outside)
    hass.states.async_set(second.entity_id, "20")
    validate_datapoint(hass, config)
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    snapshot = manager.snapshot(config.datapoint_id)
    assert snapshot.value == 20 and snapshot.fallback_used
    assert snapshot.source_statuses[0].status == "out_of_range"
    await manager.async_stop()


@pytest.mark.parametrize("field", ["minimum_value", "maximum_value"])
@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), "bad", []])
def test_temperature_range_rejects_invalid_bounds(field: str, value: Any) -> None:
    with pytest.raises(ValueError, match="invalid_accepted_range"):
        _config(
            kind="temperature",
            unit="°C",
            semantic="physical_temperature",
            sources=(SourceBinding("first"), SourceBinding("second")),
            **{field: value},
        )


@pytest.mark.parametrize("minimum,maximum", [(20, 20), (30, 20)])
def test_temperature_range_requires_increasing_bounds(
    minimum: float, maximum: float
) -> None:
    with pytest.raises(ValueError, match="invalid_accepted_range"):
        _config(
            kind="temperature",
            unit="°C",
            semantic="physical_temperature",
            sources=(SourceBinding("first"), SourceBinding("second")),
            minimum_value=minimum,
            maximum_value=maximum,
        )


@pytest.mark.parametrize(
    "unit,absolute_zero", [("°C", -273.15), ("°F", -459.67), ("K", 0)]
)
@pytest.mark.parametrize("field", ["minimum_value", "maximum_value"])
def test_temperature_bounds_cannot_be_below_absolute_zero(
    unit: str, absolute_zero: float, field: str
) -> None:
    config = _config(
        kind="temperature",
        unit=unit,
        semantic="physical_temperature",
        sources=(SourceBinding("first"), SourceBinding("second")),
        **{field: absolute_zero},
    )
    assert DatapointConfig.from_dict(config.as_dict()) == config
    with pytest.raises(ValueError, match="invalid_accepted_range"):
        replace(config, **{field: absolute_zero - 0.01})


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "humidity", "unit": "%"},
        {"semantic": "control_temperature"},
        {"time_basis": "interval", "interval_seconds": 300},
    ],
)
def test_accepted_range_requires_current_physical_temperature(
    changes: dict[str, Any],
) -> None:
    config = _config(
        kind="temperature",
        unit="°C",
        semantic="physical_temperature",
        sources=(
            SourceBinding("first", timestamp_attribute="measured_at"),
            SourceBinding("second", timestamp_attribute="measured_at"),
        ),
    )
    with pytest.raises(ValueError, match="accepted_range_not_supported"):
        replace(config, minimum_value=15, **changes)


def test_legacy_datapoint_without_range_fields_preserves_disabled_policy() -> None:
    config = _config(
        kind="temperature",
        unit="°C",
        semantic="physical_temperature",
        sources=(SourceBinding("first"), SourceBinding("second")),
    )
    saved = config.as_dict()
    del saved["minimum_value"]
    del saved["maximum_value"]
    assert DatapointConfig.from_dict(saved) == config
    assert config.minimum_value is None and config.maximum_value is None


async def test_identity_contradiction_blocks_both_sources_then_recovers(
    hass: HomeAssistant,
) -> None:
    first = _source(hass, "homekit_controller")
    second = _source(hass, "ecobee", "SENSORB")
    hass.states.async_set(first.entity_id, "40")
    hass.states.async_set(second.entity_id, "50")
    config = _config(first, second)
    with pytest.raises(ValueError, match="source_identity_mismatch"):
        validate_datapoint(hass, config)
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    assert not manager.snapshot(config.datapoint_id).available
    # Repair both native serial and native identifier, not a presentation name.
    assert second.device_id is not None
    dr.async_get(hass).async_update_device(
        second.device_id,
        serial_number="SENSORA",
        new_identifiers={("ecobee", "SENSORA")},
    )
    await hass.async_block_till_done()
    assert manager.snapshot(config.datapoint_id).value == 40
    await manager.async_stop()


async def test_registry_rename_rebinds_and_device_move_detaches_owned_helper(
    hass: HomeAssistant,
) -> None:
    first = _source(hass, "homekit_controller")
    second = _source(hass, "ecobee")
    hass.states.async_set(first.entity_id, "40")
    hass.states.async_set(second.entity_id, "50")
    config = _config(first, second)
    entry = MockConfigEntry(domain="ecobee_unified")
    entry.add_to_hass(hass)
    manager = DatapointManager(hass, entry.entry_id, (config,))
    await manager.async_start()
    sensor = UnifiedDatapointSensor(manager, config)
    assert sensor.device_entry is not None
    assert sensor.device_entry.id == first.device_id
    registry = er.async_get(hass)
    helper = registry.async_get_or_create(
        "sensor",
        "ecobee_unified",
        manager.unique_id(config.datapoint_id),
        config_entry=entry,
        device_id=first.device_id,
    )
    renamed = registry.async_update_entity(
        first.entity_id, new_entity_id="sensor.renamed_humidity"
    )
    hass.states.async_set(renamed.entity_id, "42")
    await hass.async_block_till_done()
    assert manager.snapshot(config.datapoint_id).selected_source == renamed.entity_id
    assert manager.snapshot(config.datapoint_id).value == 42
    registry.async_update_entity(renamed.entity_id, device_id=None)
    await hass.async_block_till_done()
    assert not manager.snapshot(config.datapoint_id).available
    assert registry.async_get(helper.entity_id).device_id is None
    registry.async_update_entity(renamed.entity_id, device_id=first.device_id)
    await hass.async_block_till_done()
    assert manager.snapshot(config.datapoint_id).available
    assert registry.async_get(helper.entity_id).device_id == first.device_id
    await manager.async_stop()


async def test_missing_state_fallback_is_configurable_and_removed_reference_not_rebound(
    hass: HomeAssistant,
) -> None:
    first = _source(hass, "homekit_controller")
    second = _source(hass, "ecobee")
    hass.states.async_set(second.entity_id, "50")
    config = _config(first, second)
    strict = replace(config, datapoint_id="strict", fallback=False)
    manager = DatapointManager(hass, "unified", (config, strict))
    await manager.async_start()
    assert manager.snapshot(config.datapoint_id).value == 50
    assert not manager.snapshot("strict").available
    hass.states.async_set(first.entity_id, "40")
    await hass.async_block_till_done()
    assert manager.snapshot("strict").value == 40
    er.async_get(hass).async_remove(first.entity_id)
    await hass.async_block_till_done()
    assert manager.snapshot(config.datapoint_id).value == 50
    assert manager.snapshot(config.datapoint_id).source_statuses[0].status == "missing"
    assert not manager.snapshot("strict").available
    await manager.async_stop()


async def test_freshness_boundary_unchanged_report_recovery_and_unload(
    hass: HomeAssistant,
    freezer: Any,
) -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    freezer.move_to(now)
    first = _source(hass, "homekit_controller")
    second = _source(hass, "ecobee")
    hass.states.async_set(first.entity_id, "40")
    hass.states.async_set(second.entity_id, "50")
    config = _config(first, second, max_age_seconds=30)
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    freezer.move_to(now + timedelta(seconds=31))
    async_fire_time_changed_exact(hass, now + timedelta(seconds=31))
    await hass.async_block_till_done()
    assert not manager.snapshot(config.datapoint_id).available
    assert {
        item.status for item in manager.snapshot(config.datapoint_id).source_statuses
    } == {"stale"}
    hass.states.async_set(first.entity_id, "40")
    await hass.async_block_till_done()
    assert manager.snapshot(config.datapoint_id).value == 40
    assert manager.snapshot(config.datapoint_id).reported_at == now + timedelta(
        seconds=31
    )
    await manager.async_stop()
    before = manager.snapshot(config.datapoint_id)
    hass.states.async_set(first.entity_id, "45")
    freezer.move_to(now + timedelta(minutes=2))
    async_fire_time_changed_exact(hass, now + timedelta(minutes=2))
    await hass.async_block_till_done()
    assert manager.snapshot(config.datapoint_id) == before


async def test_quiet_push_without_cutoff_does_not_become_stale(
    hass: HomeAssistant,
    freezer: Any,
) -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    freezer.move_to(now)
    first = _source(hass, "homekit_controller")
    second = _source(hass, "ecobee")
    hass.states.async_set(first.entity_id, "40")
    hass.states.async_set(second.entity_id, "50")
    config = _config(first, second)
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    before = manager.snapshot(config.datapoint_id)
    freezer.move_to(now + timedelta(days=2))
    async_fire_time_changed_exact(hass, now + timedelta(days=2))
    await hass.async_block_till_done()
    assert manager.snapshot(config.datapoint_id) == before
    assert before.available
    assert before.observed_at is None
    await manager.async_stop()


async def test_source_cutoffs_keep_quiet_local_valid_while_cloud_expires(
    hass: HomeAssistant,
    freezer: Any,
) -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    freezer.move_to(now)
    first = _source(hass, "homekit_controller")
    second = _source(hass, "ecobee")
    hass.states.async_set(first.entity_id, "40")
    hass.states.async_set(second.entity_id, "50")
    config = _config(
        max_age_seconds=30,
        sources=(
            SourceBinding(first.id, max_age_seconds=0),
            SourceBinding(second.id),
        ),
    )
    assert DatapointConfig.from_dict(config.as_dict()) == config
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    freezer.move_to(now + timedelta(seconds=31))
    async_fire_time_changed_exact(hass, now + timedelta(seconds=31))
    await hass.async_block_till_done()
    snapshot = manager.snapshot(config.datapoint_id)
    assert snapshot.value == 40
    assert snapshot.source_statuses[0].status == "available"
    assert snapshot.source_statuses[1].status == "stale"
    hass.states.async_set(first.entity_id, "unavailable")
    await hass.async_block_till_done()
    assert not manager.snapshot(config.datapoint_id).available
    hass.states.async_set(second.entity_id, "50")
    await hass.async_block_till_done()
    assert manager.snapshot(config.datapoint_id).value == 50
    assert manager.snapshot(config.datapoint_id).fallback_used
    await manager.async_stop()


async def test_interval_duration_preserves_time_basis_and_old_observation(
    hass: HomeAssistant,
    freezer: Any,
) -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    freezer.move_to(now)
    first = _source(hass, "ecobee", kind="duration", unit="s")
    device = dr.async_get(hass).async_get(first.device_id)
    assert isinstance(device, dr.DeviceEntry)
    second = _source(
        hass, "beestat_statistics", kind="duration", unit="min", device=device
    )
    old = now - timedelta(minutes=10)
    hass.states.async_set(first.entity_id, "120", {"interval_end": old.isoformat()})
    hass.states.async_set(second.entity_id, "3", {"interval_end": now.isoformat()})
    config = _config(
        kind="duration",
        unit="min",
        time_basis="interval",
        interval_seconds=300,
        max_age_seconds=600,
        sources=(
            SourceBinding(first.id, timestamp_attribute="interval_end"),
            SourceBinding(second.id, timestamp_attribute="interval_end"),
        ),
    )
    validate_datapoint(hass, config)
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    snapshot = manager.snapshot(config.datapoint_id)
    assert snapshot.value == 3
    assert snapshot.fallback_used
    assert snapshot.observed_at == now
    assert snapshot.source_statuses[0].status == "stale"
    sensor = UnifiedDatapointSensor(manager, config)
    assert sensor.state_class is None
    assert sensor.extra_state_attributes["time_basis"] == "interval"
    hass.states.async_set(first.entity_id, "120", {"interval_end": now.isoformat()})
    await hass.async_block_till_done()
    assert manager.snapshot(config.datapoint_id).value == 2
    await manager.async_stop()


async def test_boolean_contract_does_not_coerce_unknown_or_truthy_values(
    hass: HomeAssistant,
) -> None:
    first = _source(
        hass, "homekit_controller", domain="binary_sensor", kind="occupancy", unit=None
    )
    second = _source(
        hass, "ecobee", domain="binary_sensor", kind="occupancy", unit=None
    )
    hass.states.async_set(first.entity_id, "true")
    hass.states.async_set(second.entity_id, "off")
    config = _config(first, second, kind="occupancy", unit=None)
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    sensor = UnifiedDatapointBinarySensor(manager, config)
    assert sensor.is_on is False
    assert sensor.available
    assert (
        manager.snapshot(config.datapoint_id).source_statuses[0].status
        == "invalid_boolean"
    )
    hass.states.async_set(second.entity_id, "unknown")
    await hass.async_block_till_done()
    assert not sensor.available
    assert sensor.is_on is None
    await manager.async_stop()


async def test_climate_attributes_cannot_relabel_units_or_temperature_semantics(
    hass: HomeAssistant,
) -> None:
    first = _source(hass, "homekit_controller", domain="climate", unit="°F")
    second = _source(hass, "ecobee", domain="climate", unit="°C")
    hass.states.async_set(
        first.entity_id,
        "cool",
        {"current_temperature": 68, "unit_of_measurement": "°F"},
    )
    hass.states.async_set(
        second.entity_id,
        "cool",
        {"current_temperature": 20, "unit_of_measurement": "°C"},
    )
    config = _config(
        kind="temperature",
        unit="°C",
        semantic="control_temperature",
        sources=(
            SourceBinding(first.id, "current_temperature"),
            SourceBinding(second.id, "current_temperature"),
        ),
    )
    validate_datapoint(hass, config)
    with pytest.raises(ValueError, match="temperature_semantic_mismatch"):
        validate_datapoint(hass, replace(config, semantic="physical_temperature"))
    with pytest.raises(ValueError, match="source_unit_assertion_mismatch"):
        validate_datapoint(
            hass,
            replace(
                config,
                sources=(
                    SourceBinding(first.id, "current_temperature", unit="°C"),
                    config.sources[1],
                ),
            ),
        )

    # Native climate observations normally omit a unit attribute; HA owns it.
    hass.states.async_set(second.entity_id, "cool", {"current_temperature": 20})
    validate_datapoint(hass, config)
    hass.states.async_set(
        second.entity_id,
        "cool",
        {
            "current_temperature": 20,
            "unit_of_measurement": None,
        },
    )
    with pytest.raises(ValueError, match="source_unit_mismatch"):
        validate_datapoint(hass, config)


async def test_active_profile_uses_native_climate_mode_not_hold_preset(
    hass: HomeAssistant,
) -> None:
    first = _source(
        hass, "homekit_controller", kind="profile", domain="select", unit=None
    )
    first = er.async_get(hass).async_update_entity(
        first.entity_id,
        original_device_class=None,
        translation_key="ecobee_mode",
        capabilities={"options": ["home", "sleep", "away"]},
    )
    second = _source(hass, "ecobee", domain="climate", unit=None)
    hass.states.async_set(first.entity_id, "home", {"device_class": None})
    hass.states.async_set(
        second.entity_id,
        "cool",
        {
            "climate_mode": "home",
            "preset_mode": "temperature_hold",
        },
    )
    config = _config(
        kind="profile",
        unit=None,
        sources=(SourceBinding(first.id), SourceBinding(second.id, "climate_mode")),
    )
    validate_datapoint(hass, config)
    with pytest.raises(ValueError, match="invalid_profile_source"):
        validate_datapoint(
            hass,
            replace(
                config,
                sources=(
                    config.sources[0],
                    SourceBinding(second.id, "preset_mode"),
                ),
            ),
        )
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    sensor = UnifiedDatapointSensor(manager, config)
    assert sensor.extra_state_attributes["value_representation"] == "option"
    hass.states.async_set(first.entity_id, "unavailable", {"device_class": None})
    await hass.async_block_till_done()
    assert manager.snapshot(config.datapoint_id).value == "home"
    assert sensor.extra_state_attributes["value_representation"] == "label"
    await manager.async_stop()


async def test_disabled_reference_rejects_lingering_state_and_recovers(
    hass: HomeAssistant,
) -> None:
    first = _source(hass, "homekit_controller")
    second = _source(hass, "ecobee")
    hass.states.async_set(first.entity_id, "40")
    hass.states.async_set(second.entity_id, "50")
    config = _config(first, second)
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    registry = er.async_get(hass)
    registry.async_update_entity(
        first.entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert hass.states.get(first.entity_id) is not None
    with pytest.raises(ValueError, match="source_missing"):
        validate_datapoint(hass, config)
    snapshot = manager.snapshot(config.datapoint_id)
    assert snapshot.value == 50
    assert snapshot.fallback_used
    assert snapshot.source_statuses[0].status == "source_disabled"
    registry.async_update_entity(
        second.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
    )
    await hass.async_block_till_done()
    assert not manager.snapshot(config.datapoint_id).available
    registry.async_update_entity(first.entity_id, disabled_by=None)
    await hass.async_block_till_done()
    assert manager.snapshot(config.datapoint_id).value == 40
    assert not manager.snapshot(config.datapoint_id).fallback_used
    registry.async_update_entity(second.entity_id, disabled_by=None)
    await hass.async_block_till_done()
    validate_datapoint(hass, config)
    assert first.device_id is not None
    dr.async_get(hass).async_update_device(
        first.device_id, disabled_by=dr.DeviceEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert manager.snapshot(config.datapoint_id).value == 50
    with pytest.raises(ValueError, match="source_missing"):
        validate_datapoint(hass, config)
    dr.async_get(hass).async_update_device(first.device_id, disabled_by=None)
    await hass.async_block_till_done()
    assert manager.snapshot(config.datapoint_id).value == 40
    await manager.async_stop()


async def test_profile_labels_and_references_remain_distinct_across_fallback(
    hass: HomeAssistant,
) -> None:
    first = _source(hass, "ecobee", domain="climate", unit=None)
    device = dr.async_get(hass).async_get(first.device_id)
    assert isinstance(device, dr.DeviceEntry)
    second = _source(hass, "beestat_statistics", device=device, unit=None)
    second = er.async_get(hass).async_update_entity(
        second.entity_id,
        original_device_class=None,
        translation_key="current_comfort_profile",
    )
    hass.states.async_set(first.entity_id, "cool", {"climate_mode": "Daytime Comfort"})
    hass.states.async_set(
        second.entity_id, "Daytime Comfort", {"profile_ref": "smart1"}
    )
    config = _config(
        kind="profile",
        unit=None,
        sources=(
            SourceBinding(first.id, "climate_mode"),
            SourceBinding(second.id, "profile_ref"),
        ),
    )
    validate_datapoint(hass, config)
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    sensor = UnifiedDatapointSensor(manager, config)
    assert sensor.native_value == "Daytime Comfort"
    assert sensor.extra_state_attributes["value_representation"] == "label"
    hass.states.async_set(
        first.entity_id, "unavailable", {"climate_mode": "Daytime Comfort"}
    )
    await hass.async_block_till_done()
    assert sensor.native_value == "smart1"
    assert sensor.extra_state_attributes["value_representation"] == "reference"
    labels = replace(
        config,
        datapoint_id="profile_labels",
        sources=(config.sources[0], SourceBinding(second.id)),
    )
    validate_datapoint(hass, labels)
    label_manager = DatapointManager(hass, "labels", (labels,))
    await label_manager.async_start()
    assert label_manager.snapshot(labels.datapoint_id).value == "Daytime Comfort"
    assert label_manager.snapshot(labels.datapoint_id).value_representation == "label"
    await label_manager.async_stop()
    await manager.async_stop()


async def test_other_profile_looking_roles_are_rejected_at_selection_and_runtime(
    hass: HomeAssistant,
) -> None:
    first = _source(hass, "homekit_controller", domain="select", unit=None)
    registry = er.async_get(hass)
    first = registry.async_update_entity(first.entity_id, original_device_class=None)
    second = _source(hass, "ecobee", domain="climate", unit=None)
    hass.states.async_set(first.entity_id, "home", {"options": ["home", "away"]})
    hass.states.async_set(
        second.entity_id, "cool", {"climate_mode": "Home", "next_profile": "Away"}
    )
    config = _config(
        kind="profile",
        unit=None,
        sources=(SourceBinding(first.id), SourceBinding(second.id, "climate_mode")),
    )
    with pytest.raises(ValueError, match="invalid_profile_source"):
        validate_datapoint(hass, config)
    first = registry.async_update_entity(first.entity_id, translation_key="ecobee_mode")
    validate_datapoint(hass, config)
    with pytest.raises(ValueError, match="invalid_profile_source"):
        validate_datapoint(
            hass,
            replace(
                config,
                sources=(config.sources[0], SourceBinding(second.id, "next_profile")),
            ),
        )
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    registry.async_update_entity(first.entity_id, translation_key="other_mode")
    await hass.async_block_till_done()
    assert manager.snapshot(config.datapoint_id).value == "Home"
    assert (
        manager.snapshot(config.datapoint_id).source_statuses[0].status
        == "invalid_profile_source"
    )
    device = dr.async_get(hass).async_get(second.device_id)
    assert isinstance(device, dr.DeviceEntry)
    third = _source(hass, "beestat_statistics", device=device, unit=None)
    third = registry.async_update_entity(
        third.entity_id,
        original_device_class=None,
        translation_key="next_comfort_profile",
    )
    hass.states.async_set(third.entity_id, "Away", {"profile_ref": "away"})
    for attribute in (None, "profile_ref"):
        with pytest.raises(ValueError, match="invalid_profile_source"):
            validate_datapoint(
                hass,
                replace(
                    config,
                    sources=(
                        SourceBinding(second.id, "climate_mode"),
                        SourceBinding(third.id, attribute),
                    ),
                ),
            )
    await manager.async_stop()


async def test_native_number_fan_setting_is_not_elapsed_runtime(
    hass: HomeAssistant,
) -> None:
    first = _source(hass, "ecobee", domain="number", kind="duration", unit="min")
    first = er.async_get(hass).async_update_entity(
        first.entity_id, translation_key="fan_min_on_time"
    )
    second = _source(hass, "ecobee", domain="climate", unit=None)
    hass.states.async_set(first.entity_id, "15", {"device_class": None})
    hass.states.async_set(second.entity_id, "cool", {"fan_min_on_time": 15})
    config = _config(
        kind="duration",
        unit="min",
        semantic="minimum_fan_runtime_per_hour",
        sources=(SourceBinding(first.id), SourceBinding(second.id, "fan_min_on_time")),
    )
    validate_datapoint(hass, config)
    with pytest.raises(ValueError, match="duration_semantic_mismatch"):
        validate_datapoint(hass, replace(config, semantic="elapsed_duration"))
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    assert manager.snapshot(config.datapoint_id).value == 15
    hass.states.async_set(first.entity_id, "61", {"device_class": None})
    await hass.async_block_till_done()
    assert manager.snapshot(config.datapoint_id).fallback_used
    assert (
        manager.snapshot(config.datapoint_id).source_statuses[0].status
        == "invalid_fan_runtime"
    )
    await manager.async_stop()


async def test_ecobee_inc_same_device_sibling_does_not_need_cloud_registration(
    hass: HomeAssistant,
) -> None:
    first = _source(hass, "homekit_controller")
    assert first.device_id is not None
    device = dr.async_get(hass).async_update_device(
        first.device_id, manufacturer="ecobee Inc."
    )
    assert isinstance(device, dr.DeviceEntry)
    second = _source(hass, "beestat_statistics", device=device)
    hass.states.async_set(first.entity_id, "40")
    hass.states.async_set(second.entity_id, "50")
    config = _config(first, second)
    validate_datapoint(hass, config)
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    assert manager.snapshot(config.datapoint_id).value == 40
    await manager.async_stop()


async def test_custom_numeric_attribute_needs_explicit_unit_and_existing_attribute(
    hass: HomeAssistant,
) -> None:
    first = _source(hass, "homekit_controller")
    second = _source(hass, "ecobee")
    hass.states.async_set(first.entity_id, "40", {"reading": 40})
    hass.states.async_set(second.entity_id, "50", {"reading": 50})
    config = _config(
        sources=(
            SourceBinding(first.id, "reading"),
            SourceBinding(second.id, "reading"),
        )
    )
    with pytest.raises(ValueError, match="source_attribute_unit_required"):
        validate_datapoint(hass, config)
    valid = replace(
        config, sources=tuple(replace(item, unit="%") for item in config.sources)
    )
    validate_datapoint(hass, valid)
    hass.states.async_set(first.entity_id, "40")
    with pytest.raises(ValueError, match="source_attribute_missing"):
        validate_datapoint(hass, valid)


@pytest.mark.parametrize("platform", ["ecobee_unified", "demo"])
async def test_loop_and_unrelated_integration_are_rejected(
    hass: HomeAssistant,
    platform: str,
) -> None:
    first = _source(hass, platform)
    second = _source(hass, "ecobee")
    with pytest.raises(ValueError, match="unsupported_source_platform"):
        validate_datapoint(hass, _config(first, second))


@pytest.mark.parametrize("value", ["nan", "inf", "1e9999", "-1", "101"])
async def test_percentage_invalid_values_fall_back_without_clipping(
    hass: HomeAssistant,
    value: str,
) -> None:
    first = _source(hass, "homekit_controller")
    second = _source(hass, "ecobee")
    hass.states.async_set(first.entity_id, value)
    hass.states.async_set(second.entity_id, "50")
    config = _config(first, second)
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    assert manager.snapshot(config.datapoint_id).value == 50
    assert manager.snapshot(config.datapoint_id).fallback_used
    await manager.async_stop()


def test_saved_config_roundtrip_and_required_interval_observation() -> None:
    config = DatapointConfig(
        "humidity_a",
        "Humidity",
        "humidity",
        (SourceBinding("first"), SourceBinding("second")),
        unit="%",
        max_age_seconds=0,
    )
    assert DatapointConfig.from_dict(config.as_dict()) == config
    with pytest.raises(ValueError, match="interval_timestamp_required"):
        replace(config, time_basis="interval", interval_seconds=300)
    with pytest.raises(ValueError, match="invalid_max_age"):
        replace(config, max_age_seconds=float("inf"))
    with pytest.raises(ValueError, match="duplicate_datapoint_source"):
        replace(config, sources=(config.sources[0], config.sources[0]))


async def test_configured_membership_preserves_selected_labels_and_explicit_empty(
    hass: HomeAssistant,
) -> None:
    first = _source(hass, "ecobee", domain="climate", unit=None)
    device = dr.async_get(hass).async_get(first.device_id)
    assert isinstance(device, dr.DeviceEntry)
    second = _source(hass, "beestat_statistics", device=device, unit=None)
    hass.states.async_set(
        first.entity_id,
        "cool",
        {
            "active_sensors": ["Zone sensor", "Bedroom"],
            "in_use": 2,
        },
    )
    hass.states.async_set(
        second.entity_id,
        "Home",
        {
            "profile_sensors": ["Bedroom (remote)", "Thermostat"],
            "profile_ref": "home",
            "in_use": 2,
        },
    )
    config = _config(
        kind="configured_membership",
        unit=None,
        sources=(
            SourceBinding(first.id, "active_sensors"),
            SourceBinding(second.id, "profile_sensors"),
        ),
    )
    validate_datapoint(hass, config)
    with pytest.raises(ValueError, match="membership_attribute_required"):
        validate_datapoint(
            hass,
            replace(
                config, sources=(config.sources[0], SourceBinding(second.id, "in_use"))
            ),
        )
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    sensor = UnifiedDatapointSensor(manager, config)
    assert sensor.native_value == 2
    assert sensor.extra_state_attributes["members"] == ["Bedroom", "Zone sensor"]
    hass.states.async_set(first.entity_id, "unavailable")
    await hass.async_block_till_done()
    assert sensor.extra_state_attributes["members"] == [
        "Bedroom (remote)",
        "Thermostat",
    ]
    assert manager.snapshot(config.datapoint_id).fallback_used
    hass.states.async_set(first.entity_id, "cool", {"active_sensors": []})
    await hass.async_block_till_done()
    assert sensor.available
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes["members"] == []
    hass.states.async_set(
        first.entity_id, "cool", {"active_sensors": ["Bedroom", "Bedroom"]}
    )
    await hass.async_block_till_done()
    assert (
        manager.snapshot(config.datapoint_id).source_statuses[0].status
        == "invalid_membership"
    )
    assert sensor.native_value == 2
    hass.states.async_set(second.entity_id, "Home", {"profile_sensors": "Bedroom"})
    await hass.async_block_till_done()
    assert not sensor.available
    assert sensor.native_value is None
    await manager.async_stop()


async def test_membership_metadata_clock_survives_local_report_updates(
    hass: HomeAssistant,
    freezer: Any,
) -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    freezer.move_to(now)
    cloud = _source(hass, "ecobee", domain="climate", unit=None)
    device = dr.async_get(hass).async_get(cloud.device_id)
    assert isinstance(device, dr.DeviceEntry)
    spread = _source(hass, "beestat_statistics", device=device, unit=None)
    attributes = {
        "configured_sensor_names": ["Bedroom", "Thermostat"],
        "profile_ref": "home",
        "profile_name": "Home",
        "metadata_synced_at": now.isoformat(),
    }
    hass.states.async_set(spread.entity_id, "2.1", attributes)
    hass.states.async_set(
        cloud.entity_id,
        "cool",
        {
            "active_sensors": ["Cloud bedroom"],
            "climate_mode": "Away",
            "preset_mode": "temp",
        },
    )
    config = _config(
        kind="configured_membership",
        unit=None,
        sources=(
            SourceBinding(spread.id, "configured_sensor_names", max_age_seconds=30),
            SourceBinding(cloud.id, "active_sensors"),
        ),
    )
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    snapshot = manager.snapshot(config.datapoint_id)
    assert snapshot.observed_at == now
    assert snapshot.source_context.profile_ref == "home"
    assert snapshot.source_context.timing_quality == "metadata_observed"
    freezer.move_to(now + timedelta(seconds=31))
    hass.states.async_set(spread.entity_id, "2.2", attributes)
    async_fire_time_changed_exact(hass, now + timedelta(seconds=31))
    await hass.async_block_till_done()
    snapshot = manager.snapshot(config.datapoint_id)
    assert snapshot.fallback_used
    assert snapshot.source_statuses[0].status == "stale"
    assert snapshot.source_context.membership_basis == "native_active_preset"
    assert snapshot.source_context.preset_mode == "temp"
    assert snapshot.source_context.program_profile_name == "Away"
    assert snapshot.source_context.profile_name is None
    assert snapshot.source_context.timing_quality == "ha_report_only"
    attributes["metadata_synced_at"] = (now + timedelta(seconds=31)).isoformat()
    hass.states.async_set(spread.entity_id, "2.2", attributes)
    await hass.async_block_till_done()
    assert not manager.snapshot(config.datapoint_id).fallback_used
    await manager.async_stop()


async def test_profile_member_unknown_metadata_time_is_explicit_not_ha_freshness(
    hass: HomeAssistant,
) -> None:
    cloud = _source(hass, "ecobee", domain="climate", unit=None)
    device = dr.async_get(hass).async_get(cloud.device_id)
    assert isinstance(device, dr.DeviceEntry)
    profile = _source(hass, "beestat_statistics", device=device, unit=None)
    hass.states.async_set(cloud.entity_id, "unavailable")
    hass.states.async_set(
        profile.entity_id, "Home", {"profile_ref": "home", "profile_sensors": []}
    )
    config = _config(
        kind="configured_membership",
        unit=None,
        sources=(
            SourceBinding(profile.id, "profile_sensors"),
            SourceBinding(cloud.id, "active_sensors"),
        ),
    )
    bounded = replace(config, datapoint_id="bounded_members", max_age_seconds=60)
    manager = DatapointManager(hass, "unified", (config, bounded))
    await manager.async_start()
    snapshot = manager.snapshot(config.datapoint_id)
    assert snapshot.available and snapshot.value == ()
    assert snapshot.observed_at is None
    assert snapshot.status == "metadata_time_unknown"
    assert snapshot.source_context.profile_ref == "home"
    assert snapshot.source_context.profile_name == "Home"
    assert not manager.snapshot(bounded.datapoint_id).available
    assert (
        manager.snapshot(bounded.datapoint_id).source_statuses[0].status
        == "metadata_time_unknown"
    )
    hass.states.async_set(
        profile.entity_id, "Home", {"profile_ref": "home", "profile_sensors": []}
    )
    await hass.async_block_till_done()
    assert not manager.snapshot(bounded.datapoint_id).available
    await manager.async_stop()


async def test_weather_whole_snapshot_station_fallback_and_missing_primary(
    hass: HomeAssistant,
) -> None:
    first, second = _weather_sources(hass)
    issued = datetime(2026, 9, 1, tzinfo=UTC)
    hass.states.async_set(first.entity_id, "sunny", _weather_attributes(issued))
    hass.states.async_set(
        second.entity_id, "cloudy", _weather_attributes(issued, temperature=25)
    )
    config = _config(
        first,
        second,
        kind="weather",
        unit=None,
        weather_station="Example Station",
        weather_config_entry_id=first.config_entry_id,
    )
    assert DatapointConfig.from_dict(config.as_dict()) == config
    validate_datapoint(hass, config)
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    snapshot = manager.snapshot(config.datapoint_id)
    assert isinstance(snapshot.value, WeatherSnapshot)
    assert snapshot.value.temperature == 20
    assert snapshot.value.condition == "sunny"
    assert snapshot.value.wind_speed_unit == "km/h"
    assert snapshot.observed_at is None
    assert snapshot.source_context.provider_reported_at == issued
    attrs = _weather_attributes(issued)
    attrs["attribution"] = attrs["attribution"].replace(
        "Example Station", "Other Station"
    )
    hass.states.async_set(first.entity_id, "sunny", attrs)
    await hass.async_block_till_done()
    snapshot = manager.snapshot(config.datapoint_id)
    assert snapshot.fallback_used
    assert snapshot.value.condition == "cloudy"
    assert snapshot.value.temperature == 25
    assert snapshot.source_statuses[0].status == "weather_station_mismatch"
    er.async_get(hass).async_remove(first.entity_id)
    await hass.async_block_till_done()
    assert manager.snapshot(config.datapoint_id).available
    assert manager.snapshot(config.datapoint_id).selected_source == second.entity_id
    await manager.async_stop()


async def test_weather_edit_rejects_new_provider_entry_even_at_same_station(
    hass: HomeAssistant,
) -> None:
    first, second = _weather_sources(hass)
    issued = dt_util.utcnow()
    for source in (first, second):
        hass.states.async_set(source.entity_id, "sunny", _weather_attributes(issued))
    original = _config(
        first,
        second,
        kind="weather",
        unit=None,
        weather_station="Example Station",
        weather_config_entry_id=first.config_entry_id,
    )
    validate_datapoint(hass, original)
    provider = MockConfigEntry(domain="ecobee")
    provider.add_to_hass(hass)
    replacements = []
    for serial in ("200000000001", "200000000002"):
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=provider.entry_id,
            identifiers={("ecobee", serial)},
        )
        replacement = er.async_get(hass).async_get_or_create(
            "weather",
            "ecobee",
            serial,
            config_entry=provider,
            device_id=device.id,
        )
        hass.states.async_set(
            replacement.entity_id, "sunny", _weather_attributes(issued)
        )
        replacements.append(SourceBinding(replacement.id))
    changed = replace(
        original,
        weather_config_entry_id=provider.entry_id,
        sources=tuple(replacements),
    )
    validate_datapoint(hass, changed)
    with pytest.raises(ValueError, match="datapoint_meaning_change"):
        validate_datapoint_edit_sources(hass, original, changed)


async def test_weather_scalar_temperature_and_native_device_proof(
    hass: HomeAssistant,
) -> None:
    first, second = _weather_sources(hass)
    issued = datetime(2026, 9, 1, tzinfo=UTC)
    hass.states.async_set(
        first.entity_id,
        "sunny",
        _weather_attributes(issued, temperature=68, temperature_unit="°F"),
    )
    hass.states.async_set(
        second.entity_id, "sunny", _weather_attributes(issued, temperature=25)
    )
    config = _config(
        kind="temperature",
        unit="°C",
        semantic="weather_temperature",
        weather_station="Example Station",
        weather_config_entry_id=first.config_entry_id,
        sources=(
            SourceBinding(first.id, "temperature"),
            SourceBinding(second.id, "temperature"),
        ),
    )
    validate_datapoint(hass, config)
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    sensor = UnifiedDatapointSensor(manager, config)
    assert sensor.device_entry is None
    assert sensor.native_value == 20
    assert first.device_id is not None
    dr.async_get(hass).async_update_device(
        first.device_id, new_identifiers={("ecobee", "wrong_thermostat")}
    )
    await hass.async_block_till_done()
    assert sensor.native_value == 25
    assert (
        manager.snapshot(config.datapoint_id).source_statuses[0].status
        == "weather_device_identity_mismatch"
    )
    with pytest.raises(ValueError, match="weather_device_identity_mismatch"):
        validate_datapoint(hass, config)
    await manager.async_stop()


async def test_weather_provider_clock_not_ha_heartbeat_controls_freshness(
    hass: HomeAssistant,
    freezer: Any,
) -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    freezer.move_to(now)
    first, second = _weather_sources(hass)
    attrs = _weather_attributes(now)
    hass.states.async_set(first.entity_id, "sunny", attrs)
    hass.states.async_set(second.entity_id, "cloudy", attrs)
    config = _config(
        first,
        second,
        kind="weather",
        unit=None,
        max_age_seconds=30,
        weather_station="Example Station",
        weather_config_entry_id=first.config_entry_id,
    )
    manager = DatapointManager(hass, "unified", (config,))
    await manager.async_start()
    freezer.move_to(now + timedelta(seconds=20))
    hass.states.async_set(
        second.entity_id, "cloudy", _weather_attributes(now + timedelta(seconds=20))
    )
    await hass.async_block_till_done()
    freezer.move_to(now + timedelta(seconds=31))
    hass.states.async_set(first.entity_id, "sunny", attrs)
    async_fire_time_changed_exact(hass, now + timedelta(seconds=31))
    await hass.async_block_till_done()
    snapshot = manager.snapshot(config.datapoint_id)
    assert snapshot.fallback_used
    assert snapshot.source_statuses[0].status == "stale"
    assert snapshot.source_context.provider_reported_at == now + timedelta(seconds=20)
    hass.states.async_set(
        first.entity_id, "sunny", _weather_attributes(now + timedelta(seconds=31))
    )
    await hass.async_block_till_done()
    assert not manager.snapshot(config.datapoint_id).fallback_used
    await manager.async_stop()
