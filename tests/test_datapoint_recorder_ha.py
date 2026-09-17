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
    return Sources(records[0], records[1], (batteries[0], batteries[1]))


def _thermostat_state(hass: HomeAssistant, entity_id: str, temperature: float) -> None:
    hass.states.async_set(
        entity_id,
        "heat",
        {
            "current_temperature": temperature,
            "current_humidity": 42,
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
        minor_version=4,
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


@pytest.mark.parametrize(
    ("old_kind", "new_kind"), [("temperature", "humidity"), ("humidity", "battery")]
)
async def test_changed_quantity_preserves_existing_recorder_identity(
    hass: HomeAssistant,
    sources: Sources,
    freezer: FrozenDateTimeFactory,
    recorder_db_url: str,
    old_kind: str,
    new_kind: str,
) -> None:
    """Reject both incompatible units and equal-unit/different-meaning reuse."""
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
    registry_id = er.async_get(hass).async_get(entity_id).id

    freezer.move_to(START + timedelta(hours=1, minutes=5))
    flow = await _form(hass, entry, "recorded_channel")
    rejected = await hass.config_entries.flow.async_configure(
        flow["flow_id"], sources.form(new_kind)
    )
    assert rejected["type"] is FlowResultType.FORM
    assert rejected["errors"] == {"base": "datapoint_meaning_change"}
    assert entry.data == original
    assert entry.runtime_data.datapoints is manager
    assert _entity_id(hass, entry, "recorded_channel") == entity_id
    assert er.async_get(hass).async_get(entity_id).id == registry_id
    assert await _metadata(hass, [entity_id]) == metadata
    assert await _statistics(hass, [entity_id]) == recorded
    assert await _history(hass, [entity_id]) == recorded_history
    hass.config_entries.flow.async_abort(flow["flow_id"])

    # The instructed replacement route creates its own HA/Recorder identity.
    await _save(hass, await _form(hass, entry, None), sources.form(new_kind))
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
    assert new_rows[0]["mean"] == (42 if new_kind == "humidity" else 87)
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
