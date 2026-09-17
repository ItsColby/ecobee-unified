"""Public measurement/day responses through Home Assistant's service registry."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from homeassistant.core import Context, HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError

from custom_components.ecobee_unified.beestat_history import (
    METHOD,
    POLICY,
    async_read_history,
    async_validate_read,
    capture_descriptor,
    project_capability,
)

START = datetime(2026, 9, 10, tzinfo=UTC)
IDENTITY = {
    "entry_id": "example_entry",
    "api_base": "https://api.beestat.io",
    "account_anchors": ["a" * 64],
}


def descriptor() -> dict[str, Any]:
    """Synthetic public physical identity, never a private installation binding."""
    return {
        "quantity_id": "sensor:201:temperature",
        "thermostat_id": 101,
        "sensor_id": 201,
        "quantity": "temperature",
        "kind": "measurement",
        "logical_unit": "°F",
        "method_version": METHOD,
        "statistic_id": "beestat:example_room_temperature_hourly_v3",
        "legacy_statistic_ids": ["beestat:example_room_temperature"],
        "representation": {
            "kind": "arithmetic_mean",
            "native_field": "mean",
            "unit_of_measurement": "°F",
            "unit_class": "temperature",
            "logical_multiplier": 1.0,
            "v2_statistic_id": "beestat:example_room_temperature_hourly_v2",
        },
        "admission": "eligible",
        "writer_status": "adopted",
    }


def capability() -> dict[str, Any]:
    return {
        "contract_version": 3,
        "method_version": METHOD,
        "daily_policy": POLICY,
        "identity": deepcopy(IDENTITY),
        "root_revision": 8,
        "coverage_revision": 2,
        "source_revision": "b" * 64,
        "services": {"coverage": "beestat_statistics.get_hourly_coverage"},
        "limits": {"quantities": 40, "days": 366, "page_buckets": 4096},
        "operation": {
            "status": "completed",
            "root_revision": 8,
            "has_pending": False,
            "contract_version": 3,
            "operation_id": "example-operation",
            "plan_digest": "c" * 64,
            "cursor": 1,
            "batch_count": 1,
            "verified_batches": 1,
            "remaining_batches": 0,
            "error": None,
        },
        "quantities": [descriptor()],
    }


def contract() -> dict[str, Any]:
    projected = project_capability(capability(), "example_entry")
    result = capture_descriptor(projected, descriptor()["statistic_id"], "temperature")
    assert result is not None
    return result


def bucket(day: int = 0, reason: str = "ready") -> dict[str, Any]:
    first = START + timedelta(days=day)
    end = first + timedelta(days=1)
    verified = (
        24 if reason == "ready" else 1 if reason in {"partial", "legacy_day"} else 0
    )
    intervals = [
        [
            (first + timedelta(hours=i)).isoformat(),
            (first + timedelta(hours=i + 1)).isoformat(),
        ]
        for i in range(24)
    ]
    values: dict[str, Any] = {
        "start": first.isoformat(),
        "end": end.isoformat(),
        "value": 70.0 if verified else None,
        "min": 68.0 if verified else None,
        "max": 72.0 if verified else None,
        "reason": reason,
        "failure_reason": None,
        "valid_slots": verified * 12,
        "expected_slots": 288,
        "verified_hours": verified,
        "expected_hours": 24,
        "closed_hours": 24,
        "observed_amount": None,
        "complete_total": None,
        "source_basis": "points" if verified else "unavailable",
        "method_basis": METHOD,
        "confidence": ["provider_ordered"],
        "source_ids": ["d" * 64],
        "eligible_intervals": intervals if reason in {"ready", "legacy_day"} else [],
        "calendar_start": first.isoformat(),
        "calendar_end": end.isoformat(),
        "calendar_hours": 24,
        "point_observed_amount": None,
        "observed_intervals": intervals[:verified],
        "coverage_reasons": {"ready": verified, "missing": 24 - verified}
        if verified
        else {reason: 24},
    }
    if reason == "legacy_day":
        values.update(
            source_basis="legacy_daily",
            method_basis="legacy_sample_mean",
            confidence=["historical_sample_completeness_unproven"],
            source_ids=["beestat:example_room_temperature"],
            min=None,
            max=None,
        )
    return values


def response(values: list[dict[str, Any]]) -> dict[str, Any]:
    cap = capability()
    coverage: dict[str, int] = {}
    for item in values:
        for reason, count in item["coverage_reasons"].items():
            coverage[reason] = coverage.get(reason, 0) + count
    verified = sum(item["verified_hours"] for item in values)
    summary = {
        "scope": "query",
        "start": values[0]["start"],
        "end": values[-1]["end"],
        "verified_hours": verified,
        "expected_hours": len(values) * 24,
        "closed_hours": len(values) * 24,
        "eligible_hours": sum(len(item["eligible_intervals"]) for item in values),
        "eligible_buckets": sum(bool(item["eligible_intervals"]) for item in values),
        "legacy_days": sum(item["reason"] == "legacy_day" for item in values),
        "coverage_reasons": coverage,
        "summary_basis": "verified_point_hours",
        "observed_mean": 70.0 if verified else None,
        "observed_min": 68.0 if verified else None,
        "observed_max": 72.0 if verified else None,
    }
    return {
        **{
            key: cap[key]
            for key in (
                "contract_version",
                "method_version",
                "daily_policy",
                "identity",
                "root_revision",
                "coverage_revision",
                "source_revision",
                "operation",
            )
        },
        "config_revision": "e" * 64,
        "view_token": "f" * 64,
        "evaluated_at": "2026-09-17T00:00:00+00:00",
        "period": "day",
        "timezone": "UTC",
        "timezone_revision": 0,
        "native_verification": "bounded_readback",
        "start": values[0]["start"],
        "end": values[-1]["end"],
        "pagination": {
            "offset": 0,
            "next_offset": None,
            "has_more": False,
            "total_buckets": len(values),
        },
        "series": [{"descriptor": descriptor(), "buckets": values, "summary": summary}],
    }


def register(
    hass: HomeAssistant, data: dict[str, Any], *, page_size: int = 1240
) -> list[ServiceCall]:
    calls: list[ServiceCall] = []

    async def serve(call: ServiceCall) -> dict[str, Any]:
        calls.append(call)
        result = deepcopy(data)
        offset = call.data["offset"]
        all_values = result["series"][0]["buckets"]
        stop = min(len(all_values), offset + page_size)
        result["series"][0]["buckets"] = all_values[offset:stop]
        result["pagination"].update(
            offset=offset,
            next_offset=stop if stop < len(all_values) else None,
            has_more=stop < len(all_values),
        )
        return result

    hass.services.async_register(
        "beestat_statistics",
        "get_hourly_coverage",
        serve,
        supports_response=SupportsResponse.ONLY,
    )
    return calls


async def test_pagination_preserves_one_query_summary_and_context(
    hass: HomeAssistant,
) -> None:
    data = response([bucket(0), bucket(1)])
    calls = register(hass, data, page_size=1)
    context = Context()
    result = await async_read_history(
        hass, [contract()], START, START + timedelta(days=2), "UTC", context
    )
    statistic = descriptor()["statistic_id"]
    assert len(result.buckets[statistic]) == 2
    assert result.summaries[statistic]["verified_hours"] == 48
    assert result.provenance["root_revision"] == 8
    assert result.provenance["coverage_revision"] == 2
    assert calls[0].context is context
    assert calls[1].data["view_token"] == "f" * 64
    await async_validate_read(hass, result, context)
    assert dict(calls[-1].data) == result.replay_payload


@pytest.mark.parametrize("reason", ["ready", "pending", "conflict"])
async def test_writer_progress_does_not_replace_bucket_admission(
    hass: HomeAssistant, reason: str
) -> None:
    data = response([bucket(reason=reason)])
    data["series"][0]["descriptor"]["writer_status"] = "reserved"
    if reason != "ready":
        data["series"][0]["descriptor"].update(
            admission="blocked", blocked_reason="source_conflict"
        )
    register(hass, data)
    result = await async_read_history(
        hass, [contract()], START, START + timedelta(days=1), "UTC"
    )
    assert result.buckets[descriptor()["statistic_id"]][0]["reason"] == reason


async def test_two_quantities_page_independently_and_preserve_measured_zero(
    hass: HomeAssistant,
) -> None:
    data = response([bucket(0), bucket(1)])
    second = deepcopy(data["series"][0])
    second["descriptor"].update(
        quantity_id="sensor:202:temperature",
        sensor_id=202,
        statistic_id="beestat:second_temperature_hourly_v3",
        legacy_statistic_ids=["beestat:second_temperature"],
    )
    second["descriptor"]["representation"]["v2_statistic_id"] = (
        "beestat:second_temperature_hourly_v2"
    )
    for value in second["buckets"]:
        value.update(value=0.0, min=0.0, max=0.0)
    second["summary"].update(observed_mean=0.0, observed_min=0.0, observed_max=0.0)
    data["series"].append(second)
    cap = capability()
    cap["quantities"].append(second["descriptor"])
    projected = project_capability(cap, IDENTITY["entry_id"])
    contracts = [
        capture_descriptor(projected, item["statistic_id"], "temperature")
        for item in cap["quantities"]
    ]
    assert all(item is not None for item in contracts)
    calls = []

    async def serve(call: ServiceCall) -> dict[str, Any]:
        calls.append(call)
        offset = call.data["offset"]
        result = deepcopy(data)
        series = result["series"][offset // 2]
        series["buckets"] = [series["buckets"][offset % 2]]
        result["series"] = [series]
        result["pagination"].update(
            offset=offset,
            next_offset=offset + 1 if offset < 3 else None,
            has_more=offset < 3,
            total_buckets=4,
        )
        return result

    hass.services.async_register(
        "beestat_statistics",
        "get_hourly_coverage",
        serve,
        supports_response=SupportsResponse.ONLY,
    )
    result = await async_read_history(
        hass, contracts, START, START + timedelta(days=2), "UTC"
    )
    await async_validate_read(hass, result)
    statistic = second["descriptor"]["statistic_id"]
    assert [value["value"] for value in result.buckets[statistic]] == [0, 0]
    assert result.summaries[statistic]["verified_hours"] == 48
    assert result.summaries[statistic]["observed_mean"] == 0
    assert [call.data["offset"] for call in calls] == [0, 1, 2, 3, 3]


@pytest.mark.parametrize(
    "reason",
    [
        "ready",
        "legacy_day",
        "partial",
        "pending",
        "conflict",
        "unassessed",
        "unverified_native",
        "blocked",
        "missing",
        "invalid",
        "provisional",
    ],
)
async def test_qualified_values_and_absence_are_not_conflated(
    hass: HomeAssistant, reason: str
) -> None:
    data = response([bucket(reason=reason)])
    register(hass, data)
    result = await async_read_history(
        hass, [contract()], START, START + timedelta(days=1), "UTC"
    )
    actual = result.buckets[descriptor()["statistic_id"]][0]
    assert actual["reason"] == reason
    assert bool(actual["eligible_intervals"]) == (reason in {"ready", "legacy_day"})
    assert actual["value"] == (
        70 if reason in {"ready", "legacy_day", "partial"} else None
    )
    if reason == "legacy_day":
        assert actual["verified_hours"] == 1
        assert len(actual["eligible_intervals"]) == 24
        assert actual["min"] is None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("value", True),
        ("value", float("nan")),
        ("min", float("inf")),
        ("reason", "unknown"),
        ("valid_slots", True),
        ("eligible_intervals", []),
        ("source_basis", "legacy_daily"),
    ],
)
async def test_malformed_ready_bucket_rejected(
    hass: HomeAssistant, key: str, value: Any
) -> None:
    data = response([bucket()])
    data["series"][0]["buckets"][0][key] = value
    register(hass, data)
    with pytest.raises(ValueError, match="historical_response_invalid"):
        await async_read_history(
            hass, [contract()], START, START + timedelta(days=1), "UTC"
        )


async def test_late_revision_race_then_new_read_recovers(hass: HomeAssistant) -> None:
    data = response([bucket()])
    register(hass, data)
    read = await async_read_history(
        hass, [contract()], START, START + timedelta(days=1), "UTC"
    )
    data["coverage_revision"] += 1
    data["view_token"] = "1" * 64
    with pytest.raises(ValueError, match="historical_source_changed"):
        await async_validate_read(hass, read)
    recovered = await async_read_history(
        hass, [contract()], START, START + timedelta(days=1), "UTC"
    )
    assert recovered.provenance["coverage_revision"] == 3


async def test_service_failure_is_sanitized(hass: HomeAssistant) -> None:
    async def unavailable(call: ServiceCall) -> dict[str, Any]:
        raise HomeAssistantError("private provider detail")

    hass.services.async_register(
        "beestat_statistics",
        "get_hourly_coverage",
        unavailable,
        supports_response=SupportsResponse.ONLY,
    )
    with pytest.raises(ValueError, match=r"^historical_beestat_unavailable$"):
        await async_read_history(
            hass, [contract()], START, START + timedelta(days=1), "UTC"
        )


def test_exact_native_identity_and_bounded_projection() -> None:
    data = capability()
    data["private_configuration"] = "must not escape"
    projected = project_capability(data, "example_entry")
    assert "private_configuration" not in projected
    assert (
        capture_descriptor(projected, "beestat:example_room_temperature", "temperature")
        is None
    )
    captured = capture_descriptor(
        projected, descriptor()["statistic_id"], "temperature"
    )
    assert captured is not None
    assert "writer_status" not in captured["descriptor"]
    assert "root_revision" not in captured


@pytest.mark.parametrize(
    "change", ["duplicate", "identity", "alias", "multiplier", "version", "operation"]
)
def test_bad_capability_is_rejected(change: str) -> None:
    data = capability()
    if change == "duplicate":
        data["quantities"].append(descriptor())
    elif change == "identity":
        data["quantities"][0]["sensor_id"] = 999
    elif change == "alias":
        data["quantities"][0]["legacy_statistic_ids"] = [
            "https://unrelated.invalid/secret"
        ]
    elif change == "multiplier":
        data["quantities"][0]["representation"]["logical_multiplier"] = True
    elif change == "version":
        data["contract_version"] = True
    else:
        data["operation"]["status"] = "stale"
    with pytest.raises(ValueError, match="historical_response_invalid"):
        project_capability(data, "example_entry")


def test_blocked_and_unselected_operations_do_not_claim_completion() -> None:
    data = capability()
    data["operation"].update(
        status="blocked",
        remaining_batches=1,
        verified_batches=0,
        cursor=0,
        has_pending=True,
    )
    assert project_capability(data, "example_entry")["operation"]["status"] == "blocked"
    data["operation"] = {
        "status": "unselected",
        "root_revision": 8,
        "has_pending": False,
    }
    assert (
        project_capability(data, "example_entry")["operation"]["status"] == "unselected"
    )


def test_measurement_rate_representation_is_rejected() -> None:
    data = capability()
    data["quantities"][0]["representation"]["logical_multiplier"] = 0.01
    with pytest.raises(ValueError, match="historical_source_unit_mismatch"):
        capture_descriptor(
            project_capability(data, "example_entry"),
            descriptor()["statistic_id"],
            "temperature",
        )


async def test_unknown_confidence_preserved_without_granting_eligibility(
    hass: HomeAssistant,
) -> None:
    data = response([bucket(reason="partial")])
    data["series"][0]["buckets"][0]["confidence"] = ["future_bounded_qualification"]
    register(hass, data)
    result = await async_read_history(
        hass, [contract()], START, START + timedelta(days=1), "UTC"
    )
    actual = result.buckets[descriptor()["statistic_id"]][0]
    assert actual["confidence"] == ["future_bounded_qualification"]
    assert actual["eligible_intervals"] == []


@pytest.mark.parametrize("change", ["summary", "view", "duplicate", "pagination"])
async def test_second_page_cannot_change_or_repeat_the_view(
    hass: HomeAssistant, change: str
) -> None:
    data = response([bucket(0), bucket(1)])

    async def serve(call: ServiceCall) -> dict[str, Any]:
        result = deepcopy(data)
        offset = call.data["offset"]
        result["series"][0]["buckets"] = [
            deepcopy(data["series"][0]["buckets"][offset])
        ]
        result["pagination"].update(
            offset=offset, has_more=offset == 0, next_offset=1 if offset == 0 else None
        )
        if offset:
            if change == "summary":
                result["series"][0]["summary"]["observed_mean"] = 71
            elif change == "view":
                result["view_token"] = "1" * 64
            elif change == "duplicate":
                result["series"][0]["buckets"] = [bucket(0)]
            else:
                result["pagination"]["has_more"] = True
                result["pagination"]["next_offset"] = 1
        return result

    hass.services.async_register(
        "beestat_statistics",
        "get_hourly_coverage",
        serve,
        supports_response=SupportsResponse.ONLY,
    )
    with pytest.raises(ValueError, match="historical_"):
        await async_read_history(
            hass, [contract()], START, START + timedelta(days=2), "UTC"
        )


@pytest.mark.parametrize("hours", [23, 25])
async def test_dst_days_use_exact_calendar_hour_sets(
    hass: HomeAssistant, hours: int
) -> None:
    first = (
        datetime(2026, 3, 8, 5, tzinfo=UTC)
        if hours == 23
        else datetime(2026, 11, 1, 4, tzinfo=UTC)
    )
    end = first + timedelta(hours=hours)
    value = bucket()
    intervals = [
        [
            (first + timedelta(hours=i)).isoformat(),
            (first + timedelta(hours=i + 1)).isoformat(),
        ]
        for i in range(hours)
    ]
    value.update(
        start=first.isoformat(),
        end=end.isoformat(),
        calendar_start=first.isoformat(),
        calendar_end=end.isoformat(),
        calendar_hours=hours,
        expected_hours=hours,
        verified_hours=hours,
        closed_hours=hours,
        valid_slots=hours * 12,
        expected_slots=hours * 12,
        eligible_intervals=intervals,
        observed_intervals=intervals,
        coverage_reasons={"ready": hours},
    )
    data = response([value])
    data["timezone"] = "America/New_York"
    data["evaluated_at"] = "2026-12-01T00:00:00+00:00"
    data["series"][0]["summary"].update(expected_hours=hours, closed_hours=hours)
    register(hass, data)
    read = await async_read_history(hass, [contract()], first, end, "America/New_York")
    assert (
        len(read.buckets[descriptor()["statistic_id"]][0]["eligible_intervals"])
        == hours
    )
