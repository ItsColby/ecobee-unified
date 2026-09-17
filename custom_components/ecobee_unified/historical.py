"""Bounded, on-demand daily reads of explicitly bound native statistics."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from .beestat_history import HistoryRead, async_read_history, async_validate_read

MAX_DAYS = 31
MAX_FAMILIES = 16
READ_TIMEOUT_SECONDS = 30
_MEASURES = ("mean", "min", "max")
_UNITS = {
    "temperature": frozenset({"°C", "°F"}),
    "humidity": frozenset({"%"}),
    "co2": frozenset({"ppm"}),
    "aqi": frozenset({"0-100"}),
    "voc": frozenset({"native"}),
    "battery": frozenset({"%"}),
    "weather_temperature": frozenset({"°C", "°F"}),
    "weather_humidity": frozenset({"%"}),
}


def _text(value: Any, code: str, limit: int = 255) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(code)
    return value


@dataclass(frozen=True, slots=True)
class HistoricalFamily:
    """A saved identity assertion and explicit whole-day source policy."""

    family_id: str
    name: str
    quantity: str
    unit: str
    timezone: str
    anchor_ref: str
    sources: tuple[dict[str, Any], ...]
    policy: str = "fixed_source"
    accept_cross_method: bool = False

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> HistoricalFamily:
        """Validate persisted shape; native source admission is a separate read."""
        quantity = value.get("quantity")
        if not isinstance(quantity, str) or quantity not in _UNITS:
            raise ValueError("historical_quantity_invalid")
        unit = value.get("unit")
        if not isinstance(unit, str) or unit not in _UNITS[quantity]:
            raise ValueError("historical_unit_invalid")
        timezone = _text(value.get("timezone"), "historical_timezone_invalid")
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as err:
            raise ValueError("historical_timezone_invalid") from err
        policy = value.get("policy", "fixed_source")
        if not isinstance(policy, str) or policy not in {
            "fixed_source",
            "ordered_daily",
        }:
            raise ValueError("historical_policy_invalid")
        accepted = value.get("accept_cross_method", False)
        if not isinstance(accepted, bool):
            raise ValueError("historical_policy_invalid")  # noqa: TRY004
        sources = _parse_sources(value.get("sources"))
        _validate_methods(sources, policy, accepted)
        return cls(
            family_id=_text(value.get("family_id"), "historical_family_invalid", 128),
            name=_text(value.get("name"), "historical_family_invalid", 128),
            quantity=quantity,
            unit=unit,
            timezone=timezone,
            anchor_ref=_text(value.get("anchor_ref"), "historical_family_invalid", 128),
            sources=sources,
            policy=policy,
            accept_cross_method=accepted,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return an independent storage projection, retaining source extensions."""
        return {
            "family_id": self.family_id,
            "name": self.name,
            "quantity": self.quantity,
            "unit": self.unit,
            "timezone": self.timezone,
            "anchor_ref": self.anchor_ref,
            "sources": deepcopy(list(self.sources)),
            "policy": self.policy,
            "accept_cross_method": self.accept_cross_method,
        }


