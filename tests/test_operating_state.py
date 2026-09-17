"""Coherent equipment detail and configurable reads without writer changes."""

from dataclasses import replace

import pytest

from custom_components.ecobee_unified.models import (
    RawSource,
    SourceHealth,
    build_snapshot,
    degradation_advisories,
    equipment_stage,
)


def report(action: str, equipment: str = "", temperature: float = 21.0) -> RawSource:
    return RawSource(
        "heat_cool",
        {
            "hvac_action": action,
            "equipment_running": equipment,
            "current_temperature": temperature,
            "current_humidity": 42.0,
            "temperature": 22.0,
            "target_temp_low": 19.0,
            "target_temp_high": 24.0,
            "fan_mode": "auto",
            "unit_of_measurement": "°C",
            "supported_features": 3,
            "hvac_modes": ["off", "heat", "cool", "heat_cool"],
        },
        health=SourceHealth.HEALTHY,
        reported_at="2026-01-01T12:00:00+00:00",
    )


@pytest.mark.parametrize("mapping", ["zone_a", "zone_b"])
@pytest.mark.parametrize(
    ("local", "cloud", "equipment", "expected", "status"),
    [
        ("idle", "cooling", "compCool1,fan", "idle", "disagreement"),
        ("cooling", "idle", "", "cooling", "disagreement"),
        ("cooling", "cooling", "compCool1,fan", "cool_stage_1", "matched"),
        ("heating", "heating", "heatPump,fan", "heat_pump_stage_1", "matched"),
        ("heating", "cooling", "compCool1,fan", "heating", "disagreement"),
        ("idle", "fan", "fan", "idle", "disagreement"),
        ("fan", "fan", "fan", "fan", "matched"),
        ("cooling", "cooling", "compCool1,auxHeat1", "cooling", "ambiguous"),
        ("cooling", "cooling", "newEquipment", "cooling", "ambiguous"),
    ],
)
def test_detail_never_contradicts_selected_action(
    mapping: str, local: str, cloud: str, equipment: str, expected: str, status: str
) -> None:
    snapshot = build_snapshot(mapping, report(local), report(cloud, equipment))
    assert snapshot.hvac_action == local
    assert snapshot.equipment_stage == expected
    assert snapshot.equipment_detail_status == status
    assert snapshot.reported_equipment_stage == equipment_stage(equipment)
    assert snapshot.equipment_reported_at == "2026-01-01T12:00:00+00:00"
    if status == "disagreement":
        assert "equipment_action_disagreement" in degradation_advisories(snapshot)


@pytest.mark.parametrize("mapping", ["zone_a", "zone_b"])
def test_both_arrival_orders_recover_detail(mapping: str) -> None:
    idle = report("idle")
    local_cooling = report("cooling")
    cloud_cooling = report("cooling", "compCool2,fan")
    for sequence in (
        [
            (idle, idle),
            (local_cooling, idle),
            (local_cooling, cloud_cooling),
            (idle, cloud_cooling),
            (idle, idle),
        ],
        [
            (idle, idle),
            (idle, cloud_cooling),
            (local_cooling, cloud_cooling),
            (local_cooling, idle),
            (idle, idle),
        ],
    ):
        states = [build_snapshot(mapping, local, cloud) for local, cloud in sequence]
        assert states[0].equipment_stage == states[-1].equipment_stage == "idle"
        assert states[2].equipment_stage == "cool_stage_2"
        assert (
            states[1].equipment_detail_status
            == states[3].equipment_detail_status
            == "disagreement"
        )


@pytest.mark.parametrize("mapping", ["zone_a", "zone_b"])
def test_cloud_preference_changes_reads_and_keeps_writer_envelope(mapping: str) -> None:
    local, cloud = report("idle"), report("cooling", "compCool1,fan", 23.0)
    baseline = build_snapshot(mapping, local, cloud)
    selected = build_snapshot(
        mapping,
        local,
        cloud,
        read_policies={
            "hvac_action": "ecobee_first",
            "current_temperature": "ecobee_first",
        },
    )
    assert selected.hvac_action == "cooling"
    assert selected.equipment_stage == "cool_stage_1"
    assert selected.current_temperature == 23.0
    assert selected.provenance["hvac_action"] == "ecobee"
    assert selected.homekit_writable == baseline.homekit_writable
    assert selected.supported_features == baseline.supported_features
    assert selected.confirmation_values == baseline.confirmation_values


@pytest.mark.parametrize(
    "health",
    [
        SourceHealth.STALE,
        SourceHealth.UNKNOWN,
        SourceHealth.UNAVAILABLE,
        SourceHealth.MISSING,
    ],
)
def test_missing_cloud_detail_is_coarse_and_policy_controls_fallback(
    health: SourceHealth,
) -> None:
    local = report("cooling")
    cloud = replace(report("cooling", "compCool2"), health=health)
    fallback = build_snapshot(
        "zone", local, cloud, read_policies={"hvac_action": "ecobee_first"}
    )
    assert fallback.hvac_action == fallback.equipment_stage == "cooling"
    assert fallback.reported_equipment_stage is None
    assert fallback.equipment_detail_status == "unavailable"
    strict = build_snapshot(
        "zone", local, cloud, read_policies={"hvac_action": "ecobee_only"}
    )
    assert strict.hvac_action is None
    assert strict.equipment_stage is None
    assert strict.homekit_writable


@pytest.mark.parametrize(
    "field",
    [
        "hvac_mode",
        "current_temperature",
        "current_humidity",
        "target_temperature",
        "target_temperature_low",
        "target_temperature_high",
        "fan_mode",
    ],
)
def test_each_read_policy_has_explicit_no_fallback(field: str) -> None:
    missing = RawSource(None)
    cloud = report("cooling", "compCool1")
    snapshot = build_snapshot(
        "zone", missing, cloud, read_policies={field: "homekit_only"}
    )
    assert getattr(snapshot, field) is None
    assert not snapshot.homekit_writable
    assert snapshot.ecobee_writable
