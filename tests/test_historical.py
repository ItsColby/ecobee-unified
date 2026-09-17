"""Daily selection and lifetime contracts through the native HA service bus."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (  # type: ignore[import-untyped]
    MockConfigEntry,
)

from custom_components.ecobee_unified.historical import (
    HistoricalFamily,
    HistoricalManager,
)

from .runtime_fixture import CoreRuntimeTestCase


def _source(
    identifier: str = "sensor.room_temperature",
    *,
    unit: str | None = "°C",
    method: str = "recorder_hourly_time_weighted",
    transformation: str = "identity",
) -> dict[str, Any]:
    return {
        "source_id": identifier.replace(":", "_"),
        "statistic_id": identifier,
        "native_unit": unit,
        "metadata": {
            "source": "beestat" if identifier.startswith("beestat:") else "recorder",
            "unit": unit,
            "display_unit": unit,
            "unit_class": "temperature" if unit in {"°C", "°F"} else None,
            "mean_type": 1,
        },
        "method": method,
        "transformation": transformation,
        "identity_evidence": {"historical_continuity": "unknown"},
        "anchor_ref": "stable-anchor",
        "timezone": "America/New_York",
        "settlement_basis": None,
    }


def _family(
    sources: list[dict[str, Any]] | None = None, **overrides: Any
) -> HistoricalFamily:
    return HistoricalFamily.from_dict(
        {
            "family_id": "room",
            "name": "Room",
            "quantity": "temperature",
            "unit": "°F",
            "timezone": "America/New_York",
            "anchor_ref": "stable-anchor",
            "sources": sources if sources is not None else [_source()],
            **overrides,
        }
    )


class HistoricalReadTests(CoreRuntimeTestCase):
    """Recorder response fixtures exercise actual ServiceRegistry dispatch."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.original_zone = dt_util.DEFAULT_TIME_ZONE
        await self.hass.config.async_set_time_zone("America/New_York")
        self.entry = MockConfigEntry(
            domain="ecobee_unified", data={"historical_families": []}
        )
        self.entry.add_to_hass(self.hass)
        self.entry.mock_state(self.hass, ConfigEntryState.LOADED)
        self.reads: list[ServiceCall] = []
        self.daily: dict[str, list[dict[str, Any]]] = {}
        self.hourly: dict[str, list[dict[str, Any]]] = {}
        self.read_effect: Any = None
        self.validate = AsyncMock(side_effect=self._validated)
        self.validator_patch = patch(
            "custom_components.ecobee_unified.history_source.async_validate_source",
            self.validate,
        )
        self.validator_patch.start()
        self.hass.services.async_register(
            "recorder",
            "get_statistics",
            self._recorded_read,
            supports_response=SupportsResponse.ONLY,
        )
        self.histories: list[HistoricalManager] = []

    async def asyncTearDown(self) -> None:
        for manager in self.histories:
            await manager.async_stop()
        self.validator_patch.stop()
        dt_util.set_default_time_zone(self.original_zone)
        await super().asyncTearDown()

    async def _validated(
        self,
        hass: Any,
        source: dict[str, Any],
        quantity: str,
        anchor: str,
        *,
        context: Context | None = None,
        validation_cache: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return deepcopy(source)

    async def _recorded_read(self, call: ServiceCall) -> dict[str, Any]:
        self.reads.append(call)
        if self.read_effect is not None:
            await self.read_effect(call)
        rows = self.daily if call.data["period"] == "day" else self.hourly
        return {
            "statistics": {
                identifier: deepcopy(rows[identifier])
                for identifier in call.data["statistic_ids"]
                if identifier in rows
            }
        }

    def _manager(self, *families: HistoricalFamily) -> HistoricalManager:
        manager = HistoricalManager(self.hass, self.entry, families or (_family(),))
        self.entry.runtime_data = SimpleNamespace(history=manager)
        self.histories.append(manager)
        return manager

    async def _read(
        self, manager: HistoricalManager | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        return await (manager or self._manager()).async_read(
            **{
                "family_ids": ["room"],
                "start_date": "2026-03-08",
                "end_date": "2026-03-09",
                **kwargs,
            }
        )

    def _row(
        self,
        identifier: str = "sensor.room_temperature",
        *,
        start: str = "2026-03-08T05:00:00+00:00",
        mean: float = 20,
        minimum: float | None = 19,
        maximum: float | None = 21,
        hours: int = 23,
    ) -> None:
        first = datetime.fromisoformat(start)
        end = (
            first.astimezone(dt_util.DEFAULT_TIME_ZONE) + timedelta(days=1)
        ).astimezone(UTC)
        self.daily[identifier] = [
            {"start": start, "end": end.isoformat(), "mean": mean}
        ]
        if minimum is not None:
            self.daily[identifier][0]["min"] = minimum
        if maximum is not None:
            self.daily[identifier][0]["max"] = maximum
        self.hourly[identifier] = [
            {
                "start": (first + timedelta(hours=hour)).isoformat(),
                "end": (first + timedelta(hours=hour + 1)).isoformat(),
                "mean": mean,
            }
            for hour in range(hours)
        ]

    async def test_native_payload_context_exclusive_end_and_spring_dst(self) -> None:
        self._row()
        context = Context(user_id="native-admin-user")
        result = await self._read(context=context)
        self.assertEqual(len(self.reads), 2)
        self.assertTrue(all(call.context is context for call in self.reads))
        day, hour = self.reads
        self.assertEqual(day.data["start_time"], "2026-03-08T05:00:00+00:00")
        self.assertEqual(day.data["end_time"], "2026-03-09T03:59:59.999999+00:00")
        self.assertEqual(hour.data["end_time"], "2026-03-09T04:00:00+00:00")
        self.assertEqual(day.data["units"], {"temperature": "°C"})
        self.assertEqual(day.data["types"], ["mean", "min", "max"])
        selected = result["families"][0]["rows"][0]["selected"]
        self.assertEqual(selected["native_values"]["mean"], 20)
        self.assertEqual(selected["values"]["mean"], 68)
        self.assertEqual(selected["coverage"]["native_bins_expected"], 23)
        self.assertEqual(selected["coverage"]["bin_coverage"], "bins_complete")
        self.assertEqual(selected["coverage"]["sample_coverage"], "unknown")
        self.assertEqual(selected["settlement"], "unknown")
        self.assertEqual(result["snapshot_consistency"], "separate_native_reads")
        self.assertTrue(
            all(
                item.kwargs["context"] is context
                for item in self.validate.call_args_list
            )
        )

    async def test_fall_dst_expected_25_bins(self) -> None:
        self._row(start="2025-11-02T04:00:00+00:00", hours=25)
        result = await self._read(start_date="2025-11-02", end_date="2025-11-03")
        selected = result["families"][0]["rows"][0]["selected"]
        self.assertEqual(selected["coverage"]["native_bins_expected"], 25)
        self.assertEqual(self.reads[1].data["end_time"], "2025-11-03T05:00:00+00:00")

    async def test_missing_co2_primary_fixed_gap_then_whole_source_fallback(
        self,
    ) -> None:
        primary = _source("sensor.native_co2", unit="ppm")
        secondary = _source("sensor.retained_co2", unit="ppm")
        self._row(secondary["statistic_id"], mean=801, minimum=700, maximum=910)
        family = _family([primary, secondary], quantity="co2", unit="ppm")
        fixed = (await self._read(self._manager(family)))["families"][0]["rows"][0]
        self.assertIsNone(fixed["selected"])
        self.assertEqual(fixed["candidates"][0]["status"], "no_rows")
        self.assertEqual(fixed["candidates"][1]["native_values"]["mean"], 801)
        family = _family(
            [primary, secondary], quantity="co2", unit="ppm", policy="ordered_daily"
        )
        fallback = (await self._read(self._manager(family)))["families"][0]["rows"][0]
        self.assertEqual(fallback["selection_reason"], "ordered_fallback")
        self.assertEqual(fallback["values"], {"mean": 801, "min": 700, "max": 910})

    async def test_null_humidity_extrema_legacy_daily_bin_and_zero(self) -> None:
        source = _source(
            "beestat:zone_indoor_humidity",
            unit="%",
            method="beestat_legacy_daily_humidity_summary",
        )
        self._row(source["statistic_id"], mean=0, minimum=None, maximum=None, hours=1)
        family = _family([source], quantity="humidity", unit="%")
        row = (await self._read(self._manager(family)))["families"][0]["rows"][0]
        self.assertEqual(row["values"], {"mean": 0, "min": None, "max": None})
        self.assertEqual(row["selected"]["coverage"]["native_bins_expected"], 1)
        self.assertEqual(row["selected"]["coverage"]["sample_count"], None)
        self.assertEqual(
            row["selected"]["missing_measures"]["min"], "unsupported_or_missing_measure"
        )

    async def test_every_requested_date_has_gap_without_carry_forward(self) -> None:
        self._row()
        result = await self._read(start_date="2026-03-07", end_date="2026-03-10")
        rows = result["families"][0]["rows"]
        self.assertEqual(
            [row["date"] for row in rows], ["2026-03-07", "2026-03-08", "2026-03-09"]
        )
        self.assertEqual([row["values"]["mean"] for row in rows], [None, 68, None])

    async def test_aqi_forward_scaling_without_daily_rounding_and_method_acceptance(
        self,
    ) -> None:
        raw = _source(
            "sensor.raw_aqi", unit=None, transformation="aqi_raw_div350_times100"
        )
        provider = _source(
            "beestat:zone_air_quality",
            unit="%",
            method="beestat_legacy_daily_sample_mean",
        )
        self._row(raw["statistic_id"], mean=71.25, minimum=40, maximum=90)
        self._row(provider["statistic_id"], mean=20.3, minimum=11, maximum=26, hours=1)
        with self.assertRaisesRegex(ValueError, "historical_cross_method_required"):
            _family(
                [raw, provider], quantity="aqi", unit="0-100", policy="ordered_daily"
            )
        family = _family(
            [raw, provider],
            quantity="aqi",
            unit="0-100",
            policy="ordered_daily",
            accept_cross_method=True,
        )
        row = (await self._read(self._manager(family)))["families"][0]["rows"][0]
        self.assertAlmostEqual(row["values"]["mean"], 71.25 / 350 * 100)
        self.assertNotEqual(row["values"]["mean"], round(71.25 / 350 * 100))
        self.assertEqual(row["candidates"][1]["values"]["mean"], 20.3)
        self.assertEqual(
            row["candidates"][1]["rounding"],
            "provider_rounded_samples_before_aggregation",
        )

    async def test_voc_candidates_visible_without_equivalent_selection(self) -> None:
        native = _source("sensor.native_voc", unit="µg/m³")
        provider = _source(
            "beestat:zone_voc", unit="ppb", method="beestat_legacy_daily_sample_mean"
        )
        self._row(native["statistic_id"], mean=250)
        self._row(provider["statistic_id"], mean=0.04, hours=1)
        family = _family([native, provider], quantity="voc", unit="native")
        row = (await self._read(self._manager(family)))["families"][0]["rows"][0]
        self.assertIsNone(row["selected"])
        self.assertEqual(
            [item["native_values"]["mean"] for item in row["candidates"]], [250, 0.04]
        )
        self.assertTrue(
            all(
                item["status"] == "voc_unit_contract_unresolved"
                for item in row["candidates"]
            )
        )

    async def test_provider_window_provisional_does_not_claim_import_settlement(
        self,
    ) -> None:
        source = _source()
        source["provider_data_through"] = "2026-03-09T03:55:00+00:00"
        source["import_completed_at"] = "2026-03-09T04:30:00+00:00"
        self._row()
        manager = self._manager(_family([source]))
        excluded = (await self._read(manager))["families"][0]["rows"][0]
        self.assertIsNone(excluded["selected"])
        self.assertEqual(excluded["candidates"][0]["status"], "provisional_excluded")
        included = (await self._read(manager, include_provisional=True))["families"][0][
            "rows"
        ][0]
        self.assertTrue(included["selected"]["provisional"])
        self.assertEqual(included["selected"]["settlement"], "unknown")
        self.assertIsNone(included["selected"]["statistic_imported_through"])

    async def test_open_current_date_requires_include_provisional(self) -> None:
        now = datetime.now(dt_util.DEFAULT_TIME_ZONE)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        self._row(start=start.astimezone(UTC).isoformat(), hours=1)
        kwargs: dict[str, Any] = {
            "start_date": start.date().isoformat(),
            "end_date": (start.date() + timedelta(days=1)).isoformat(),
        }
        result = await self._read(**kwargs)
        candidate = result["families"][0]["rows"][0]["candidates"][0]
        self.assertEqual(candidate["status"], "provisional_excluded")
        self.assertIn("open_calendar_day", candidate["provisional_reasons"])

    async def test_native_units_partition_prevents_silent_display_conversion(
        self,
    ) -> None:
        cold = _source()
        warm = _source("sensor.second", unit="°F")
        self._row(warm["statistic_id"], mean=72)
        self._row()
        family = _family([cold, warm])
        await self._read(self._manager(family))
        self.assertEqual(
            [call.data["units"] for call in self.reads],
            [
                {"temperature": "°C"},
                {"temperature": "°C"},
                {"temperature": "°F"},
                {"temperature": "°F"},
            ],
        )

    async def test_invalid_dates_families_and_fractional_calendar_fail_before_reads(
        self,
    ) -> None:
        manager = self._manager()
        invalid: list[dict[str, Any]] = [
            {"family_ids": []},
            {"family_ids": ["room", "room"]},
            {"family_ids": ["missing"]},
            {"family_ids": "room"},
            {"start_date": "2026-01-01", "end_date": "2026-02-02"},
            {"start_date": "2026-03-09", "end_date": "2026-03-08"},
            {"start_date": "20260308"},
            {"end_date": "9999-01-01"},
            {"include_provisional": "true"},
        ]
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(ServiceValidationError):
                await self._read(manager, **kwargs)
        await self.hass.config.async_set_time_zone("Asia/Kathmandu")
        with self.assertRaisesRegex(ServiceValidationError, "alignment_unsupported"):
            await self._read(manager)
        self.assertEqual(self.reads, [])

    async def test_native_service_error_is_not_an_empty_success(self) -> None:
        async def failure(call: ServiceCall) -> None:
            raise HomeAssistantError("native query failed")

        self.read_effect = failure
        with self.assertRaisesRegex(HomeAssistantError, "native query failed"):
            await self._read()
        self.assertEqual(len(self.reads), 1)

    async def test_metadata_contract_changed_after_reads_discards_response(
        self,
    ) -> None:
        self.validate.side_effect = [
            deepcopy(_source()),
            ValueError("historical_source_contract_changed"),
        ]
        with self.assertRaisesRegex(
            ServiceValidationError, "historical_source_contract_changed"
        ):
            await self._read()
        self.assertEqual(len(self.reads), 2)

    async def test_reconfiguration_during_read_fails_before_next_dispatch(self) -> None:
        async def changed(call: ServiceCall) -> None:
            self.hass.config_entries.async_update_entry(
                self.entry, options={"changed": True}
            )

        self.read_effect = changed
        with self.assertRaisesRegex(ServiceValidationError, "configuration_changed"):
            await self._read()
        self.assertEqual(len(self.reads), 1)

    async def test_timezone_change_during_read_discards_response(self) -> None:
        async def changed(call: ServiceCall) -> None:
            await self.hass.config.async_set_time_zone("UTC")

        self.read_effect = changed
        with self.assertRaisesRegex(ServiceValidationError, "timezone_mismatch"):
            await self._read()

    async def test_busy_then_unload_cancels_inflight_and_prevents_late_response(
        self,
    ) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocked(call: ServiceCall) -> None:
            entered.set()
            await release.wait()

        self.read_effect = blocked
        manager = self._manager()
        task = asyncio.create_task(self._read(manager))
        await entered.wait()
        with self.assertRaisesRegex(ServiceValidationError, "historical_busy"):
            await self._read(manager)
        await manager.async_stop()
        with self.assertRaisesRegex(HomeAssistantError, "historical_unloaded"):
            await task
        release.set()
        with self.assertRaisesRegex(ServiceValidationError, "historical_unloaded"):
            await self._read(manager)

    async def test_timeout_has_no_retry_and_releases_busy_guard(self) -> None:
        async def blocked(call: ServiceCall) -> None:
            await asyncio.Event().wait()

        self.read_effect = blocked
        manager = self._manager()
        with (
            patch(
                "custom_components.ecobee_unified.historical.READ_TIMEOUT_SECONDS", 0.01
            ),
            self.assertRaisesRegex(HomeAssistantError, "historical_timeout"),
        ):
            await self._read(manager)
        self.assertEqual(len(self.reads), 1)
        self.read_effect = None
        await self._read(manager)
        self.assertEqual(len(self.reads), 3)

    async def test_duplicate_or_nonfinite_native_daily_rows_fail_closed(self) -> None:
        self._row()
        self.daily["sensor.room_temperature"].append(
            deepcopy(self.daily["sensor.room_temperature"][0])
        )
        with self.assertRaisesRegex(
            ServiceValidationError, "historical_response_invalid"
        ):
            await self._read()
        self.daily["sensor.room_temperature"] = [
            {
                "start": "2026-03-08T05:00:00+00:00",
                "end": "2026-03-09T04:00:00+00:00",
                "mean": float("nan"),
            }
        ]
        with self.assertRaisesRegex(
            ServiceValidationError, "historical_response_invalid"
        ):
            await self._read()

    async def test_overflowed_temperature_conversion_is_rejected_and_read_recovers(
        self,
    ) -> None:
        manager = self._manager()
        for measure in ("mean", "min", "max"):
            for value in (1e308, -1e308):
                with self.subTest(measure=measure, value=value):
                    self._row()
                    self.daily["sensor.room_temperature"][0][measure] = value
                    with self.assertRaisesRegex(
                        ServiceValidationError, "historical_response_invalid"
                    ):
                        await self._read(manager)
        self._row()
        row = (await self._read(manager))["families"][0]["rows"][0]
        self.assertEqual(row["values"]["mean"], 68)
        self.daily["sensor.room_temperature"][0].update(mean=1e307, min=None, max=None)
        row = (await self._read(manager))["families"][0]["rows"][0]
        self.assertAlmostEqual(row["values"]["mean"] / 1e307, 1.8)
        self.assertIsNone(row["values"]["min"])
        self.assertIsNone(row["values"]["max"])

    async def test_oversized_native_integer_is_rejected_as_bounded_response_error(
        self,
    ) -> None:
        manager = self._manager()
        for measure in ("mean", "min", "max"):
            with self.subTest(measure=measure):
                self._row()
                self.daily["sensor.room_temperature"][0][measure] = 10**500
                with self.assertRaisesRegex(
                    ServiceValidationError, "historical_response_invalid"
                ):
                    await self._read(manager)

    async def test_parser_roundtrip_limits_and_unknown_source_extensions(self) -> None:
        source = _source()
        source["future_contract"] = {"version": 2}
        family = _family([source])
        projection = family.as_dict()
        self.assertEqual(HistoricalFamily.from_dict(projection), family)
        projection["sources"][0]["future_contract"]["version"] = 3
        self.assertEqual(family.sources[0]["future_contract"]["version"], 2)
        invalid: list[dict[str, Any]] = [
            {"quantity": "duration"},
            {"unit": "K"},
            {"policy": []},
            {"sources": []},
            {"sources": [source] * 4},
            {"sources": [source, source]},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                _family(**changes)
        await self._read(family_ids=None)

    async def test_request_limit_does_not_limit_saved_configuration(self) -> None:
        families = tuple(_family(family_id=f"room_{number}") for number in range(19))
        manager = self._manager(*families)
        self.assertEqual(len(manager.families), 19)
        with self.assertRaisesRegex(
            ServiceValidationError, "historical_families_invalid"
        ):
            await self._read(manager, family_ids=None)
        result = await self._read(manager, family_ids=["room_17", "room_18"])
        self.assertEqual(
            [row["family_id"] for row in result["families"]], ["room_17", "room_18"]
        )
        # Reused source IDs are batched across families; no duplicate native read.
        self.assertEqual(len(self.reads), 2)

    async def test_battery_and_outdoor_quantities_keep_native_method(self) -> None:
        battery = _source("sensor.room_battery", unit="%")
        outdoor = _source(
            "beestat:zone_outdoor_temperature",
            unit="°F",
            method="beestat_legacy_daily_weather_summary",
        )
        humidity = _source(
            "beestat:zone_outdoor_humidity",
            unit="%",
            method="beestat_legacy_daily_weather_summary",
        )
        self._row(battery["statistic_id"], mean=75)
        self._row(outdoor["statistic_id"], mean=68, hours=1)
        self._row(
            humidity["statistic_id"], mean=55, minimum=None, maximum=None, hours=1
        )
        families = (
            _family([battery], family_id="battery", quantity="battery", unit="%"),
            _family(
                [outdoor],
                family_id="outdoor",
                quantity="weather_temperature",
                unit="°C",
            ),
            _family(
                [humidity], family_id="humidity", quantity="weather_humidity", unit="%"
            ),
        )
        result = await self._read(self._manager(*families), family_ids=None)
        selected = [family["rows"][0]["selected"] for family in result["families"]]
        self.assertEqual([row["values"]["mean"] for row in selected], [75, 20, 55])
        self.assertEqual(selected[1]["method"], "beestat_legacy_daily_weather_summary")
        self.assertIsNone(selected[2]["values"]["min"])

    async def test_interval_end_duplicate_hour_and_legacy_shape_rejected(self) -> None:
        for fault in ("day_end", "hour_end", "hour_duplicate", "legacy_two_rows"):
            with self.subTest(fault=fault):
                source = _source()
                self._row()
                if fault == "day_end":
                    self.daily[source["statistic_id"]][0]["end"] = (
                        "2026-03-09T05:00:00+00:00"
                    )
                elif fault == "hour_end":
                    self.hourly[source["statistic_id"]][0]["end"] = (
                        "2026-03-08T07:00:00+00:00"
                    )
                elif fault == "hour_duplicate":
                    self.hourly[source["statistic_id"]].append(
                        deepcopy(self.hourly[source["statistic_id"]][0])
                    )
                else:
                    source["method"] = "beestat_legacy_daily_sample_mean"
                    self.hourly[source["statistic_id"]] = self.hourly[
                        source["statistic_id"]
                    ][:2]
                with self.assertRaises(ServiceValidationError):
                    await self._read(self._manager(_family([source])))

    async def test_advancing_watermark_cannot_settle_an_earlier_read(self) -> None:
        source = _source()
        source["provider_data_through"] = "2026-03-09T03:55:00+00:00"
        later = deepcopy(source)
        later["provider_data_through"] = "2026-03-09T05:00:00+00:00"
        self.validate.side_effect = [source, later]
        self._row()
        row = (await self._read(self._manager(_family([source]))))["families"][0][
            "rows"
        ][0]
        self.assertIsNone(row["selected"])
        self.assertEqual(
            row["candidates"][0]["provider_thermostat_data_end"],
            source["provider_data_through"],
        )

    async def test_native_unload_transition_and_runtime_replacement_fence(self) -> None:
        for change in ("unload", "replace"):
            self.entry.mock_state(self.hass, ConfigEntryState.LOADED)
            manager = self._manager()

            async def changed(call: ServiceCall, operation: str = change) -> None:
                if operation == "unload":
                    self.entry.mock_state(
                        self.hass, ConfigEntryState.UNLOAD_IN_PROGRESS
                    )
                else:
                    self.entry.runtime_data = SimpleNamespace(history=None)

            self.read_effect = changed
            with (
                self.subTest(change=change),
                self.assertRaisesRegex(ServiceValidationError, "historical_unloaded"),
            ):
                await self._read(manager)

    async def test_unverified_saved_extensions_cannot_claim_settled_samples(
        self,
    ) -> None:
        source = _source()
        source["statistic_imported_through"] = "2099-01-01T00:00:00+00:00"
        source["sensor_observed_through"] = "2099-01-01T00:00:00+00:00"
        self._row()
        selected = (await self._read(self._manager(_family([source]))))["families"][0][
            "rows"
        ][0]["selected"]
        self.assertEqual(selected["settlement"], "unknown")
        self.assertIsNone(selected["statistic_imported_through"])
        self.assertIsNone(selected["sensor_observed_through"])

    async def test_absent_statistic_and_empty_return_remain_distinct(self) -> None:
        absent = _source("sensor.absent")
        empty = _source("sensor.empty")
        self.daily[empty["statistic_id"]] = []
        self.hourly[empty["statistic_id"]] = []
        family = _family([absent, empty])
        candidates = (await self._read(self._manager(family)))["families"][0]["rows"][
            0
        ]["candidates"]
        self.assertEqual([row["status"] for row in candidates], ["no_rows", "no_rows"])
        self.assertEqual(
            [row["native_response"]["day"] for row in candidates],
            ["statistic_not_returned", "empty_rows"],
        )
        calls = self.validate.call_args_list
        self.assertIs(
            calls[0].kwargs["validation_cache"], calls[1].kwargs["validation_cache"]
        )
        self.assertIsNot(
            calls[0].kwargs["validation_cache"], calls[2].kwargs["validation_cache"]
        )