def _parse_sources(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 3:
        raise ValueError("historical_sources_invalid")
    sources: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    statistic_ids: set[str] = set()
    for row in value:
        if not isinstance(row, dict):
            raise ValueError("historical_sources_invalid")  # noqa: TRY004
        source_id = _text(row.get("source_id"), "historical_sources_invalid", 128)
        statistic_id = _text(row.get("statistic_id"), "historical_sources_invalid")
        if source_id in source_ids or statistic_id in statistic_ids:
            raise ValueError("historical_sources_invalid")
        source_ids.add(source_id)
        statistic_ids.add(statistic_id)
        sources.append(deepcopy(row))
    return tuple(sources)


def _validate_methods(
    sources: Sequence[dict[str, Any]], policy: str, accepted: bool
) -> None:
    methods = {
        (
            _text(row.get("method"), "historical_sources_invalid"),
            _text(row.get("transformation", "identity"), "historical_sources_invalid"),
        )
        for row in sources
    }
    if not accepted and (
        (policy == "ordered_daily" and len(methods) > 1)
        or any("beestat_history_v3" in row for row in sources)
    ):
        # The producer's declared daily policy itself switches between complete
        # point evidence and a qualified legacy day, even for one fixed source.
        raise ValueError("historical_cross_method_required")


@dataclass(frozen=True, slots=True)
class _Window:
    start: date
    end: date
    timezone: str
    boundaries: tuple[datetime, ...]

    @property
    def start_utc(self) -> datetime:
        return self.boundaries[0]

    @property
    def end_utc(self) -> datetime:
        return self.boundaries[-1]


@dataclass(frozen=True, slots=True)
class _NativeRows:
    rows: list[dict[str, Any]]
    returned: bool

    @property
    def disposition(self) -> str:
        if not self.returned:
            return "statistic_not_returned"
        return "rows_returned" if self.rows else "empty_rows"


def _date(value: date | str) -> date:
    if isinstance(value, datetime):
        raise ServiceValidationError("historical_dates_invalid")
    if isinstance(value, date):
        return value
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as err:
        raise ServiceValidationError("historical_dates_invalid") from err
    if parsed.isoformat() != value:
        raise ServiceValidationError("historical_dates_invalid")
    return parsed


def _window(hass: HomeAssistant, start: date | str, end: date | str) -> _Window:
    first, last = _date(start), _date(end)
    zone = ZoneInfo(hass.config.time_zone)
    days = (last - first).days
    if not 1 <= days <= MAX_DAYS or last > datetime.now(zone).date() + timedelta(
        days=1
    ):
        raise ServiceValidationError("historical_dates_invalid")
    if str(dt_util.DEFAULT_TIME_ZONE) != hass.config.time_zone:
        raise ServiceValidationError("historical_timezone_mismatch")
    boundaries = tuple(
        datetime.combine(first + timedelta(days=offset), time.min, zone).astimezone(UTC)
        for offset in range(days + 1)
    )
    # Recorder hour bins are UTC-aligned. Fractional-offset calendars need a
    # different coverage contract; counting overlapping bins would overstate it.
    if any(boundary.timestamp() % 3600 for boundary in boundaries):
        raise ServiceValidationError("historical_timezone_alignment_unsupported")
    return _Window(first, last, hass.config.time_zone, boundaries)


def _revision(entry: ConfigEntry[Any]) -> str:
    payload = json.dumps(
        {"data": dict(entry.data), "options": dict(entry.options)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode()).hexdigest()


class HistoricalManager:
    """Read-only selector; no provider acquisition, import, cache or polling."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry[Any],
        families: tuple[HistoricalFamily, ...],
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.families = tuple(
            HistoricalFamily.from_dict(row.as_dict()) for row in families
        )
        if len({row.family_id for row in self.families}) != len(self.families):
            raise ValueError("historical_family_invalid")
        self._active = True
        self._inflight: asyncio.Task[Any] | None = None

    async def async_stop(self) -> None:
        """Fence any pending response before the config entry unloads."""
        self._active = False
        if self._inflight is not None and self._inflight is not asyncio.current_task():
            self._inflight.cancel()

    async def async_read(
        self,
        *,
        family_ids: Sequence[str] | None,
        start_date: date | str,
        end_date: date | str,
        include_provisional: bool = False,
        context: Context | None = None,
    ) -> dict[str, Any]:
        """Return each calendar date, keeping all source candidates inspectable."""
        self._require_runtime()
        if self._inflight is not None:
            raise ServiceValidationError("historical_busy")
        if not isinstance(include_provisional, bool):
            raise ServiceValidationError("historical_request_invalid")
        families = self._select_families(family_ids)
        window = _window(self.hass, start_date, end_date)
        if any(family.timezone != window.timezone for family in families):
            raise ServiceValidationError("historical_timezone_mismatch")
        revision = _revision(self.entry)
        self._inflight = asyncio.current_task()
        try:
            async with asyncio.timeout(READ_TIMEOUT_SECONDS):
                return await self._read(
                    families, window, include_provisional, context, revision
                )
        except TimeoutError as err:
            raise HomeAssistantError("historical_timeout") from err
        except asyncio.CancelledError as err:
            if not self._active:
                raise HomeAssistantError("historical_unloaded") from err
            raise
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        finally:
            self._inflight = None

    def _select_families(
        self, ids: Sequence[str] | None
    ) -> tuple[HistoricalFamily, ...]:
        if ids is None:
            ids = [row.family_id for row in self.families]
        if (
            isinstance(ids, str)
            or not 1 <= len(ids) <= MAX_FAMILIES
            or any(not isinstance(item, str) for item in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ServiceValidationError("historical_families_invalid")
        configured = {row.family_id: row for row in self.families}
        if any(item not in configured for item in ids):
            raise ServiceValidationError("historical_families_invalid")
        return tuple(configured[item] for item in ids)

    def _guard(self, revision: str, window: _Window) -> None:
        self._require_runtime()
        if _revision(self.entry) != revision:
            raise ServiceValidationError("historical_configuration_changed")
        if (
            self.hass.config.time_zone != window.timezone
            or str(dt_util.DEFAULT_TIME_ZONE) != window.timezone
        ):
            raise ServiceValidationError("historical_timezone_mismatch")

    def _require_runtime(self) -> None:
        if (
            not self._active
            or self.entry.state is not ConfigEntryState.LOADED
            or self.hass.config_entries.async_get_entry(self.entry.entry_id)
            is not self.entry
            or getattr(getattr(self.entry, "runtime_data", None), "history", None)
            is not self
        ):
            raise ServiceValidationError("historical_unloaded")

    async def _validate(
        self, families: tuple[HistoricalFamily, ...], context: Context | None
    ) -> dict[str, tuple[dict[str, Any], ...]]:
        # Isolate the source adapter from the purely persisted model import.
        from .history_source import async_validate_source  # noqa: PLC0415

        result: dict[str, tuple[dict[str, Any], ...]] = {}
        validation_cache: dict[str, Any] = {}
        for family in families:
            sources = []
            for source in family.sources:
                current = await async_validate_source(
                    self.hass,
                    deepcopy(source),
                    family.quantity,
                    family.anchor_ref,
                    context=context,
                    validation_cache=validation_cache,
                )
                if current.get("timezone") != family.timezone:
                    raise ValueError("historical_timezone_mismatch")
                sources.append(current)
            _validate_methods(sources, family.policy, family.accept_cross_method)
            result[family.family_id] = tuple(sources)
        return result

    async def _read(
        self,
        families: tuple[HistoricalFamily, ...],
        window: _Window,
        include_provisional: bool,
        context: Context | None,
        revision: str,
    ) -> dict[str, Any]:
        acquired_start = dt_util.utcnow()
        sources = await self._validate(families, context)
        self._guard(revision, window)
        daily: dict[str, _NativeRows] = {}
        hourly: dict[str, _NativeRows] = {}
        reads: list[dict[str, Any]] = []
        producer_reads: list[HistoryRead] = []
        producer_buckets: dict[str, list[dict[str, Any]]] = {}
        for contracts in _producer_groups(sources):
            self._guard(revision, window)
            read = await async_read_history(
                self.hass,
                contracts=contracts,
                start=window.start_utc,
                end=window.end_utc,
                timezone=window.timezone,
                context=context,
            )
            self._guard(revision, window)
            producer_reads.append(read)
            producer_buckets.update(read.buckets)
        for ids, units in _read_groups(sources):
            for period, destination in (("day", daily), ("hour", hourly)):
                self._guard(revision, window)
                started = dt_util.utcnow()
                end = window.end_utc
                if period == "day":
                    end -= timedelta(microseconds=1)
                payload = {
                    "statistic_ids": ids,
                    "start_time": window.start_utc.isoformat(),
                    "end_time": end.isoformat(),
                    "period": period,
                    "types": list(_MEASURES),
                    "units": units,
                }
                response = await self.hass.services.async_call(
                    "recorder",
                    "get_statistics",
                    payload,
                    blocking=True,
                    return_response=True,
                    context=context,
                )
                self._guard(revision, window)
                destination.update(_statistics(response, ids))
                reads.append(
                    {
                        "period": period,
                        "statistic_ids": ids,
                        "start_time": payload["start_time"],
                        "end_time": payload["end_time"],
                        "units": units,
                        "acquired_start": started.isoformat(),
                        "acquired_end": dt_util.utcnow().isoformat(),
                    }
                )
        # Recheck identity/metadata. Keep the timing captured before acquisition:
        # a later advancing watermark cannot settle an earlier values response.
        await self._validate(families, context)
        for read in producer_reads:
            self._guard(revision, window)
            await async_validate_read(self.hass, read, context=context)
        self._guard(revision, window)
        acquired_end = dt_util.utcnow()
        return {
            "schema_version": 1,
            "config_revision": revision,
            "period": "day",
            "start_date": window.start.isoformat(),
            "end_date": window.end.isoformat(),
            "timezone": window.timezone,
            "include_provisional": include_provisional,
            "acquired_start": acquired_start.isoformat(),
            "acquired_end": acquired_end.isoformat(),
            "snapshot_consistency": (
                "separate_native_and_producer_reads"
                if producer_reads and reads
                else "producer_qualified_reads"
                if producer_reads
                else "separate_native_reads"
            ),
            "native_reads": reads,
            **(
                {
                    "producer_reads": [
                        read.provenance | {"summaries": deepcopy(read.summaries)}
                        for read in producer_reads
                    ]
                }
                if producer_reads
                else {}
            ),
            "families": [
                _family_result(
                    family,
                    sources[family.family_id],
                    window,
                    daily,
                    hourly,
                    include_provisional,
                    acquired_start,
                    producer_buckets,
                )
                for family in families
            ],
        }


def _read_groups(
    families: dict[str, tuple[dict[str, Any], ...]],
) -> list[tuple[list[str], dict[str, str]]]:
    groups: dict[tuple[str | None, str | None], list[str]] = {}
    contracts: dict[str, tuple[str | None, str | None]] = {}
    for sources in families.values():
        for source in sources:
            if "beestat_history_v3" in source:
                continue
            metadata = source["metadata"]
            unit_class, unit = metadata.get("unit_class"), source["native_unit"]
            key = (unit_class, unit)
            identifier = source["statistic_id"]
            if identifier in contracts and contracts[identifier] != key:
                raise ValueError("historical_source_contract_changed")
            contracts[identifier] = key
            group = groups.setdefault(key, [])
            if identifier not in group:
                group.append(identifier)
    # Explicit stored units suppress HA's per-entity display-unit conversion.
    return [
        (ids, {kind: unit} if kind is not None and unit is not None else {})
        for (kind, unit), ids in groups.items()
    ]


def _producer_groups(
    families: dict[str, tuple[dict[str, Any], ...]],
) -> list[list[dict[str, Any]]]:
    groups: dict[str, dict[str, dict[str, Any]]] = {}
    for sources in families.values():
        for source in sources:
            contract = source.get("beestat_history_v3")
            if contract is None:
                continue
            identifier = source["statistic_id"]
            group = groups.setdefault(contract["entry_id"], {})
            if identifier in group and group[identifier] != contract:
                raise ValueError("historical_source_contract_changed")
            group[identifier] = contract
    return [list(group.values()) for group in groups.values()]


def _statistics(response: Any, identifiers: list[str]) -> dict[str, _NativeRows]:
    if not isinstance(response, dict) or not isinstance(
        response.get("statistics"), dict
    ):
        raise ValueError("historical_response_invalid")  # noqa: TRY004
    result = {}
    for identifier in identifiers:
        rows = response["statistics"].get(identifier, [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("historical_response_invalid")
        result[identifier] = _NativeRows(rows, identifier in response["statistics"])
    return result


def _timestamp(value: Any) -> datetime:
    parsed = dt_util.parse_datetime(value) if isinstance(value, str) else None
    if parsed is None or parsed.tzinfo is None:
        raise ValueError("historical_response_invalid")
    return parsed.astimezone(UTC)


def _finite_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("historical_response_invalid")  # noqa: TRY004 -- Public validation boundary.
    try:
        number = float(value)
    except OverflowError as err:
        raise ValueError("historical_response_invalid") from err
    if not isfinite(number):
        raise ValueError("historical_response_invalid")
    return number


def _values(row: dict[str, Any] | None) -> dict[str, float | None]:
    return {
        measure: _finite_value(row.get(measure) if row else None)
        for measure in _MEASURES
    }


def _day_row(
    rows: list[dict[str, Any]], start: datetime, end: datetime
) -> dict[str, Any] | None:
    matches = [row for row in rows if start <= _timestamp(row.get("start")) < end]
    if len(matches) > 1 or (
        matches
        and (
            _timestamp(matches[0]["start"]) != start
            or _timestamp(matches[0].get("end")) != end
        )
    ):
        raise ValueError("historical_response_invalid")
    return matches[0] if matches else None


def _coverage(
    source: dict[str, Any], rows: list[dict[str, Any]], start: datetime, end: datetime
) -> dict[str, Any]:
    legacy = str(source["method"]).startswith("beestat_legacy_daily_")
    matched = [row for row in rows if start <= _timestamp(row.get("start")) < end]
    starts = [_timestamp(row["start"]) for row in matched]
    if (
        len(set(starts)) != len(starts)
        or any(stamp.timestamp() % 3600 for stamp in starts)
        or any(
            _timestamp(row.get("end")) != stamp + timedelta(hours=1)
            for row, stamp in zip(matched, starts, strict=True)
        )
    ):
        raise ValueError("historical_response_invalid")
    expected = 1 if legacy else int((end - start).total_seconds() / 3600)
    if legacy and (len(starts) > 1 or (starts and starts[0] != start)):
        raise ValueError("historical_source_contract_changed")
    return {
        "native_period": "legacy_daily_in_hourly_table" if legacy else "hour",
        "native_bins_present": len(starts),
        "native_bins_expected": expected,
        "bin_coverage": "bins_complete" if len(starts) == expected else "bins_partial",
        "sample_count": None,
        "sample_coverage": "unknown",
    }


def _transformed(
    family: HistoricalFamily, source: dict[str, Any], values: dict[str, float | None]
) -> dict[str, float | None]:
    def convert(value: float | None) -> float | None:
        if value is None:
            return None
        try:
            if family.quantity in {"temperature", "weather_temperature"}:
                value = TemperatureConverter.convert(
                    value, source["native_unit"], family.unit
                )
            elif source.get("transformation") == "aqi_raw_div350_times100":
                value = value / 350 * 100
        except OverflowError as err:
            raise ValueError("historical_response_invalid") from err
        return _finite_value(value)

    return {measure: convert(value) for measure, value in values.items()}


def _timing(source: dict[str, Any], end: datetime, now: datetime) -> dict[str, Any]:
    reasons = []
    if end > now:
        reasons.append("open_calendar_day")
    through = source.get("provider_data_through")
    if through is not None and _timestamp(through) < end:
        reasons.append("provider_window_before_day_end")
    return {
        "provisional": bool(reasons),
        "provisional_reasons": reasons,
        # No supported source currently exposes per-statistic import coverage.
        # Unknown saved extension fields cannot acquire this authority.
        "settlement": "unknown",
        "provider_thermostat_data_end": through,
        "provider_window_begin": source.get("provider_window_begin"),
        "import_completed_at": source.get("import_completed_at"),
        "sensor_observed_through": None,
        "statistic_imported_through": None,
        "provider_data_through_ref": source.get("provider_data_through_ref"),
    }


def _candidate(
    family: HistoricalFamily,
    source: dict[str, Any],
    daily: _NativeRows,
    hourly: _NativeRows,
    start: datetime,
    end: datetime,
    include_provisional: bool,
    now: datetime,
) -> dict[str, Any]:
    row = _day_row(daily.rows, start, end)
    native_values = _values(row)
    timing = _timing(source, end, now)
    status = "eligible"
    if row is None:
        status = "no_rows"
    elif native_values["mean"] is None:
        status = "missing_mean"
    elif family.quantity == "voc":
        status = "voc_unit_contract_unresolved"
    elif timing["provisional"] and not include_provisional:
        status = "provisional_excluded"
    return {
        "source_id": source["source_id"],
        "statistic_id": source["statistic_id"],
        "status": status,
        "native_response": {"day": daily.disposition, "hour": hourly.disposition},
        "native_values": native_values,
        "native_unit": source["native_unit"],
        "values": _transformed(family, source, native_values)
        if family.quantity != "voc"
        else None,
        "unit": family.unit if family.quantity != "voc" else source["native_unit"],
        "missing_measures": {
            measure: "no_rows" if row is None else "unsupported_or_missing_measure"
            for measure, value in native_values.items()
            if value is None
        },
        "aggregate_interval": "day",
        "method": source["method"],
        "transformation": source.get("transformation", "identity"),
        "rounding": (
            "linear_scale_daily_aggregate_no_rounding"
            if source.get("transformation") == "aqi_raw_div350_times100"
            else "provider_rounded_samples_before_aggregation"
            if family.quantity == "aqi" and str(source["method"]).startswith("beestat_")
            else "native_aggregate_preserved"
        ),
        "identity_evidence": deepcopy(source.get("identity_evidence", {})),
        "historical_identity_continuity": "unknown",
        "coverage": _coverage(source, hourly.rows, start, end),
        **timing,
    }


def _producer_candidate(
    family: HistoricalFamily,
    source: dict[str, Any],
    buckets: list[dict[str, Any]],
    start: datetime,
    end: datetime,
    now: datetime,
) -> dict[str, Any]:
    """Preserve the producer's chosen daily basis and its eligibility proof."""
    bucket = _day_row(buckets, start, end)
    if bucket is None:
        raise ValueError("historical_response_invalid")
    native_values = _values(
        {"mean": bucket["value"], "min": bucket["min"], "max": bucket["max"]}
    )
    reason = bucket["reason"]
    status = (
        "eligible"
        if reason in {"ready", "legacy_day"} and native_values["mean"] is not None
        else f"producer_{reason}"
    )
    reasons = []
    if end > now:
        reasons.append("open_calendar_day")
    if bucket["source_basis"] == "points" and bucket["coverage_reasons"].get(
        "provisional", 0
    ):
        reasons.append("producer_provisional_hours")
    # Inclusion may disclose provisional observations; it cannot grant missing
    # producer eligibility or combine a partial subtotal with a legacy day.
    if status == "eligible" and (reasons or not bucket["eligible_intervals"]):
        raise ValueError("historical_response_invalid")
    return {
        "source_id": source["source_id"],
        "statistic_id": source["statistic_id"],
        "status": status,
        "native_response": {"producer": "qualified_day_returned"},
        "native_values": native_values,
        "native_unit": source["native_unit"],
        "values": _transformed(family, source, native_values),
        "unit": family.unit,
        "missing_measures": {
            measure: "unsupported_or_missing_measure"
            for measure, value in native_values.items()
            if value is None
        },
        "aggregate_interval": "day",
        "method": bucket["method_basis"],
        "source_method": source["method"],
        "transformation": source.get("transformation", "identity"),
        "rounding": "producer_method_preserved",
        "identity_evidence": deepcopy(source.get("identity_evidence", {})),
        "historical_identity_continuity": "unknown",
        "coverage": {
            "native_period": "producer_qualified_day",
            "native_bins_present": bucket["verified_hours"],
            "native_bins_expected": bucket["expected_hours"],
            "bin_coverage": (
                "bins_complete"
                if bucket["verified_hours"] == bucket["expected_hours"]
                else "bins_partial"
            ),
            "sample_count": None,
            "sample_coverage": "qualified_producer_evidence",
            "point_valid_slots": bucket["valid_slots"],
            "point_expected_slots": bucket["expected_slots"],
            "source_basis": bucket["source_basis"],
            "method_basis": bucket["method_basis"],
            "eligible_intervals": deepcopy(bucket["eligible_intervals"]),
            "observed_intervals": deepcopy(bucket["observed_intervals"]),
            "coverage_reasons": deepcopy(bucket["coverage_reasons"]),
            "confidence": list(bucket["confidence"]),
            "source_ids": list(bucket["source_ids"]),
        },
        "producer_bucket": deepcopy(bucket),
        "provisional": bool(reasons),
        "provisional_reasons": reasons,
        "settlement": "unknown",
        "provider_thermostat_data_end": None,
        "provider_window_begin": None,
        "import_completed_at": None,
        "sensor_observed_through": None,
        "statistic_imported_through": None,
        "provider_data_through_ref": None,
    }


def _family_result(
    family: HistoricalFamily,
    sources: tuple[dict[str, Any], ...],
    window: _Window,
    daily: dict[str, _NativeRows],
    hourly: dict[str, _NativeRows],
    include_provisional: bool,
    now: datetime,
    producer_buckets: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    rows = []
    for offset, (start, end) in enumerate(
        zip(window.boundaries[:-1], window.boundaries[1:], strict=True)
    ):
        candidates = [
            _producer_candidate(
                family,
                source,
                producer_buckets[source["statistic_id"]],
                start,
                end,
                now,
            )
            if "beestat_history_v3" in source
            else _candidate(
                family,
                source,
                daily[source["statistic_id"]],
                hourly[source["statistic_id"]],
                start,
                end,
                include_provisional,
                now,
            )
            for source in sources
        ]
        eligible = candidates[:1] if family.policy == "fixed_source" else candidates
        selected = next(
            (item for item in eligible if item["status"] == "eligible"), None
        )
        rows.append(
            {
                "date": (window.start + timedelta(days=offset)).isoformat(),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "source_id": selected["source_id"] if selected else None,
                "statistic_id": selected["statistic_id"] if selected else None,
                "values": selected["values"] if selected else dict.fromkeys(_MEASURES),
                "unit": family.unit,
                "selection_reason": (
                    "fixed_source"
                    if selected and family.policy == "fixed_source"
                    else "primary_source"
                    if selected is candidates[0]
                    else "ordered_fallback"
                    if selected
                    else "no_eligible_source"
                ),
                "selected": selected,
                "candidates": candidates,
            }
        )
    return {
        "family_id": family.family_id,
        "name": family.name,
        "quantity": family.quantity,
        "unit": family.unit,
        "policy": family.policy,
        "accept_cross_method": family.accept_cross_method,
        "single_source": len(sources) == 1,
        "rows": rows,
    }
