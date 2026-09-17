"""Keep measurement meaning stable after native SQLite statistics exist."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import (
    Recorder,
    get_instance,
    history,
    statistics,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util.unit_system import METRIC_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
    do_adhoc_statistics,
)

from custom_components.ecobee_unified.config_flow import _datapoint_form_defaults
from custom_components.ecobee_unified.const import CONF_MAPPINGS, DOMAIN
from custom_components.ecobee_unified.datapoints import DatapointConfig, SourceBinding
from custom_components.ecobee_unified.history_source import (
    async_capture_source,
    async_validate_source,
)

pytestmark = [
    pytest.mark.usefixtures("recorder_mock"),
    pytest.mark.parametrize("persistent_database", [True]),
    pytest.mark.freeze_time("2026-09-01T12:55:00+00:00"),
]

START = datetime(2026, 9, 1, 12, tzinfo=UTC)


@pytest.fixture
def mock_recorder_before_hass(recorder_db_url: str) -> None:
    """Resolve the native SQLite fixture before auto-enabled integrations use HA."""


@dataclass
class Sources:
    """Only isolated HA registry/state sources; no provider integrations run."""

    primary: er.RegistryEntry
    secondary: er.RegistryEntry
    batteries: tuple[er.RegistryEntry, er.RegistryEntry]
    replacements: tuple[er.RegistryEntry, er.RegistryEntry]
    other_thermostat: tuple[er.RegistryEntry, er.RegistryEntry]

    def form(self, kind: str, **changes: Any) -> dict[str, Any]:
        battery = kind == "battery"
        return {
            "name": f"Recorded {kind}",
            "kind": kind,
            "unit": "°C" if kind == "temperature" else "%",
            "semantic": "control_temperature",
            "time_basis": "current",
            "max_age_seconds": 0,
            "fallback": True,
            "primary_entity": self.batteries[0].entity_id
            if battery
            else self.primary.entity_id,
            "secondary_entity": self.batteries[1].entity_id
            if battery
            else self.secondary.entity_id,
            "primary_attribute": "" if battery else f"current_{kind}",
            "secondary_attribute": "" if battery else f"current_{kind}",
            "confirm_equivalence": True,
            **changes,
        }


@pytest.fixture
async def sources(hass: HomeAssistant, recorder_mock: Recorder) -> Sources:
    """Create two proven thermostat surfaces plus a same-device battery mirror."""
    hass.config.units = METRIC_SYSTEM
    assert await async_setup_component(hass, "sensor", {})
    registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    records = []
    replacements = []
    other_thermostat = []
    devices = []
    entries = []
    for platform in ("homekit_controller", "ecobee"):
        source_entry = MockConfigEntry(domain=platform)
        source_entry.add_to_hass(hass)
        device = device_registry.async_get_or_create(
            config_entry_id=source_entry.entry_id,
            identifiers={(platform, "thermostat_a")},
            serial_number="thermostat_a",
            manufacturer="ecobee Inc.",
        )
        entry = registry.async_get_or_create(
            "climate",
            platform,
            f"{platform}_thermostat_a",
            config_entry=source_entry,
            device_id=device.id,
        )
        _thermostat_state(hass, entry.entity_id, 20 if not records else 21)
        replacement = registry.async_get_or_create(
            "climate",
            platform,
            f"{platform}_thermostat_a_replacement",
            config_entry=source_entry,
            device_id=device.id,
        )
        _thermostat_state(
            hass,
            replacement.entity_id,
            23 if not records else 24,
            humidity=46 if not records else 47,
        )
        other_device = device_registry.async_get_or_create(
            config_entry_id=source_entry.entry_id,
            identifiers={(platform, "thermostat_b")},
            serial_number="thermostat_b",
            manufacturer="ecobee Inc.",
        )
        other = registry.async_get_or_create(
            "climate",
            platform,
            f"{platform}_thermostat_b",
            config_entry=source_entry,
            device_id=other_device.id,
        )
        _thermostat_state(hass, other.entity_id, 24 if not records else 25)
        replacements.append(replacement)
        other_thermostat.append(other)
        records.append(entry)
        devices.append(device)
        entries.append(source_entry)
    batteries = []
    for platform in ("homekit_controller", "battery_notes"):
        source_entry = (
            entries[0]
            if platform == "homekit_controller"
            else MockConfigEntry(domain=platform)
        )
        if platform == "battery_notes":
            source_entry.add_to_hass(hass)
        entry = registry.async_get_or_create(
            "sensor",
            platform,
            f"{platform}_battery_a",
            config_entry=source_entry,
            device_id=devices[0].id,
            original_device_class="battery",
            unit_of_measurement="%",
        )
        hass.states.async_set(
            entry.entity_id,
            "87",
            {"device_class": "battery", "unit_of_measurement": "%"},
        )
        batteries.append(entry)
    await async_wait_recording_done(hass)
    return Sources(
        records[0],
        records[1],
        (batteries[0], batteries[1]),
        (replacements[0], replacements[1]),
        (other_thermostat[0], other_thermostat[1]),
    )


@pytest.fixture
async def physical_temperature_sources(
    hass: HomeAssistant, recorder_mock: Recorder
) -> tuple[er.RegistryEntry, er.RegistryEntry]:
    """Keep native room-probe meaning separate from thermostat control readings."""
    hass.config.units = METRIC_SYSTEM
    assert await async_setup_component(hass, "sensor", {})
    registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    records = []
    for platform in ("homekit_controller", "ecobee"):
        source_entry = MockConfigEntry(domain=platform)
        source_entry.add_to_hass(hass)
        device = device_registry.async_get_or_create(
            config_entry_id=source_entry.entry_id,
            identifiers={(platform, "room_probe_a")},
            serial_number="room_probe_a",
            manufacturer="ecobee Inc.",
            model="EBERS41" if platform == "homekit_controller" else "Remote sensor",
        )
        source = registry.async_get_or_create(
            "sensor",
            platform,
            "room_probe_a-temperature",
            config_entry=source_entry,
            device_id=device.id,
            original_device_class="temperature",
            unit_of_measurement="°C",
        )
        _physical_temperature_state(hass, source.entity_id, 20 if not records else 21)
        records.append(source)
    await async_wait_recording_done(hass)
    return records[0], records[1]


def _physical_temperature_state(
    hass: HomeAssistant, entity_id: str, temperature: float
) -> None:
    hass.states.async_set(
        entity_id,
        str(temperature),
        {
            "device_class": "temperature",
            "unit_of_measurement": "°C",
            "state_class": "measurement",
        },
    )


def _thermostat_state(
    hass: HomeAssistant, entity_id: str, temperature: float, *, humidity: float = 42
) -> None:
    hass.states.async_set(
        entity_id,
        "heat",
        {
            "current_temperature": temperature,
            "current_humidity": humidity,
            "humidity": 55,
            "unit_of_measurement": "°C",
            "temperature": 22,
            "hvac_modes": ["heat", "off"],
            "hvac_action": "idle",
            "supported_features": 1,
        },
    )


async def _setup(
    hass: HomeAssistant, sources: Sources, kind: str
) -> tuple[MockConfigEntry, str]:
    config = DatapointConfig(
        datapoint_id="recorded_channel",
        name=f"Recorded {kind}",
        kind=kind,
        sources=(
            SourceBinding(sources.primary.id, attribute=f"current_{kind}"),
            SourceBinding(sources.secondary.id, attribute=f"current_{kind}"),
        ),
        unit="°C" if kind == "temperature" else "%",
        semantic="control_temperature" if kind == "temperature" else None,
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        version=1,
        minor_version=5,
        data={CONF_MAPPINGS: [], "datapoints": [config.as_dict()]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await async_wait_recording_done(hass)
    return entry, _entity_id(hass, entry, config.datapoint_id)


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry, datapoint_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, entry.runtime_data.datapoints.unique_id(datapoint_id)
    )
    assert entity_id is not None
    return entity_id


async def _form(
    hass: HomeAssistant, entry: MockConfigEntry, edit_id: str | None
) -> dict[str, Any]:
    flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"],
        {"next_step_id": "datapoint_edit" if edit_id else "datapoint_add"},
    )
    if edit_id:
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"datapoint_id": edit_id}
        )
    assert flow["type"] is FlowResultType.FORM
    return flow


async def _save(
    hass: HomeAssistant, flow: dict[str, Any], values: dict[str, Any]
) -> None:
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], values)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "reconfigure_finish"}
    )
    assert result["reason"] == "reconfigure_successful"
    # Await the real unload/reload and real Recorder commits; no reload stub.
    await async_wait_recording_done(hass)


async def _compile_hour(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hour: int
) -> None:
    freezer.move_to(START + timedelta(hours=hour + 1, seconds=1))
    await async_wait_recording_done(hass)
    do_adhoc_statistics(hass, start=START + timedelta(hours=hour, minutes=55))
    await async_wait_recording_done(hass)


async def _statistics(
    hass: HomeAssistant, entity_ids: list[str], hours: int = 1
) -> dict[str, Any]:
    response = await hass.services.async_call(
        "recorder",
        "get_statistics",
        {
            "statistic_ids": entity_ids,
            "start_time": START.isoformat(),
            "end_time": (START + timedelta(hours=hours)).isoformat(),
            "period": "hour",
            "types": ["mean", "min", "max"],
            "units": {"temperature": "°C"},
        },
        blocking=True,
        return_response=True,
    )
    assert response is not None
    return response["statistics"]


async def _metadata(hass: HomeAssistant, entity_ids: list[str]) -> dict[str, Any]:
    return await get_instance(hass).async_add_executor_job(
        partial(statistics.get_metadata, hass, statistic_ids=set(entity_ids))
    )


async def _history(
    hass: HomeAssistant, entity_ids: list[str], hours: int = 1
) -> dict[str, Any]:
    raw = await get_instance(hass).async_add_executor_job(
        partial(
            history.get_significant_states,
            hass,
            START,
            START + timedelta(hours=hours),
            entity_ids,
            include_start_time_state=False,
            significant_changes_only=False,
        )
    )
    return {
        entity_id: [
            (state.state, dict(state.attributes), state.last_updated)
            for state in states
            if isinstance(state, State)
        ]
        for entity_id, states in raw.items()
    }


async def test_physical_temperature_range_edit_preserves_recorded_identity_and_fallback(
    hass: HomeAssistant,
    physical_temperature_sources: tuple[er.RegistryEntry, er.RegistryEntry],
    freezer: FrozenDateTimeFactory,
    recorder_db_url: str,
) -> None:
    """A saved acceptance policy rejects before selection without replacing history."""
    primary, secondary = physical_temperature_sources
    config = DatapointConfig(
        datapoint_id="recorded_room_temperature",
        name="Recorded room temperature",
        kind="temperature",
        sources=(SourceBinding(primary.id), SourceBinding(secondary.id)),
        unit="°C",
        semantic="physical_temperature",
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        version=1,
        minor_version=5,
        data={CONF_MAPPINGS: [], "datapoints": [config.as_dict()]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await async_wait_recording_done(hass)
    entity_id = _entity_id(hass, entry, config.datapoint_id)
    await _compile_hour(hass, freezer, 0)
    assert recorder_db_url.startswith("sqlite:///")
    database = Path(recorder_db_url.removeprefix("sqlite:///"))
    assert (await hass.async_add_executor_job(database.stat)).st_size > 0
    metadata = await _metadata(hass, [entity_id])
    recorded = await _statistics(hass, [entity_id])
    recorded_history = await _history(hass, [entity_id])
    assert recorded[entity_id][0]["mean"] == 20
    assert recorded_history[entity_id]
    registry_entry = er.async_get(hass).async_get(entity_id)
    assert registry_entry is not None
    original_identity = (
        registry_entry.id,
        registry_entry.unique_id,
        registry_entry.device_id,
    )

    freezer.move_to(START + timedelta(hours=1, minutes=5))
    values = _datapoint_form_defaults(hass, entry.data["datapoints"][0]) | {
        "minimum_value": 5,
        "maximum_value": 30,
        "confirm_equivalence": True,
    }
    await _save(hass, await _form(hass, entry, config.datapoint_id), values)
    saved = entry.data["datapoints"][0]
    assert saved["datapoint_id"] == config.datapoint_id
    assert saved["minimum_value"] == 5
    assert saved["maximum_value"] == 30
    assert _entity_id(hass, entry, config.datapoint_id) == entity_id
    assert float(hass.states.get(entity_id).state) == 20

    fallback_time = START + timedelta(hours=1, minutes=10)
    freezer.move_to(fallback_time)
    _physical_temperature_state(hass, primary.entity_id, 100)
    await async_wait_recording_done(hass)
    state = hass.states.get(entity_id)
    assert state is not None
    assert float(state.state) == 21
    assert state.attributes["source"] == secondary.entity_id
    assert state.attributes["fallback_used"] is True
    await _compile_hour(hass, freezer, 1)

    unavailable_time = START + timedelta(hours=2, minutes=5)
    freezer.move_to(unavailable_time)
    _physical_temperature_state(hass, secondary.entity_id, -10)
    await async_wait_recording_done(hass)
    assert hass.states.get(entity_id).state == "unavailable"
    snapshot = entry.runtime_data.datapoints.snapshot(config.datapoint_id)
    assert snapshot.value is None
    assert snapshot.selected_source is None
    assert snapshot.status == "no_usable_source"
    assert [source.status for source in snapshot.source_statuses] == [
        "out_of_range",
        "out_of_range",
    ]
    await _compile_hour(hass, freezer, 2)

    recovery_time = START + timedelta(hours=3, minutes=5)
    freezer.move_to(recovery_time)
    _physical_temperature_state(hass, primary.entity_id, 22)
    await async_wait_recording_done(hass)
    state = hass.states.get(entity_id)
    assert state is not None
    assert float(state.state) == 22
    assert state.attributes["source"] == primary.entity_id
    assert state.attributes["fallback_used"] is False
    await _compile_hour(hass, freezer, 3)

    current_registry = er.async_get(hass).async_get(entity_id)
    assert current_registry is not None
    assert (
        current_registry.id,
        current_registry.unique_id,
        current_registry.device_id,
    ) == original_identity
    assert await _metadata(hass, [entity_id]) == metadata
    assert await _statistics(hass, [entity_id]) == recorded
    assert await _history(hass, [entity_id]) == recorded_history
    rows = (await _statistics(hass, [entity_id], 4))[entity_id]
    assert [row["mean"] for row in rows] == [20, 21, 22]
    assert [row["min"] for row in rows] == [20, 21, 22]
    assert [row["max"] for row in rows] == [20, 21, 22]
    recorded_states = (await _history(hass, [entity_id], 4))[entity_id]
    fallback_states = [
        (value, attributes)
        for value, attributes, updated in recorded_states
        if fallback_time <= updated < unavailable_time
    ]
    assert fallback_states
    assert {float(value) for value, _ in fallback_states} == {21}
    assert {attributes["source"] for _, attributes in fallback_states} == {
        secondary.entity_id
    }
    unavailable_states = [
        value
        for value, _, updated in recorded_states
        if unavailable_time <= updated < recovery_time
    ]
    assert unavailable_states
    assert set(unavailable_states) == {"unavailable"}
    assert {
        float(value)
        for value, _, _ in recorded_states
        if value not in {"unavailable", "unknown"}
    } == {20, 21, 22}
    assert not any(
        issue.translation_key == "units_changed"
        for issue in ir.async_get(hass).issues.values()
    )


@pytest.mark.parametrize("edit", [False, True], ids=["add", "edit"])
async def test_mixed_humidity_form_rejected_after_statistics_exist(
    hass: HomeAssistant,
    sources: Sources,
    freezer: FrozenDateTimeFactory,
    edit: bool,
) -> None:
    """Explicit equivalence cannot admit contradictory native humidity roles."""
    entry, entity_id = await _setup(hass, sources, "humidity")
    await _compile_hour(hass, freezer, 0)
    metadata = await _metadata(hass, [entity_id])
    recorded = await _statistics(hass, [entity_id])
    recorded_history = await _history(hass, [entity_id])
    assert recorded[entity_id][0]["mean"] == 42
    assert recorded_history[entity_id]
    original = deepcopy(dict(entry.data))
    manager = entry.runtime_data.datapoints
    registry_entry = er.async_get(hass).async_get(entity_id)
    assert registry_entry is not None
    assert registry_entry.device_id is not None
    device_entry = dr.async_get(hass).async_get(registry_entry.device_id)
    assert device_entry is not None

    freezer.move_to(START + timedelta(hours=1, minutes=5))
    flow = await _form(hass, entry, "recorded_channel" if edit else None)
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"],
        sources.form(
            "humidity",
            name="Recorded humidity" if edit else "Mixed humidity",
            secondary_attribute="humidity",
        ),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "source_observation_role_mismatch"}
    await async_wait_recording_done(hass)
    assert entry.data == original
    assert entry.runtime_data.datapoints is manager
    assert _entity_id(hass, entry, "recorded_channel") == entity_id
    assert er.async_get(hass).async_get(entity_id) == registry_entry
    assert dr.async_get(hass).async_get(registry_entry.device_id) == device_entry
    assert await _metadata(hass, [entity_id]) == metadata
    assert await _statistics(hass, [entity_id]) == recorded
    assert await _history(hass, [entity_id]) == recorded_history
    hass.config_entries.flow.async_abort(flow["flow_id"])


async def test_retained_mixed_humidity_cannot_extend_recorded_measurements(
    hass: HomeAssistant,
    sources: Sources,
    freezer: FrozenDateTimeFactory,
    recorder_db_url: str,
) -> None:
    """A legacy mixed row stays unavailable without rewriting its earlier series."""
    entry, entity_id = await _setup(hass, sources, "humidity")
    await _compile_hour(hass, freezer, 0)
    assert recorder_db_url.startswith("sqlite:///")
    database = Path(recorder_db_url.removeprefix("sqlite:///"))
    assert (await hass.async_add_executor_job(database.stat)).st_size > 0
    metadata = await _metadata(hass, [entity_id])
    recorded = await _statistics(hass, [entity_id])
    recorded_history = await _history(hass, [entity_id])
    assert len(recorded[entity_id]) == 1
    assert recorded[entity_id][0]["mean"] == 42
    assert recorded_history[entity_id]
    registry_entry = er.async_get(hass).async_get(entity_id)
    assert registry_entry is not None
    assert registry_entry.device_id is not None
    device_entry = dr.async_get(hass).async_get(registry_entry.device_id)
    assert device_entry is not None

    # Model a pre-existing saved row only after genuine HA/Recorder writes.
    # These sources share one device, so changing priority cannot relink it.
    legacy = deepcopy(dict(entry.data))
    legacy["datapoints"][0]["sources"] = [
        {"entity": sources.primary.id, "attribute": "current_humidity"},
        {"entity": sources.replacements[0].id, "attribute": "humidity"},
    ]
    legacy["datapoints"][0]["future_field"] = {"preserve": True}
    observations: dict[str, dict[str, Any]] = {}

    async def load_saved(data: dict[str, Any]) -> None:
        assert await hass.config_entries.async_unload(entry.entry_id)
        await async_wait_recording_done(hass)
        hass.config_entries.async_update_entry(entry, data=deepcopy(data))
        assert await hass.config_entries.async_setup(entry.entry_id)
        await async_wait_recording_done(hass)
        assert entry.data == data
        assert _entity_id(hass, entry, "recorded_channel") == entity_id
        current = er.async_get(hass).async_get(entity_id)
        assert current is not None
        assert (current.id, current.unique_id, current.device_id) == (
            registry_entry.id,
            registry_entry.unique_id,
            registry_entry.device_id,
        )
        assert dr.async_get(hass).async_get(registry_entry.device_id) == device_entry

    async def observe(stage: str) -> None:
        await async_wait_recording_done(hass)
        state = hass.states.get(entity_id)
        assert state is not None
        snapshot = entry.runtime_data.datapoints.snapshot("recorded_channel")
        # HA suppresses extra attributes while unavailable; inspect provenance
        # through the loaded manager without weakening the native state check.
        observations[stage] = {
            "state": state.state,
            "quality": snapshot.status,
            "source": snapshot.selected_source,
            "fallback_used": snapshot.fallback_used,
            "device_class": state.attributes["device_class"],
            "unit_of_measurement": state.attributes["unit_of_measurement"],
        }

    transition = START + timedelta(hours=1, minutes=5)
    freezer.move_to(transition)
    await load_saved(legacy)
    await observe("loaded")

    # An unchanged set with reversed priority must not bypass the native form.
    flow = await _form(hass, entry, "recorded_channel")
    reordered = await hass.config_entries.flow.async_configure(
        flow["flow_id"],
        sources.form(
            "humidity",
            primary_entity=sources.replacements[0].entity_id,
            primary_attribute="humidity",
            secondary_entity=sources.primary.entity_id,
            secondary_attribute="current_humidity",
        ),
    )
    hass.config_entries.flow.async_abort(flow["flow_id"])
    assert entry.data == legacy

    freezer.move_to(START + timedelta(hours=1, minutes=10))
    hass.states.async_set(sources.primary.entity_id, "unavailable")
    await observe("measured_source_unavailable")
    freezer.move_to(START + timedelta(hours=1, minutes=15))
    hass.states.async_remove(sources.primary.entity_id)
    await observe("measured_source_absent")

    reversed_legacy = deepcopy(legacy)
    reversed_legacy["datapoints"][0]["sources"].reverse()
    freezer.move_to(START + timedelta(hours=1, minutes=20))
    await load_saved(reversed_legacy)
    await observe("reordered")
    freezer.move_to(START + timedelta(hours=1, minutes=25))
    _thermostat_state(hass, sources.primary.entity_id, 20, humidity=43)
    await observe("measured_source_returned")
    freezer.move_to(START + timedelta(hours=1, minutes=30))
    await load_saved(legacy)
    await observe("original_order_restored")
    await _compile_hour(hass, freezer, 1)

    # Keep all transition observations visible in an old-source failure report.
    assert observations == {
        stage: {
            "state": "unavailable",
            "quality": "source_observation_role_mismatch",
            "source": None,
            "fallback_used": False,
            "device_class": "humidity",
            "unit_of_measurement": "%",
        }
        for stage in observations
    }
    assert reordered["type"] is FlowResultType.FORM
    assert reordered["errors"] == {"base": "source_observation_role_mismatch"}
    assert entry.data == legacy
    assert await _metadata(hass, [entity_id]) == metadata
    assert await _statistics(hass, [entity_id], 2) == recorded
    assert await _history(hass, [entity_id]) == recorded_history
    later_history = [
        (value, attributes)
        for value, attributes, updated in (await _history(hass, [entity_id], 2))[
            entity_id
        ]
        if updated >= transition
    ]
    assert later_history
    assert {value for value, _ in later_history} == {"unavailable"}
    assert {attributes["unit_of_measurement"] for _, attributes in later_history} == {
        "%"
    }
    assert {attributes["device_class"] for _, attributes in later_history} == {
        "humidity"
    }
    assert not any(
        issue.translation_key == "units_changed"
        for issue in ir.async_get(hass).issues.values()
    )


@pytest.mark.parametrize(
    ("attribute", "first_value", "fallback_value"),
    [
        ("current_humidity", 42, 47),
        ("humidity", 55, 57),
        ("reported_humidity", 41, 46),
    ],
    ids=["measured", "target", "opaque-confirmed"],
)
async def test_consistent_humidity_add_records_native_fallback(
    hass: HomeAssistant,
    sources: Sources,
    freezer: FrozenDateTimeFactory,
    attribute: str,
    first_value: float,
    fallback_value: float,
) -> None:
    """Current values stay usable; only proven measurements supply history."""
    entry, _ = await _setup(hass, sources, "humidity")
    native_entry = hass.config_entries.async_get_entry(
        sources.secondary.config_entry_id
    )
    assert native_entry is not None
    anchor = er.async_get(hass).async_get_or_create(
        "sensor",
        "ecobee",
        "thermostat_a-humidity",
        config_entry=native_entry,
        device_id=sources.secondary.device_id,
        original_device_class="humidity",
        unit_of_measurement="%",
    )
    hass.states.async_set(
        anchor.entity_id,
        "42",
        {
            "device_class": "humidity",
            "unit_of_measurement": "%",
            "state_class": "measurement",
        },
    )
    for source, value in (
        (sources.primary, first_value),
        (sources.secondary, fallback_value),
    ):
        state = hass.states.get(source.entity_id)
        assert state is not None
        hass.states.async_set(
            source.entity_id, state.state, dict(state.attributes) | {attribute: value}
        )
    values = sources.form(
        "humidity",
        name=f"Consistent {attribute}",
        primary_attribute=attribute,
        secondary_attribute=attribute,
    )
    if attribute == "reported_humidity":
        values |= {"primary_unit": "%", "secondary_unit": "%"}
    flow = await _form(hass, entry, None)
    unconfirmed = await hass.config_entries.flow.async_configure(
        flow["flow_id"], values | {"confirm_equivalence": False}
    )
    assert unconfirmed["type"] is FlowResultType.FORM
    assert unconfirmed["errors"] == {"base": "datapoint_equivalence_required"}
    await _save(hass, unconfirmed, values)
    config = entry.data["datapoints"][1]
    entity_id = _entity_id(hass, entry, config["datapoint_id"])
    assert float(hass.states.get(entity_id).state) == first_value
    await _compile_hour(hass, freezer, 0)
    metadata = await _metadata(hass, [entity_id])

    freezer.move_to(START + timedelta(hours=1, minutes=5))
    hass.states.async_set(sources.primary.entity_id, "unavailable")
    await async_wait_recording_done(hass)
    state = hass.states.get(entity_id)
    assert state is not None
    assert float(state.state) == fallback_value
    assert state.attributes["fallback_used"] is True
    assert state.attributes["source"] == sources.secondary.entity_id
    await _compile_hour(hass, freezer, 1)
    assert await _metadata(hass, [entity_id]) == metadata
    rows = (await _statistics(hass, [entity_id], 2))[entity_id]
    assert [row["mean"] for row in rows] == [first_value, fallback_value]
    assert {
        attributes["device_class"]
        for value, attributes, _ in (await _history(hass, [entity_id], 2))[entity_id]
        if value not in {"unavailable", "unknown"}
    } == {"humidity"}

    # Native Recorder metadata exists for both the composed output and anchor.
    native_rows = (await _statistics(hass, [anchor.entity_id], 2))[anchor.entity_id]
    assert [row["mean"] for row in native_rows] == [42, 42]
    native_contract = await async_capture_source(
        hass, anchor.entity_id, "humidity", anchor.id
    )
    composed = er.async_get(hass).async_get(entity_id)
    assert composed is not None
    original_data = deepcopy(dict(entry.data))
    manager = entry.runtime_data.datapoints
    for statistic_id, selected_anchor in (
        (entity_id, anchor),
        (anchor.entity_id, composed),
    ):
        if attribute == "current_humidity":
            captured = await async_capture_source(
                hass, statistic_id, "humidity", selected_anchor.id
            )
            assert captured["statistic_id"] == statistic_id
            assert captured["anchor_ref"] == selected_anchor.id
            assert captured["method"] == "recorder_hourly_time_weighted"
            assert captured["native_unit"] == "%"
            assert (
                await async_validate_source(
                    hass, captured, "humidity", selected_anchor.id
                )
                == captured
            )
        else:
            with pytest.raises(ValueError, match="historical_source_role_mismatch"):
                await async_capture_source(
                    hass, statistic_id, "humidity", selected_anchor.id
                )
            with pytest.raises(ValueError, match="historical_source_role_mismatch"):
                await async_validate_source(
                    hass,
                    native_contract | {"statistic_id": statistic_id},
                    "humidity",
                    selected_anchor.id,
                )

        flow = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
        )
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"next_step_id": "historical_add"}
        )
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"],
            {
                "name": "Daily measured humidity",
                "quantity": "humidity",
                "unit": "%",
                "timezone": hass.config.time_zone,
                "anchor_ref": selected_anchor.entity_id,
                "primary_statistic": statistic_id,
                "policy": "fixed_source",
                "accept_cross_method": False,
                "confirm_association": True,
            },
        )
        if attribute == "current_humidity":
            assert result["type"] is FlowResultType.MENU
        else:
            assert result["type"] is FlowResultType.FORM
            assert result["errors"] == {"base": "historical_source_role_mismatch"}
        hass.config_entries.flow.async_abort(flow["flow_id"])
    assert entry.data == original_data
    assert entry.runtime_data.datapoints is manager
    assert float(hass.states.get(entity_id).state) == fallback_value
    assert await _metadata(hass, [entity_id]) == metadata
    assert (await _statistics(hass, [entity_id], 2))[entity_id] == rows


@pytest.mark.parametrize(
    ("old_kind", "new_kind", "change", "new_mean"),
    [
        ("temperature", "humidity", "quantity", 42),
        ("humidity", "battery", "quantity", 87),
        ("temperature", "temperature", "subject", 24),
        ("humidity", "humidity", "role", 55),
    ],
)
async def test_changed_meaning_preserves_existing_recorder_identity(
    hass: HomeAssistant,
    sources: Sources,
    freezer: FrozenDateTimeFactory,
    recorder_db_url: str,
    old_kind: str,
    new_kind: str,
    change: str,
    new_mean: float,
) -> None:
    """Reject quantity, physical-subject and measured/target-role identity reuse."""
    entry, entity_id = await _setup(hass, sources, old_kind)
    await _compile_hour(hass, freezer, 0)
    assert recorder_db_url.startswith("sqlite:///")
    database = Path(recorder_db_url.removeprefix("sqlite:///"))
    assert (await hass.async_add_executor_job(database.stat)).st_size > 0
    metadata = await _metadata(hass, [entity_id])
    recorded = await _statistics(hass, [entity_id])
    recorded_history = await _history(hass, [entity_id])
    assert len(recorded[entity_id]) == 1
    assert recorded[entity_id][0]["mean"] == (20 if old_kind == "temperature" else 42)
    assert recorded_history[entity_id]
    original = deepcopy(dict(entry.data))
    manager = entry.runtime_data.datapoints
    registry_entry = er.async_get(hass).async_get(entity_id)
    assert registry_entry is not None
    assert registry_entry.device_id is not None
    device_entry = dr.async_get(hass).async_get(registry_entry.device_id)
    assert device_entry is not None
    values = sources.form(new_kind)
    if change == "subject":
        values |= {
            "primary_entity": sources.other_thermostat[0].entity_id,
            "secondary_entity": sources.other_thermostat[1].entity_id,
        }
    elif change == "role":
        values |= {"primary_attribute": "humidity", "secondary_attribute": "humidity"}

    freezer.move_to(START + timedelta(hours=1, minutes=5))
    flow = await _form(hass, entry, "recorded_channel")
    rejected = await hass.config_entries.flow.async_configure(flow["flow_id"], values)
    assert rejected["type"] is FlowResultType.FORM
    assert rejected["errors"] == {"base": "datapoint_meaning_change"}
    assert entry.data == original
    assert entry.runtime_data.datapoints is manager
    assert _entity_id(hass, entry, "recorded_channel") == entity_id
    await async_wait_recording_done(hass)
    assert er.async_get(hass).async_get(entity_id) == registry_entry
    assert dr.async_get(hass).async_get(registry_entry.device_id) == device_entry
    assert await _metadata(hass, [entity_id]) == metadata
    assert await _statistics(hass, [entity_id]) == recorded
    assert await _history(hass, [entity_id]) == recorded_history
    hass.config_entries.flow.async_abort(flow["flow_id"])

    # The instructed replacement route creates its own HA/Recorder identity.
    await _save(
        hass,
        await _form(hass, entry, None),
        values | {"name": f"New {change} {new_kind}"},
    )
    new_config = entry.data["datapoints"][1]
    new_entity_id = _entity_id(hass, entry, new_config["datapoint_id"])
    assert new_config["datapoint_id"] != "recorded_channel"
    assert new_entity_id != entity_id
    await _compile_hour(hass, freezer, 1)
    after_metadata = await _metadata(hass, [entity_id, new_entity_id])
    assert after_metadata[entity_id] == metadata[entity_id]
    assert after_metadata[new_entity_id][0] != metadata[entity_id][0]
    assert await _statistics(hass, [entity_id]) == recorded
    assert await _history(hass, [entity_id]) == recorded_history
    new_rows = (await _statistics(hass, [new_entity_id], 2))[new_entity_id]
    assert len(new_rows) == 1
    assert new_rows[0]["mean"] == new_mean
    all_history = await _history(hass, [entity_id, new_entity_id], 2)
    for current_id, kind in ((entity_id, old_kind), (new_entity_id, new_kind)):
        assert {
            attrs["device_class"]
            for state, attrs, _ in all_history[current_id]
            if state not in {"unavailable", "unknown"}
        } == {kind}
    assert not any(
        issue.translation_key == "units_changed"
        for issue in ir.async_get(hass).issues.values()
    )


@pytest.mark.parametrize(
    ("kind", "old_mean", "new_mean"),
    [("temperature", 20, 23), ("humidity", 42, 46)],
)
async def test_equivalent_native_rebinding_preserves_persisted_identity(
    hass: HomeAssistant,
    sources: Sources,
    freezer: FrozenDateTimeFactory,
    kind: str,
    old_mean: float,
    new_mean: float,
) -> None:
    """New registry UUIDs can retain a proven thermostat and native measured role."""
    entry, entity_id = await _setup(hass, sources, kind)
    await _compile_hour(hass, freezer, 0)
    metadata = await _metadata(hass, [entity_id])
    first_statistics = await _statistics(hass, [entity_id])
    first_history = await _history(hass, [entity_id])
    original_registry = er.async_get(hass).async_get(entity_id)
    assert original_registry is not None
    assert original_registry.device_id == sources.primary.device_id
    assert {source.id for source in sources.replacements}.isdisjoint(
        {sources.primary.id, sources.secondary.id}
    )

    freezer.move_to(START + timedelta(hours=1, minutes=5))
    await _save(
        hass,
        await _form(hass, entry, "recorded_channel"),
        sources.form(
            kind,
            primary_entity=sources.replacements[0].entity_id,
            secondary_entity=sources.replacements[1].entity_id,
        ),
    )
    rebound = er.async_get(hass).async_get(entity_id)
    assert rebound is not None
    assert rebound.id == original_registry.id
    assert rebound.device_id == original_registry.device_id
    assert _entity_id(hass, entry, "recorded_channel") == entity_id
    assert {source["entity"] for source in entry.data["datapoints"][0]["sources"]} == {
        source.id for source in sources.replacements
    }
    assert entry.runtime_data.datapoints.snapshot("recorded_channel").value == new_mean
    # The former source is no longer a dependency of the retained datapoint.
    _thermostat_state(hass, sources.primary.entity_id, 99, humidity=99)
    await async_wait_recording_done(hass)
    assert float(hass.states.get(entity_id).state) == new_mean
    await _compile_hour(hass, freezer, 1)
    assert await _metadata(hass, [entity_id]) == metadata
    assert await _statistics(hass, [entity_id]) == first_statistics
    assert await _history(hass, [entity_id]) == first_history
    rows = (await _statistics(hass, [entity_id], 2))[entity_id]
    assert [row["mean"] for row in rows] == [old_mean, new_mean]


async def test_same_meaning_edits_preserve_persisted_statistics_and_convert_units(
    hass: HomeAssistant,
    sources: Sources,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Rename/order/fallback and C→F→C edits retain one correctly scaled series."""
    entry, entity_id = await _setup(hass, sources, "temperature")
    await _compile_hour(hass, freezer, 0)
    metadata = await _metadata(hass, [entity_id])
    first = await _statistics(hass, [entity_id])
    registry_entry = er.async_get(hass).async_get(entity_id)
    assert registry_entry is not None
    original_registry_id = registry_entry.id

    freezer.move_to(START + timedelta(hours=1, minutes=5))
    values = _datapoint_form_defaults(hass, entry.data["datapoints"][0]) | sources.form(
        "temperature",
        name="Renamed temperature",
        primary_entity=sources.secondary.entity_id,
        secondary_entity=sources.primary.entity_id,
        fallback=False,
    )
    await _save(hass, await _form(hass, entry, "recorded_channel"), values)
    assert _entity_id(hass, entry, "recorded_channel") == entity_id
    assert entry.runtime_data.datapoints.snapshot("recorded_channel").value == 21
    hass.states.async_set(sources.secondary.entity_id, "unavailable")
    await async_wait_recording_done(hass)
    assert hass.states.get(entity_id).state == "unavailable"
    _thermostat_state(hass, sources.secondary.entity_id, 21)
    await async_wait_recording_done(hass)
    await _compile_hour(hass, freezer, 1)

    freezer.move_to(START + timedelta(hours=2, minutes=5))
    values |= {"unit": "°F", "fallback": True}
    await _save(hass, await _form(hass, entry, "recorded_channel"), values)
    # Exercise genuine Fahrenheit state storage as well as native-value conversion.
    er.async_get(hass).async_update_entity_options(
        entity_id, "sensor", {"unit_of_measurement": "°F"}
    )
    hass.states.async_set(sources.secondary.entity_id, "unavailable")
    await async_wait_recording_done(hass)
    snapshot = entry.runtime_data.datapoints.snapshot("recorded_channel")
    assert snapshot.value == 68
    assert snapshot.fallback_used
    assert hass.states.get(entity_id).attributes["unit_of_measurement"] == "°F"
    assert float(hass.states.get(entity_id).state) == 68
    await _compile_hour(hass, freezer, 2)

    freezer.move_to(START + timedelta(hours=3, minutes=5))
    _thermostat_state(hass, sources.secondary.entity_id, 22)
    await async_wait_recording_done(hass)
    await _save(
        hass, await _form(hass, entry, "recorded_channel"), values | {"unit": "°C"}
    )
    er.async_get(hass).async_update_entity_options(
        entity_id, "sensor", {"unit_of_measurement": "°C"}
    )
    await async_wait_recording_done(hass)
    await _compile_hour(hass, freezer, 3)
    assert _entity_id(hass, entry, "recorded_channel") == entity_id
    assert er.async_get(hass).async_get(entity_id).id == original_registry_id
    assert await _metadata(hass, [entity_id]) == metadata
    assert await _statistics(hass, [entity_id]) == first
    rows = (await _statistics(hass, [entity_id], 4))[entity_id]
    assert [row["mean"] for row in rows] == [20, 21, 20, 22]
    assert {row["min"] for row in rows} == {20, 21, 22}
    assert {row["max"] for row in rows} == {20, 21, 22}
    stored_states = (await _history(hass, [entity_id], 4))[entity_id]
    assert {
        attrs["unit_of_measurement"]
        for state, attrs, _ in stored_states
        if state not in {"unavailable", "unknown"}
    } == {"°C", "°F"}
    assert {
        attrs["device_class"]
        for state, attrs, _ in stored_states
        if state not in {"unavailable", "unknown"}
    } == {"temperature"}
    assert not any(
        issue.translation_key == "units_changed"
        for issue in ir.async_get(hass).issues.values()
    )
