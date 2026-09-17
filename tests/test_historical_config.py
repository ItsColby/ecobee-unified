"""Native historical reconfiguration with source capture at its owned boundary."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from unittest.mock import patch

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import EntitySelector, StatisticSelector
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ecobee_unified.config_flow import (
    _historical_form_defaults,
    _historical_schema,
)
from custom_components.ecobee_unified.const import (
    CONF_HISTORICAL_FAMILIES,
    CONF_MAPPINGS,
    DOMAIN,
)

from .runtime_fixture import CoreRuntimeTestCase


class HistoricalConfigurationTests(CoreRuntimeTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        registry = er.async_get(self.hass)
        native_entry = self.hass.config_entries.async_get_entry(
            self.ecobee.config_entry_id
        )
        assert native_entry is not None
        self.anchor = registry.async_get_or_create(
            "sensor",
            "ecobee",
            "thermostat_a-ei:0-temperature",
            config_entry=native_entry,
            device_id=self.ecobee.device_id,
            original_device_class="temperature",
            unit_of_measurement="°C",
        )
        self.hass.states.async_set(
            self.anchor.entity_id,
            "22",
            {
                "device_class": "temperature",
                "unit_of_measurement": "°C",
            },
        )
        self.primary = self.anchor.entity_id
        self.secondary = "beestat:synthetic_temperature"
        self.captured: list[tuple[str, str, str]] = []
        self.capture_error: str | None = None
        self.method_override: str | None = None
        self.timing: dict[str, Any] = {}
        self.capture_patch = patch(
            "custom_components.ecobee_unified.config_flow.async_capture_source",
            side_effect=self._capture,
        )
        self.capture_patch.start()
        self.addCleanup(self.capture_patch.stop)

    async def _capture(
        self, hass: Any, statistic_id: str, quantity: str, anchor_ref: str
    ) -> dict[str, Any]:
        self.assertIs(hass, self.hass)
        self.captured.append((statistic_id, quantity, anchor_ref))
        if self.capture_error:
            raise ValueError(self.capture_error)
        provider = statistic_id.startswith("beestat:")
        unit = {
            "temperature": "°C",
            "humidity": "%",
            "co2": "ppm",
            "aqi": "%" if provider else None,
            "voc": "ppb" if provider else "µg/m³",
            "battery": "%",
            "weather_temperature": "°C",
            "weather_humidity": "%",
        }[quantity]
        return {
            "statistic_id": statistic_id,
            "metadata": {
                "source": "beestat" if provider else "recorder",
                "unit": unit,
                "display_unit": unit,
                "mean_type": 1,
                "unit_class": "temperature" if quantity == "temperature" else None,
            },
            "method": self.method_override
            or (
                "beestat_legacy_daily_sample_mean"
                if provider
                else "recorder_hourly_time_weighted"
            ),
            "native_unit": unit,
            "transformation": "aqi_raw_div350_times100"
            if quantity == "aqi" and not provider
            else "identity",
            "identity_evidence": {
                "association": "explicit_synthetic_fixture",
                "historical_identity_continuity": "unknown",
            },
            "entity_ref": None if provider else anchor_ref,
            "anchor_ref": anchor_ref,
            "timezone": self.hass.config.time_zone,
            "settlement_basis": None,
        } | self.timing

    def _values(self, **changes: Any) -> dict[str, Any]:
        return {
            "name": "Daily room temperature",
            "quantity": "temperature",
            "unit": "°C",
            "timezone": self.hass.config.time_zone,
            "anchor_ref": self.anchor.entity_id,
            "primary_statistic": self.primary,
            "policy": "fixed_source",
            "accept_cross_method": False,
            "confirm_association": True,
        } | changes

    def _entry(self, families: list[dict[str, Any]] | None = None) -> MockConfigEntry:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=DOMAIN,
            version=1,
            minor_version=4,
            data={
                CONF_MAPPINGS: [self.mapping.as_dict()],
                CONF_HISTORICAL_FAMILIES: families or [],
                "future_top": {"keep": True},
            },
            options={"future_option": 7},
        )
        entry.add_to_hass(self.hass)
        return entry

    async def _open(self, entry: MockConfigEntry) -> dict[str, Any]:
        return await self.hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
        )

    async def _submit(
        self, result: dict[str, Any], values: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.hass.config_entries.flow.async_configure(
            result["flow_id"], values
        )

    async def _next(self, result: dict[str, Any], step: str) -> dict[str, Any]:
        return await self._submit(result, {"next_step_id": step})

    async def _save(self, result: dict[str, Any]) -> dict[str, Any]:
        with patch.object(self.hass.config_entries, "async_reload", return_value=True):
            result = await self._next(result, "reconfigure_finish")
            await self.hass.async_block_till_done()
        self.assertEqual("reconfigure_successful", result["reason"])
        return result

    async def _add(self, entry: MockConfigEntry, **changes: Any) -> dict[str, Any]:
        result = await self._next(await self._open(entry), "historical_add")
        result = await self._submit(result, self._values(**changes))
        self.assertEqual(FlowResultType.MENU, result["type"], result)
        await self._save(result)
        return deepcopy(entry.data[CONF_HISTORICAL_FAMILIES][-1])

    async def test_native_forms_add_edit_reorder_remove_preserve_identities_and_future_fields(
        self,
    ) -> None:
        entry = self._entry()
        menu = await self._open(entry)
        self.assertIn("historical_add", menu["menu_options"])
        self.assertNotIn("historical_edit", menu["menu_options"])
        self.hass.config_entries.flow.async_abort(menu["flow_id"])
        row = await self._add(entry, secondary_statistic=self.secondary)
        self.assertEqual(self.anchor.id, row["anchor_ref"])
        self.assertEqual(self.hass.config.time_zone, row["timezone"])
        self.assertTrue(all(item[2] == self.anchor.id for item in self.captured))
        original_sources = {
            source["statistic_id"]: source["source_id"] for source in row["sources"]
        }
        row["future_row"] = {"keep": True}
        row["sources"][0]["future_binding"] = "keep"
        self.hass.config_entries.async_update_entry(
            entry, data=dict(entry.data) | {CONF_HISTORICAL_FAMILIES: [row]}
        )
        result = await self._next(await self._open(entry), "historical_edit")
        result = await self._submit(result, {"family_id": row["family_id"]})
        self.timing = {
            "provider_data_through": "2024-01-02T00:00:00+00:00",
            "import_completed_at": "2024-01-02T00:01:00+00:00",
        }
        values = _historical_form_defaults(self.hass, row) | {
            "name": "Renamed daily temperature",
            "confirm_association": False,
        }
        result = await self._submit(result, values)
        self.assertEqual(FlowResultType.MENU, result["type"], result)
        await self._save(result)
        renamed = entry.data[CONF_HISTORICAL_FAMILIES][0]
        self.assertEqual(row["family_id"], renamed["family_id"])
        self.assertEqual({"keep": True}, renamed["future_row"])
        self.assertEqual("keep", renamed["sources"][0]["future_binding"])
        self.assertEqual(
            self.timing["provider_data_through"],
            renamed["sources"][0]["provider_data_through"],
        )
        self.timing = {}
        result = await self._next(await self._open(entry), "historical_edit")
        result = await self._submit(result, {"family_id": row["family_id"]})
        result = await self._submit(
            result,
            values
            | {
                "primary_statistic": self.secondary,
                "secondary_statistic": self.primary,
                "confirm_association": True,
            },
        )
        self.assertEqual(FlowResultType.MENU, result["type"], result)
        await self._save(result)
        reordered = entry.data[CONF_HISTORICAL_FAMILIES][0]
        self.assertEqual(
            original_sources,
            {
                source["statistic_id"]: source["source_id"]
                for source in reordered["sources"]
            },
        )
        self.assertEqual("keep", reordered["sources"][1]["future_binding"])
        self.assertNotIn("provider_data_through", reordered["sources"][1])
        self.assertNotIn("import_completed_at", reordered["sources"][1])
        await self._add(entry, name="Other family")
        result = await self._next(await self._open(entry), "historical_remove")
        result = await self._submit(result, {"family_id": row["family_id"]})
        result = await self._submit(result, {"confirm_change": False})
        self.assertEqual("confirmation_required", result["errors"]["base"])
        result = await self._submit(result, {"confirm_change": True})
        await self._save(result)
        self.assertEqual(
            ["Other family"],
            [item["name"] for item in entry.data[CONF_HISTORICAL_FAMILIES]],
        )
        self.assertEqual([self.mapping.as_dict()], entry.data[CONF_MAPPINGS])
        self.assertEqual({"keep": True}, entry.data["future_top"])
        self.assertEqual({"future_option": 7}, entry.options)

    async def test_native_selectors_and_confirmation_and_capture_error_paths(
        self,
    ) -> None:
        schema = _historical_schema(self.hass, {})
        selectors = {key.schema: value for key, value in schema.schema.items()}
        self.assertIsInstance(selectors["primary_statistic"], StatisticSelector)
        self.assertIsInstance(selectors["anchor_ref"], EntitySelector)
        for unit in ("°C", "°F", "%", "ppm", "0-100", "native"):
            with self.subTest(unit=unit):
                saved_schema = _historical_schema(self.hass, {"unit": unit})
                field, selector = next(
                    (field, selector)
                    for field, selector in saved_schema.schema.items()
                    if field.schema == "unit"
                )
                self.assertEqual(unit, selector(field.default()))
                # Unit symbols are native labels, not translation lookup keys.
                self.assertNotIn("translation_key", selector.config)
                self.assertIn(unit, selector.config["options"])
        entry = self._entry()
        for changes, error in (
            ({"confirm_association": False}, "historical_confirmation_required"),
            ({"secondary_statistic": self.primary}, "historical_sources_invalid"),
            ({"unit": "ppm"}, "historical_unit_invalid"),
            ({"timezone": "Invalid/Timezone"}, "historical_timezone_mismatch"),
        ):
            with self.subTest(error=error):
                result = await self._next(await self._open(entry), "historical_add")
                result = await self._submit(result, self._values(**changes))
                self.assertEqual(error, result["errors"]["base"])
                self.hass.config_entries.flow.async_abort(result["flow_id"])
        self.capture_error = "historical_source_identity_mismatch"
        result = await self._next(await self._open(entry), "historical_add")
        result = await self._submit(result, self._values())
        self.assertEqual(self.capture_error, result["errors"]["base"])
        self.assertEqual([], entry.data[CONF_HISTORICAL_FAMILIES])

    async def test_cross_method_ordered_daily_requires_acceptance_and_reconfirmation(
        self,
    ) -> None:
        entry = self._entry()
        row = await self._add(entry, secondary_statistic=self.secondary)
        result = await self._next(await self._open(entry), "historical_edit")
        result = await self._submit(result, {"family_id": row["family_id"]})
        values = _historical_form_defaults(self.hass, row) | {
            "policy": "ordered_daily",
            "confirm_association": True,
        }
        result = await self._submit(result, values)
        self.assertEqual("historical_cross_method_required", result["errors"]["base"])
        values |= {"accept_cross_method": True, "confirm_association": False}
        result = await self._submit(result, values)
        self.assertEqual("historical_confirmation_required", result["errors"]["base"])
        result = await self._submit(result, values | {"confirm_association": True})
        self.assertEqual(FlowResultType.MENU, result["type"], result)
        await self._save(result)
        self.assertEqual(
            "ordered_daily", entry.data[CONF_HISTORICAL_FAMILIES][0]["policy"]
        )
        self.assertTrue(entry.data[CONF_HISTORICAL_FAMILIES][0]["accept_cross_method"])

    async def test_stale_flow_never_overwrites_data_or_options(self) -> None:
        entry = self._entry()
        for owner in ("data", "options"):
            with self.subTest(owner=owner):
                result = await self._next(await self._open(entry), "historical_add")
                result = await self._submit(result, self._values())
                self.assertEqual(FlowResultType.MENU, result["type"], result)
                changed = dict(getattr(entry, owner)) | {"concurrent_change": owner}
                self.hass.config_entries.async_update_entry(entry, **{owner: changed})
                with patch.object(self.hass.config_entries, "async_reload") as reload:
                    result = await self._next(result, "reconfigure_finish")
                    self.assertEqual("configuration_changed", result["reason"])
                    reload.assert_not_called()
                self.assertEqual(changed, getattr(entry, owner))
                self.assertEqual([], entry.data[CONF_HISTORICAL_FAMILIES])

    async def test_binding_quantity_and_method_changes_require_confirmation(
        self,
    ) -> None:
        entry = self._entry()
        row = await self._add(entry)
        for changes, method in (
            ({"primary_statistic": self.secondary}, None),
            ({"quantity": "humidity", "unit": "%"}, None),
            ({"unit": "°F"}, None),
            ({}, "beestat_legacy_daily_sample_mean"),
        ):
            with self.subTest(changes=changes, method=method):
                self.method_override = method
                result = await self._next(await self._open(entry), "historical_edit")
                result = await self._submit(result, {"family_id": row["family_id"]})
                values = (
                    _historical_form_defaults(self.hass, row)
                    | changes
                    | {"confirm_association": False}
                )
                result = await self._submit(result, values)
                self.assertEqual(
                    "historical_confirmation_required", result["errors"]["base"]
                )
                self.hass.config_entries.flow.async_abort(result["flow_id"])
        self.assertEqual([row], entry.data[CONF_HISTORICAL_FAMILIES])
        self.method_override = None
        result = await self._next(await self._open(entry), "historical_edit")
        result = await self._submit(result, {"family_id": row["family_id"]})
        result = await self._submit(
            result, self._values(primary_statistic=self.secondary)
        )
        self.assertEqual(FlowResultType.MENU, result["type"], result)
        await self._save(result)
        changed = entry.data[CONF_HISTORICAL_FAMILIES][0]
        self.assertEqual(row["family_id"], changed["family_id"])
        self.assertNotEqual(
            row["sources"][0]["source_id"], changed["sources"][0]["source_id"]
        )

    async def test_aqi_voc_battery_and_weather_contracts_are_configurable(self) -> None:
        entry = self._entry()
        for quantity, unit in (
            ("aqi", "0-100"),
            ("voc", "native"),
            ("battery", "%"),
            ("weather_temperature", "°C"),
            ("weather_humidity", "%"),
        ):
            with self.subTest(quantity=quantity):
                row = await self._add(
                    entry,
                    name=f"Daily {quantity}",
                    quantity=quantity,
                    unit=unit,
                    secondary_statistic=self.secondary,
                )
                self.assertEqual(quantity, row["quantity"])
                self.assertEqual(unit, row["unit"])
                self.assertEqual("fixed_source", row["policy"])
                if quantity == "aqi":
                    self.assertEqual(
                        ["aqi_raw_div350_times100", "identity"],
                        [source["transformation"] for source in row["sources"]],
                    )
                if quantity == "voc":
                    self.assertEqual(
                        ["µg/m³", "ppb"],
                        [source["native_unit"] for source in row["sources"]],
                    )

    async def test_request_limit_does_not_truncate_or_limit_configured_families(
        self,
    ) -> None:
        entry = self._entry()
        template = await self._add(entry)
        existing = []
        for index in range(17):
            row = deepcopy(template)
            row |= {"family_id": f"family_{index}", "name": f"Daily family {index}"}
            row["sources"][0]["source_id"] = f"source_{index}"
            existing.append(row)
        self.hass.config_entries.async_update_entry(
            entry, data=dict(entry.data) | {CONF_HISTORICAL_FAMILIES: existing}
        )
        await self._add(entry, name="Additional family")
        self.assertEqual(18, len(entry.data[CONF_HISTORICAL_FAMILIES]))
        self.assertEqual(existing, entry.data[CONF_HISTORICAL_FAMILIES][:17])

    async def test_duplicate_identity_and_oversized_saved_source_collection_do_not_remove_or_truncate(
        self,
    ) -> None:
        entry = self._entry()
        row = await self._add(entry)
        self.hass.config_entries.async_update_entry(
            entry,
            data=dict(entry.data)
            | {CONF_HISTORICAL_FAMILIES: [row, row | {"name": "Duplicate"}]},
        )
        result = await self._next(await self._open(entry), "historical_remove")
        result = await self._submit(result, {"family_id": row["family_id"]})
        self.assertEqual("historical_identity_invalid", result["reason"])
        oversized = row | {"sources": row["sources"] * 4}
        self.hass.config_entries.async_update_entry(
            entry, data=dict(entry.data) | {CONF_HISTORICAL_FAMILIES: [oversized]}
        )
        result = await self._next(await self._open(entry), "historical_edit")
        result = await self._submit(result, {"family_id": row["family_id"]})
        self.assertEqual("historical_source_limit", result["reason"])
        self.assertEqual([oversized], entry.data[CONF_HISTORICAL_FAMILIES])
