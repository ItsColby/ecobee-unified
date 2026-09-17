"""Read the public Beestat v3 measurement contract without owning its writer.

Only explicitly captured native identities are accepted. Provider day reductions,
point coverage and legacy daily provenance remain distinct throughout the read.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError

METHOD = "five_minute_complete_hour_v3"
POLICY = "complete_points_else_legacy_day"
_HOUR = timedelta(hours=1)
_ERROR = "historical_response_invalid"
_SPECS = {
    "temperature": ("temperature", "°F", "temperature"),
    "humidity": ("indoor_humidity", "%", "unitless"),
    "co2": ("co2_concentration", "ppm", "unitless"),
    "aqi": ("air_quality", "%", "unitless"),
    "weather_temperature": ("outdoor_temperature", "°F", "temperature"),
    "weather_humidity": ("outdoor_humidity", "%", "unitless"),
}
_REASONS = frozenset(
    {
        "ready",
        "legacy_day",
        "partial",
        "missing",
        "invalid",
        "provisional",
        "pending",
        "conflict",
        "unassessed",
        "unverified_native",
        "blocked",
    }
)
_FAILURES = frozenset(
    {
        "voc_unit_unresolved",
        "resource_identity_mismatch",
        "source_timestamp_unplaced",
        "source_horizon_unsettled",
        "source_conflict",
        "invalid_slots",
        "missing_slots",
        "source_evidence_invalid",
    }
)
_DESCRIPTOR = (
    "quantity_id",
    "thermostat_id",
    "sensor_id",
    "quantity",
    "kind",
    "logical_unit",
    "method_version",
    "statistic_id",
    "legacy_statistic_ids",
    "representation",
)


@dataclass(frozen=True)
class HistoryRead:
    """One complete bounded view, retaining the exact final-page replay."""

    buckets: dict[str, list[dict[str, Any]]]
    summaries: dict[str, dict[str, Any]]
    provenance: dict[str, Any]
    replay_payload: dict[str, Any]
    replay_response: dict[str, Any]


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError(_ERROR)


def _object(value: Any) -> dict[str, Any]:
    _require(isinstance(value, dict))
    return cast(dict[str, Any], value)


def _text(value: Any, limit: int = 255) -> str:
    _require(isinstance(value, str) and 0 < len(value) <= limit)
    _require(all(ord(char) >= 32 for char in value))
    return cast(str, value)


def _integer(value: Any, maximum: int = 2**63 - 1) -> int:
    _require(type(value) is int and 0 <= value <= maximum)
    return cast(int, value)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    _require(type(value) in (int, float))
    try:
        result = float(value)
    except (OverflowError, ValueError) as err:
        raise ValueError(_ERROR) from err
    _require(math.isfinite(result))
    return result


def _strings(value: Any, maximum: int = 4096) -> list[str]:
    _require(isinstance(value, list) and len(value) <= maximum)
    result = [_text(item) for item in value]
    _require(len(set(result)) == len(result))
    return result


def _digest(value: Any) -> str:
    _require(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None
    )
    return cast(str, value)


def _stamp(value: Any, *, hour: bool = True) -> datetime:
    try:
        stamp = datetime.fromisoformat(_text(value))
    except ValueError as err:
        raise ValueError(_ERROR) from err
    _require(stamp.utcoffset() is not None)
    stamp = stamp.astimezone(UTC)
    _require(not hour or not (stamp.minute or stamp.second or stamp.microsecond))
    return stamp


def _identity(value: Any, entry_id: str) -> dict[str, Any]:
    data = _object(value)
    _require(_text(data.get("entry_id")) == entry_id)
    base = _text(data.get("api_base"), 2048)
    try:
        parsed = urlsplit(base)
    except ValueError as err:
        raise ValueError(_ERROR) from err
    _require(parsed.scheme in {"http", "https"} and bool(parsed.hostname))
    _require(
        not (parsed.username or parsed.password or parsed.query or parsed.fragment)
    )
    anchors = _strings(data.get("account_anchors"), 128)
    for anchor in anchors:
        _digest(anchor)
    return {"entry_id": entry_id, "api_base": base, "account_anchors": anchors}


def _operation(value: Any, revision: int) -> dict[str, Any]:
    data = _object(value)
    status = _text(data.get("status"))
    _require(
        status in {"unselected", "accepted", "in_progress", "completed", "blocked"}
    )
    _require(_integer(data.get("root_revision")) == revision)
    _require(type(data.get("has_pending")) is bool)
    result = {key: data[key] for key in ("status", "root_revision", "has_pending")}
    if status == "unselected":
        _require(data["has_pending"] is False)
        _require(not any(key in data for key in ("operation_id", "plan_digest")))
        return result
    _require(
        type(data.get("contract_version")) is int and data["contract_version"] == 3
    )
    result.update(
        contract_version=3,
        operation_id=_text(data.get("operation_id")),
        plan_digest=_digest(data.get("plan_digest")),
    )
    for key in ("cursor", "batch_count", "verified_batches", "remaining_batches"):
        result[key] = _integer(data.get(key))
    _require(
        result["verified_batches"] + result["remaining_batches"]
        == result["batch_count"]
    )
    _require(result["cursor"] <= result["batch_count"])
    if status == "completed":
        _require(result["remaining_batches"] == 0 and data["has_pending"] is False)
    error = data.get("error")
    result["error"] = None if error is None else _text(error)
    return result


def _descriptor(value: Any) -> dict[str, Any]:
    data = _object(value)
    thermostat = _integer(data.get("thermostat_id"))
    _require(thermostat > 0)
    sensor = data.get("sensor_id")
    if sensor is not None:
        _require(_integer(sensor) > 0)
    quantity = _text(data.get("quantity"))
    _require(re.fullmatch(r"[a-z][a-z0-9_]*", quantity) is not None)
    owner = f"thermostat:{thermostat}" if sensor is None else f"sensor:{sensor}"
    _require(data.get("quantity_id") == f"{owner}:{quantity}")
    _require(_text(data.get("kind")) in {"measurement", "runtime", "degree_days"})
    _require(data.get("method_version") == METHOD)
    result = {key: deepcopy(data[key]) for key in _DESCRIPTOR if key in data}
    _require(len(result) == len(_DESCRIPTOR))
    _text(data["logical_unit"])
    _statistic(data["statistic_id"])
    result["legacy_statistic_ids"] = _strings(data["legacy_statistic_ids"], 128)
    for alias in result["legacy_statistic_ids"]:
        _statistic(alias)
        _require(alias != data["statistic_id"])
    representation = _object(data["representation"])
    _require("unit_class" in representation)
    _require(representation.get("kind") == "arithmetic_mean")
    _require(representation.get("native_field") == "mean")
    _text(representation.get("unit_of_measurement"))
    if representation.get("unit_class") is not None:
        _text(representation["unit_class"])
    _require(_number(representation.get("logical_multiplier")) is not None)
    _statistic(representation.get("v2_statistic_id"))
    result["representation"] = {
        key: representation[key]
        for key in (
            "kind",
            "native_field",
            "unit_of_measurement",
            "unit_class",
            "logical_multiplier",
            "v2_statistic_id",
        )
    }
    _require(_text(data.get("admission")) in {"eligible", "blocked"})
    _require(_text(data.get("writer_status")) in {"unselected", "reserved", "adopted"})
    result.update(admission=data["admission"], writer_status=data["writer_status"])
    if data["admission"] == "blocked":
        result["blocked_reason"] = _text(data.get("blocked_reason"))
    return result


def _statistic(value: Any) -> str:
    value = _text(value)
    _require(re.fullmatch(r"beestat:[a-z0-9_]+", value) is not None)
    return cast(str, value)


def project_capability(value: Any, entry_id: str) -> dict[str, Any]:
    """Project only declared non-secret public capability fields."""
    data = _object(value)
    _require(
        type(data.get("contract_version")) is int and data["contract_version"] == 3
    )
    _require(
        data.get("method_version") == METHOD and data.get("daily_policy") == POLICY
    )
    services = _object(data.get("services"))
    _require(services.get("coverage") == "beestat_statistics.get_hourly_coverage")
    limits = _object(data.get("limits"))
    projected_limits = {
        key: _integer(limits.get(key)) for key in ("quantities", "days", "page_buckets")
    }
    _require(all(value > 0 for value in projected_limits.values()))
    items = data.get("quantities")
    _require(isinstance(items, list) and len(items) <= 4096)
    quantities = [_descriptor(item) for item in cast(list[Any], items)]
    for key in ("quantity_id", "statistic_id"):
        _require(len({item[key] for item in quantities}) == len(quantities))
    revision = _integer(data.get("root_revision"))
    source = data.get("source_revision")
    return {
        "contract_version": 3,
        "method_version": METHOD,
        "daily_policy": POLICY,
        "identity": _identity(data.get("identity"), entry_id),
        "services": {"coverage": services["coverage"]},
        "limits": projected_limits,
        "root_revision": revision,
        "coverage_revision": _integer(data.get("coverage_revision")),
        "source_revision": None if source is None else _digest(source),
        "operation": _operation(data.get("operation"), revision),
        "quantities": quantities,
    }


def capture_descriptor(
    capability: dict[str, Any], statistic_id: str, quantity: str
) -> dict[str, Any] | None:
    """Capture an exact native destination; aliases never upgrade a legacy ID."""
    matches = [
        item
        for item in capability["quantities"]
        if item["statistic_id"] == statistic_id
    ]
    if not matches:
        return None
    _require(len(matches) == 1)
    descriptor = matches[0]
    spec = _SPECS.get(quantity)
    if (
        spec is None
        or descriptor["kind"] != "measurement"
        or descriptor["quantity"] != spec[0]
    ):
        raise ValueError("historical_unsupported_source")
    representation = descriptor["representation"]
    if (
        descriptor["logical_unit"] != spec[1]
        or representation["unit_of_measurement"] != spec[1]
        or representation["unit_class"] != spec[2]
        or representation["logical_multiplier"] != 1
    ):
        raise ValueError("historical_source_unit_mismatch")
    if descriptor["admission"] != "eligible":
        raise ValueError("historical_unsupported_source")
    return {
        "entry_id": capability["identity"]["entry_id"],
        "identity": deepcopy(capability["identity"]),
        "contract_version": 3,
        "method_version": METHOD,
        "daily_policy": POLICY,
        "descriptor": {key: deepcopy(descriptor[key]) for key in _DESCRIPTOR},
    }


def _intervals(value: Any, start: datetime, end: datetime) -> list[list[str]]:
    _require(isinstance(value, list) and len(value) <= 25)
    result: list[list[str]] = []
    seen: set[datetime] = set()
    for pair in value:
        _require(isinstance(pair, list) and len(pair) == 2)
        first, stop = _stamp(pair[0]), _stamp(pair[1])
        _require(
            start <= first < stop <= end and stop - first == _HOUR and first not in seen
        )
        seen.add(first)
        result.append([first.isoformat(), stop.isoformat()])
    _require(result == sorted(result))
    return result


def _coverage(value: Any, expected: int) -> dict[str, int]:
    data = _object(value)
    _require(set(data) <= _REASONS)
    result = {key: _integer(count, expected) for key, count in data.items()}
    _require(sum(result.values()) == expected)
    return result


def _bucket(
    value: Any, first: datetime, stop: datetime, timezone: str
) -> dict[str, Any]:
    data = _object(value)
    _require(all(key in data for key in ("value", "failure_reason")))
    start, end = _stamp(data.get("start")), _stamp(data.get("end"))
    _require(first <= start < end <= stop)
    local = start.astimezone(ZoneInfo(timezone))
    calendar_start = local.replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(UTC)
    calendar_end = (
        local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    ).astimezone(UTC)
    _require(_stamp(data.get("calendar_start")) == calendar_start)
    _require(_stamp(data.get("calendar_end")) == calendar_end)
    _require(start == max(first, calendar_start) and end == min(stop, calendar_end))
    hours = int((end - start) / _HOUR)
    _require(
        _integer(data.get("calendar_hours"), 25)
        == (calendar_end - calendar_start) / _HOUR
    )
    _require(_integer(data.get("expected_hours"), 25) == hours)
    _require(_integer(data.get("expected_slots"), 300) == hours * 12)
    verified = _integer(data.get("verified_hours"), hours)
    _integer(data.get("valid_slots"), hours * 12)
    _integer(data.get("closed_hours"), hours)
    reason = _text(data.get("reason"))
    _require(reason in _REASONS)
    failure = data.get("failure_reason")
    _require(failure is None or _text(failure) in _FAILURES)
    numbers = {key: _number(data.get(key)) for key in ("value", "min", "max")}
    for key in ("observed_amount", "complete_total", "point_observed_amount"):
        _require(key in data and data[key] is None)
    for key in ("min", "max"):
        _require(key in data)
    _extrema(numbers["value"], numbers["min"], numbers["max"])
    eligible = _intervals(data.get("eligible_intervals"), start, end)
    observed = _intervals(data.get("observed_intervals"), start, end)
    _require(len(observed) == verified)
    coverage = _coverage(data.get("coverage_reasons"), hours)
    _require(coverage.get("ready", 0) == verified)
    _validate_basis(data, eligible, start, end, calendar_start, calendar_end)
    if reason == "ready":
        _require(failure is None)
        _require(verified == hours and data["valid_slots"] == hours * 12)
        _require(data["closed_hours"] == hours and eligible == observed)
    if reason == "partial":
        _require(0 < verified <= hours and numbers["value"] is not None)
    elif reason not in {"ready", "legacy_day"}:
        _require(all(item is None for item in numbers.values()))
    result = {
        key: deepcopy(data[key])
        for key in (
            "start",
            "end",
            "value",
            "min",
            "max",
            "reason",
            "failure_reason",
            "valid_slots",
            "expected_slots",
            "verified_hours",
            "expected_hours",
            "closed_hours",
            "observed_amount",
            "complete_total",
            "source_basis",
            "method_basis",
            "calendar_start",
            "calendar_end",
            "calendar_hours",
            "point_observed_amount",
        )
    }
    result.update(
        confidence=_strings(data.get("confidence"), 128),
        source_ids=_strings(data.get("source_ids")),
        eligible_intervals=eligible,
        observed_intervals=observed,
        coverage_reasons=coverage,
    )
    if "legacy_reason" in data:
        _require(
            _text(data["legacy_reason"])
            in {"legacy_calendar_mismatch", "legacy_day_not_eligible"}
        )
        result["legacy_reason"] = data["legacy_reason"]
    return result


def _extrema(value: float | None, minimum: float | None, maximum: float | None) -> None:
    _require(value is not None or (minimum is None and maximum is None))
    if value is not None:
        _require(minimum is None or minimum <= value)
        _require(maximum is None or maximum >= value)


def _validate_basis(
    data: dict[str, Any],
    eligible: list[list[str]],
    start: datetime,
    end: datetime,
    calendar_start: datetime,
    calendar_end: datetime,
) -> None:
    reason = data["reason"]
    if reason in {"ready", "legacy_day"}:
        _require(
            data["value"] is not None
            and start == calendar_start
            and end == calendar_end
        )
        expected = [
            [(start + i * _HOUR).isoformat(), (start + (i + 1) * _HOUR).isoformat()]
            for i in range(int((end - start) / _HOUR))
        ]
        _require(eligible == expected)
        _require(
            data["source_basis"] == ("points" if reason == "ready" else "legacy_daily")
        )
        _require(
            data["method_basis"]
            == (METHOD if reason == "ready" else "legacy_sample_mean")
        )
        if reason == "legacy_day":
            _require(
                not set(data["coverage_reasons"]) & {"pending", "conflict", "blocked"}
            )
    else:
        _require(not eligible and data.get("method_basis") == METHOD)
        _require(
            data.get("source_basis")
            == ("points" if reason == "partial" else "unavailable")
        )


def _summary(value: Any, first: datetime, stop: datetime) -> dict[str, Any]:
    data = _object(value)
    _require(
        data.get("scope") == "query"
        and data.get("summary_basis") == "verified_point_hours"
    )
    _require(_stamp(data.get("start")) == first and _stamp(data.get("end")) == stop)
    hours = int((stop - first) / _HOUR)
    _require(_integer(data.get("expected_hours")) == hours)
    result = {key: data[key] for key in ("scope", "start", "end", "summary_basis")}
    for key in (
        "verified_hours",
        "expected_hours",
        "closed_hours",
        "eligible_hours",
        "eligible_buckets",
        "legacy_days",
    ):
        result[key] = _integer(data.get(key), hours)
    result["coverage_reasons"] = _coverage(data.get("coverage_reasons"), hours)
    _require(result["coverage_reasons"].get("ready", 0) == result["verified_hours"])
    for key in ("observed_mean", "observed_min", "observed_max"):
        _require(key in data)
        result[key] = _number(data[key])
    _extrema(result["observed_mean"], result["observed_min"], result["observed_max"])
    _require((result["observed_mean"] is None) == (result["verified_hours"] == 0))
    return result


def _view(
    value: Any, contract: dict[str, Any], first: datetime, stop: datetime, timezone: str
) -> dict[str, Any]:
    data = _object(value)
    _require(
        type(data.get("contract_version")) is int and data["contract_version"] == 3
    )
    _require(
        data.get("method_version") == METHOD and data.get("daily_policy") == POLICY
    )
    _require(data.get("period") == "day" and data.get("timezone") == timezone)
    _require(_stamp(data.get("start")) == first and _stamp(data.get("end")) == stop)
    _require(
        _identity(data.get("identity"), contract["entry_id"]) == contract["identity"]
    )
    _require(data.get("native_verification") == "bounded_readback")
    _stamp(data.get("evaluated_at"), hour=False)
    revision = _integer(data.get("root_revision"))
    source = data.get("source_revision")
    return {
        "identity": deepcopy(contract["identity"]),
        "contract_version": 3,
        "method_version": METHOD,
        "daily_policy": POLICY,
        "period": "day",
        "start": first.isoformat(),
        "end": stop.isoformat(),
        "timezone": timezone,
        "timezone_revision": _integer(data.get("timezone_revision")),
        "root_revision": revision,
        "coverage_revision": _integer(data.get("coverage_revision")),
        "source_revision": None if source is None else _digest(source),
        "config_revision": _digest(data.get("config_revision")),
        "view_token": _digest(data.get("view_token")),
        "evaluated_at": data["evaluated_at"],
        "native_verification": "bounded_readback",
        "operation": _operation(data.get("operation"), revision),
    }


async def _call(
    hass: HomeAssistant, payload: dict[str, Any], context: Context | None
) -> dict[str, Any]:
    try:
        response = await hass.services.async_call(
            "beestat_statistics",
            "get_hourly_coverage",
            payload,
            blocking=True,
            return_response=True,
            context=context,
        )
    except (HomeAssistantError, TimeoutError) as err:
        raise ValueError("historical_beestat_unavailable") from err
    return _object(response)


async def async_read_history(
    hass: HomeAssistant,
    contracts: Sequence[dict[str, Any]],
    start: datetime,
    end: datetime,
    timezone: str,
    context: Context | None = None,
) -> HistoryRead:
    """Read every requested day exactly once, pinning all pages to one view."""
    _require(1 <= len(contracts) <= 40)
    first, stop = _stamp(start.isoformat()), _stamp(end.isoformat())
    _require(first < stop and (stop - first) <= timedelta(days=32))
    days = _day_count(first, stop, timezone)
    _require(days <= 31)
    owner = contracts[0]
    _require(
        all(
            item["entry_id"] == owner["entry_id"]
            and item["identity"] == owner["identity"]
            for item in contracts
        )
    )
    by_quantity = {item["descriptor"]["quantity_id"]: item for item in contracts}
    _require(len(by_quantity) == len(contracts))
    payload = {
        "config_entry_id": owner["entry_id"],
        "contract_version": 3,
        "quantity_ids": list(by_quantity),
        "start": first.isoformat(),
        "end": stop.isoformat(),
        "period": "day",
        "daily_policy": POLICY,
        "page_size": 1240,
        "offset": 0,
    }
    buckets: dict[str, list[dict[str, Any]]] = {
        item["descriptor"]["statistic_id"]: [] for item in contracts
    }
    _require(len(buckets) == len(contracts))
    summaries: dict[str, dict[str, Any]] = {}
    provenance: dict[str, Any] | None = None
    while True:
        response = await _call(hass, payload, context)
        view = _view(response, owner, first, stop, timezone)
        if provenance is None:
            provenance = view
        elif view != provenance:
            raise ValueError("historical_source_changed")
        count = _collect(
            response, by_quantity, buckets, summaries, first, stop, timezone
        )
        pagination = _object(response.get("pagination"))
        _require(_integer(pagination.get("offset")) == payload["offset"])
        _require(
            _integer(pagination.get("total_buckets"), 1240) == days * len(contracts)
        )
        offset = payload["offset"] + count
        _require(
            0 < count <= payload["page_size"] and offset <= pagination["total_buckets"]
        )
        _require(type(pagination.get("has_more")) is bool)
        more = offset < pagination["total_buckets"]
        _require(pagination["has_more"] == more)
        if not more:
            _require(pagination.get("next_offset") is None)
            break
        _require(_integer(pagination.get("next_offset")) == offset)
        payload = {
            **payload,
            "offset": offset,
            "view_token": view["view_token"],
            "evaluated_at": view["evaluated_at"],
        }
    _complete(buckets, summaries, days, first, stop)
    replay = {
        **payload,
        "view_token": provenance["view_token"],
        "evaluated_at": provenance["evaluated_at"],
    }
    return HistoryRead(buckets, summaries, provenance, replay, deepcopy(response))


def _day_count(first: datetime, stop: datetime, timezone: str) -> int:
    zone = ZoneInfo(timezone)
    return (
        (stop - timedelta(microseconds=1)).astimezone(zone).date()
        - first.astimezone(zone).date()
    ).days + 1


def _collect(
    response: dict[str, Any],
    contracts: dict[str, dict[str, Any]],
    buckets: dict[str, list[dict[str, Any]]],
    summaries: dict[str, dict[str, Any]],
    first: datetime,
    stop: datetime,
    timezone: str,
) -> int:
    series = response.get("series")
    _require(isinstance(series, list) and 0 < len(series) <= len(contracts))
    seen: set[str] = set()
    count = 0
    for raw_item in cast(list[Any], series):
        item = _object(raw_item)
        descriptor = _descriptor(item.get("descriptor"))
        quantity = descriptor["quantity_id"]
        _require(quantity in contracts and quantity not in seen)
        seen.add(quantity)
        contract = contracts[quantity]["descriptor"]
        if {key: descriptor[key] for key in _DESCRIPTOR} != contract:
            raise ValueError("historical_source_changed")
        statistic = descriptor["statistic_id"]
        summary = _summary(item.get("summary"), first, stop)
        if statistic in summaries:
            _require(summaries[statistic] == summary)
        summaries[statistic] = summary
        values = item.get("buckets")
        _require(isinstance(values, list) and 0 < len(values) <= 31)
        for value in cast(list[Any], values):
            bucket = _bucket(value, first, stop, timezone)
            if bucket["eligible_intervals"]:
                _require(
                    _stamp(bucket["end"])
                    <= _stamp(response["evaluated_at"], hour=False)
                )
            if descriptor["admission"] != "eligible":
                # Pending/conflict can take precedence over blocked admission.
                # Committed interval proof, not writer progress, grants eligibility.
                _require(not bucket["eligible_intervals"])
            if bucket["source_basis"] == "legacy_daily":
                _require(bool(bucket["source_ids"]))
                _require(
                    set(bucket["source_ids"]) <= set(descriptor["legacy_statistic_ids"])
                )
            elif bucket["source_basis"] == "points":
                _require(bool(bucket["source_ids"]))
                for source_id in bucket["source_ids"]:
                    _digest(source_id)
            previous = buckets[statistic]
            if previous:
                _require(_stamp(previous[-1]["end"]) == _stamp(bucket["start"]))
            else:
                _require(_stamp(bucket["start"]) == first)
            previous.append(bucket)
            count += 1
    return count


def _complete(
    buckets: dict[str, list[dict[str, Any]]],
    summaries: dict[str, dict[str, Any]],
    days: int,
    first: datetime,
    stop: datetime,
) -> None:
    for statistic, values in buckets.items():
        _require(
            len(values) == days
            and _stamp(values[0]["start"]) == first
            and _stamp(values[-1]["end"]) == stop
        )
        summary = summaries[statistic]
        for key in ("verified_hours", "expected_hours", "closed_hours"):
            _require(sum(item[key] for item in values) == summary[key])
        _require(
            sum(len(item["eligible_intervals"]) for item in values)
            == summary["eligible_hours"]
        )
        _require(
            sum(bool(item["eligible_intervals"]) for item in values)
            == summary["eligible_buckets"]
        )
        _require(
            sum(item["reason"] == "legacy_day" for item in values)
            == summary["legacy_days"]
        )
        for reason in _REASONS:
            _require(
                sum(item["coverage_reasons"].get(reason, 0) for item in values)
                == summary["coverage_reasons"].get(reason, 0)
            )


async def async_validate_read(
    hass: HomeAssistant, read: HistoryRead, context: Context | None = None
) -> None:
    """Fence an assembled report against late native/configuration changes."""
    response = await _call(hass, deepcopy(read.replay_payload), context)
    try:
        same = json.dumps(response, sort_keys=True, allow_nan=False) == json.dumps(
            read.replay_response, sort_keys=True, allow_nan=False
        )
    except (TypeError, ValueError) as err:
        raise ValueError(_ERROR) from err
    if not same:
        raise ValueError("historical_source_changed")
