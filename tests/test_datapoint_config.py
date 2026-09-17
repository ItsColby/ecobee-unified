"""Native flow coverage for read-only datapoints and per-thermostat policies."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from unittest.mock import patch

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ecobee_unified.config_flow import (
    READ_POLICY_FIELDS,
    _datapoint_form_defaults,
    _datapoint_from_input,
)
from custom_components.ecobee_unified.const import CONF_MAPPINGS, DOMAIN

from .runtime_fixture import CoreRuntimeTestCase


class DatapointConfigurationTests(CoreRuntimeTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        for source in (self.homekit, self.ecobee):
            state = self.hass.states.get(source.entity_id)
            assert state is not None
            self.hass.states.async_set(
                source.entity_id,
                state.state,
                dict(state.attributes) | {"current_humidity": 42},
            )

    async def test_weather_mapping_preserves_feed_and_requires_new_identity_on_drift(
        self,
    ) -> None:
        """Different thermostat aliases share only their explicitly proven station feed."""
        source_entry = self.hass.config_entries.async_get_entry(
            self.ecobee.config_entry_id
        )
        assert source_entry is not None
        registry = er.async_get(self.hass)
        aliases = []
        for serial in (
            "weather_thermostat_a",
            "weather_thermostat_b",
            "weather_thermostat_c",
        ):
            device = dr.async_get(self.hass).async_get_or_create(
                config_entry_id=source_entry.entry_id,
                identifiers={("ecobee", serial)},
            )
            alias = registry.async_get_or_create(
                "weather",
                "ecobee",
                serial,
                config_entry=source_entry,
                device_id=device.id,
            )
            self.hass.states.async_set(
                alias.entity_id,
                "sunny",
                {
                    "temperature": 22,
                    "temperature_unit": "°C",
                    "attribution": "Ecobee weather provided by STATION-A at "
                    + dt_util.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "supported_features": 1,
                },
            )
            aliases.append(alias)
        entry = self._entry()
        result = await self._next(await self._open(entry), "datapoint_add")
        values = self._values(
            name="Shared weather",
            kind="weather",
            unit="",
            primary_entity=aliases[0].entity_id,
            secondary_entity=aliases[1].entity_id,
            primary_attribute="",
            secondary_attribute="",
        )
        result = await self._submit(result, values)
        self.assertEqual(FlowResultType.MENU, result["type"])
        await self._save(result)
        saved = entry.data["datapoints"][0]
        self.assertEqual("STATION-A", saved["weather_station"])
        self.assertEqual(source_entry.entry_id, saved["weather_config_entry_id"])
        rebound = _datapoint_from_input(
            self.hass,
            values
            | {
                "primary_entity": aliases[2].entity_id,
                "name": "Same station through another alias",
                "fallback": False,
            },
            saved,
        )
        self.assertEqual(saved["datapoint_id"], rebound["datapoint_id"])
        self.assertEqual("STATION-A", rebound["weather_station"])
        self.assertEqual(aliases[2].id, rebound["sources"][0]["entity"])
        for alias in aliases:
            state = self.hass.states.get(alias.entity_id)
            assert state is not None
            self.hass.states.async_set(
                alias.entity_id,
                state.state,
                dict(state.attributes)
                | {
                    "attribution": state.attributes["attribution"].replace(
                        "STATION-A", "STATION-B"
                    )
                },
            )
        for confirmed in (False, True):
            with (
                self.subTest(confirmed=confirmed),
                self.assertRaisesRegex(ValueError, "datapoint_meaning_change"),
            ):
                _datapoint_from_input(
                    self.hass, values | {"confirm_equivalence": confirmed}, saved
                )
        added = _datapoint_from_input(self.hass, values)
        self.assertNotEqual(saved["datapoint_id"], added["datapoint_id"])
        self.assertEqual("STATION-B", added["weather_station"])

    def _values(self, **changes: Any) -> dict[str, Any]:
        return {
            "name": "Zone humidity",
            "kind": "humidity",
            "unit": "%",
            "semantic": "physical_temperature",
            "time_basis": "current",
            "max_age_seconds": 0,
            "fallback": True,
            "primary_entity": self.homekit.entity_id,
            "primary_attribute": "current_humidity",
            "secondary_entity": self.ecobee.entity_id,
            "secondary_attribute": "current_humidity",
            "confirm_equivalence": True,
        } | changes

    def _entry(
        self,
        rows: list[dict[str, Any]] | None = None,
        options: dict[str, Any] | None = None,
    ) -> MockConfigEntry:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=DOMAIN,
            version=1,
            minor_version=4,
            data={
                CONF_MAPPINGS: [self.mapping.as_dict() | {"future_mapping": 7}],
                "datapoints": rows or [],
                "future_top_level": {"keep": True},
            },
            options=options or {},
        )
        entry.add_to_hass(self.hass)
        return entry

    async def _open(self, entry: MockConfigEntry) -> dict[str, Any]:
        return await self.hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reconfigure", "entry_id": entry.entry_id},
        )

    async def _next(self, result: dict[str, Any], step: str) -> dict[str, Any]:
        return await self.hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"next_step_id": step},
        )

    async def _submit(
        self, result: dict[str, Any], values: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.hass.config_entries.flow.async_configure(
            result["flow_id"], values
        )

    async def _save(self, result: dict[str, Any]) -> dict[str, Any]:
        with patch.object(self.hass.config_entries, "async_reload", return_value=True):
            result = await self._next(result, "reconfigure_finish")
            await self.hass.async_block_till_done()
        self.assertEqual("reconfigure_successful", result["reason"])
        return result

    async def test_add_edit_remove_preserves_mappings_identity_and_future_fields(
        self,
    ) -> None:
        original_row = _datapoint_from_input(
            self.hass, self._values(name="Keep this row")
        )
        entry = self._entry([original_row])
        self.assertIsNone(original_row["sources"][0]["max_age_seconds"])
        original_mappings = deepcopy(entry.data[CONF_MAPPINGS])
        result = await self._next(await self._open(entry), "datapoint_add")
        result = await self._submit(
            result,
            self._values(
                max_age_seconds=600,
                primary_max_age_seconds=0,
                secondary_max_age_seconds=1800,
            ),
        )
        self.assertEqual(FlowResultType.MENU, result["type"])
        self.assertEqual([original_row], entry.data["datapoints"])
        await self._save(result)
        added = deepcopy(entry.data["datapoints"][1])
        self.assertEqual(self.homekit.id, added["sources"][0]["entity"])
        self.assertEqual(600, added["max_age_seconds"])
        self.assertEqual(0, added["sources"][0]["max_age_seconds"])
        self.assertEqual(1800, added["sources"][1]["max_age_seconds"])
        added["future_row"] = {"keep": True}
        added["sources"][0]["future_binding"] = "keep"
        self.hass.config_entries.async_update_entry(
            entry,
            data=dict(entry.data) | {"datapoints": [original_row, added]},
        )
        result = await self._next(await self._open(entry), "datapoint_edit")
        result = await self._submit(result, {"datapoint_id": added["datapoint_id"]})
        values = _datapoint_form_defaults(self.hass, added) | {
            "name": "Renamed humidity",
            "confirm_equivalence": False,
            "secondary_max_age_seconds": 0,
        }
        values.pop("primary_max_age_seconds")
        result = await self._submit(result, values)
        self.assertEqual(FlowResultType.MENU, result["type"])
        await self._save(result)
        edited = entry.data["datapoints"][1]
        self.assertEqual(added["datapoint_id"], edited["datapoint_id"])
        self.assertEqual({"keep": True}, edited["future_row"])
        self.assertEqual("keep", edited["sources"][0]["future_binding"])
        self.assertEqual(600, edited["max_age_seconds"])
        self.assertIsNone(edited["sources"][0]["max_age_seconds"])
        self.assertEqual(0, edited["sources"][1]["max_age_seconds"])
        result = await self._next(await self._open(entry), "datapoint_remove")
        result = await self._submit(result, {"datapoint_id": edited["datapoint_id"]})
        result = await self._submit(result, {"confirm_change": False})
        self.assertEqual("confirmation_required", result["errors"]["base"])
        result = await self._submit(result, {"confirm_change": True})
        await self._save(result)
        self.assertEqual([original_row], entry.data["datapoints"])
        self.assertEqual(original_mappings, entry.data[CONF_MAPPINGS])
        self.assertEqual({"keep": True}, entry.data["future_top_level"])

    async def test_native_form_rejects_unconfirmed_duplicate_missing_and_mismatched_sources(
        self,
    ) -> None:
        mismatched = self._source(
            "ecobee",
            "ec_other",
            device=True,
            physical_identity="thermostat_other",
        )
        state = self.hass.states.get(mismatched.entity_id)
        assert state is not None
        self.hass.states.async_set(
            mismatched.entity_id,
            state.state,
            dict(state.attributes) | {"current_humidity": 44},
        )
        cases = (
            ({"confirm_equivalence": False}, "datapoint_equivalence_required"),
            (
                {"secondary_entity": self.homekit.entity_id},
                "duplicate_datapoint_source",
            ),
            ({"secondary_entity": "sensor.missing"}, "datapoint_source_missing"),
            ({"secondary_entity": mismatched.entity_id}, "source_identity_mismatch"),
            ({"secondary_attribute": "nonexistent"}, "source_attribute_missing"),
            ({"max_age_seconds": 0.5}, "datapoint_invalid_timing"),
        )
        entry = self._entry()
        for changes, expected in cases:
            with self.subTest(expected=expected):
                result = await self._next(await self._open(entry), "datapoint_add")
                result = await self._submit(result, self._values(**changes))
                self.assertEqual(FlowResultType.FORM, result["type"])
                self.assertEqual(expected, result["errors"]["base"])
                self.hass.config_entries.flow.async_abort(result["flow_id"])
        self.assertEqual([], entry.data["datapoints"])

    async def test_meaning_change_rejected_while_compatible_edit_preserves_output_identity(
        self,
    ) -> None:
        await self.manager.async_stop()
        original = _datapoint_from_input(
            self.hass,
            self._values(
                name="Zone control temperature",
                kind="temperature",
                semantic="control_temperature",
                unit="°C",
                primary_attribute="current_temperature",
                secondary_attribute="current_temperature",
            ),
        )
        original["future_row"] = {"keep": True}
        original["sources"][0]["future_binding"] = "keep"
        entry = self._entry([original])
        self.assertTrue(await self.hass.config_entries.async_setup(entry.entry_id))
        await self.hass.async_block_till_done()
        old_manager = entry.runtime_data.datapoints
        entity_id = er.async_get(self.hass).async_get_entity_id(
            "sensor", DOMAIN, old_manager.unique_id(original["datapoint_id"])
        )
        assert entity_id is not None
        before = self.hass.states.get(entity_id)
        assert before is not None
        self.assertEqual("control_temperature", before.attributes["semantic"])
        self.assertEqual("temperature", before.attributes["device_class"])

        result = await self._next(await self._open(entry), "datapoint_edit")
        result = await self._submit(result, {"datapoint_id": original["datapoint_id"]})
        values = _datapoint_form_defaults(self.hass, original) | self._values(
            semantic="control_temperature"
        )
        accepted_data = deepcopy(dict(entry.data))
        with patch.object(self.hass.config_entries, "async_reload") as reload:
            result = await self._submit(result, values)
            self.assertEqual("datapoint_meaning_change", result["errors"]["base"])
            reload.assert_not_called()
        self.assertEqual(accepted_data, entry.data)
        self.assertIs(old_manager, entry.runtime_data.datapoints)
        self.assertEqual(before, self.hass.states.get(entity_id))

        values = _datapoint_form_defaults(self.hass, original) | {
            "name": "Renamed control temperature",
            "unit": "°F",
            "primary_entity": self.ecobee.entity_id,
            "secondary_entity": self.homekit.entity_id,
            "fallback": False,
            "confirm_equivalence": True,
        }
        result = await self._submit(result, values)
        self.assertEqual(FlowResultType.MENU, result["type"])
        result = await self._next(result, "reconfigure_finish")
        self.assertEqual("reconfigure_successful", result["reason"])
        await self.hass.async_block_till_done()

        saved = entry.data["datapoints"][0]
        self.assertEqual(original["datapoint_id"], saved["datapoint_id"])
        self.assertEqual("control_temperature", saved["semantic"])
        self.assertEqual("°F", saved["unit"])
        self.assertFalse(saved["fallback"])
        self.assertEqual({"keep": True}, saved["future_row"])
        self.assertEqual("keep", saved["sources"][0]["future_binding"])
        self.assertEqual({"keep": True}, entry.data["future_top_level"])
        self.assertIsNot(old_manager, entry.runtime_data.datapoints)
        self.assertFalse(old_manager._running)
        self.assertEqual(
            entity_id,
            er.async_get(self.hass).async_get_entity_id(
                "sensor",
                DOMAIN,
                entry.runtime_data.datapoints.unique_id(saved["datapoint_id"]),
            ),
        )
        after = self.hass.states.get(entity_id)
        assert after is not None
        self.assertEqual("temperature", after.attributes["device_class"])
        self.assertEqual("control_temperature", after.attributes["semantic"])
        snapshot = entry.runtime_data.datapoints.snapshot(saved["datapoint_id"])
        self.assertAlmostEqual(68, snapshot.value)

    async def test_existing_identity_rejects_quantity_semantic_interval_and_generic_meaning_changes(
        self,
    ) -> None:
        for source in (self.homekit, self.ecobee):
            state = self.hass.states.get(source.entity_id)
            assert state is not None
            self.hass.states.async_set(
                source.entity_id,
                state.state,
                dict(state.attributes)
                | {
                    "observed_at": state.last_reported.isoformat(),
                    "runtime": 15,
                    "generic_a": 10,
                    "generic_b": 20,
                },
            )
        temperature = self._values(
            kind="temperature",
            semantic="control_temperature",
            unit="°C",
            primary_attribute="current_temperature",
            secondary_attribute="current_temperature",
        )
        duration = self._values(
            kind="duration",
            semantic="elapsed_duration",
            unit="min",
            primary_attribute="runtime",
            secondary_attribute="runtime",
            primary_unit="min",
            secondary_unit="min",
        )
        interval = self._values(
            time_basis="interval",
            interval_seconds=300,
            primary_timestamp_attribute="observed_at",
            secondary_timestamp_attribute="observed_at",
        )
        number = self._values(
            kind="number",
            unit="ppm",
            primary_attribute="generic_a",
            secondary_attribute="generic_a",
            primary_unit="ppm",
            secondary_unit="ppm",
        )
        text = self._values(
            kind="text",
            unit="",
            primary_attribute="equipment_running",
            secondary_attribute="equipment_running",
        )
        for label, original_values, changes in (
            ("same_unit_new_quantity", self._values(), {"kind": "battery"}),
            ("temperature_semantic", temperature, {"semantic": "physical_temperature"}),
            (
                "duration_semantic",
                duration,
                {"semantic": "minimum_fan_runtime_per_hour"},
            ),
            ("current_to_interval", self._values(), interval),
            ("interval_duration", interval, {"interval_seconds": 600}),
            (
                "generic_unit",
                number,
                {"unit": "%", "primary_unit": "%", "secondary_unit": "%"},
            ),
            ("generic_number_attribute", number, {"primary_attribute": "generic_b"}),
            ("generic_text_attribute", text, {"primary_attribute": "hvac_action"}),
        ):
            with self.subTest(change=label):
                row = _datapoint_from_input(self.hass, original_values)
                entry = self._entry([row])
                original_data = deepcopy(dict(entry.data))
                result = await self._next(await self._open(entry), "datapoint_edit")
                result = await self._submit(
                    result, {"datapoint_id": row["datapoint_id"]}
                )
                values = (
                    _datapoint_form_defaults(self.hass, row)
                    | changes
                    | {"confirm_equivalence": True}
                )
                with patch.object(self.hass.config_entries, "async_reload") as reload:
                    result = await self._submit(result, values)
                    self.assertEqual(
                        "datapoint_meaning_change", result["errors"]["base"]
                    )
                    reload.assert_not_called()
                self.assertEqual(original_data, entry.data)
                self.hass.config_entries.flow.async_abort(result["flow_id"])

        # Duration conversion and opaque-binding reorder keep their existing meaning.
        for values, changes in (
            (duration, {"unit": "h"}),
            (
                number,
                {
                    "primary_entity": self.ecobee.entity_id,
                    "secondary_entity": self.homekit.entity_id,
                },
            ),
            (
                text,
                {
                    "primary_entity": self.ecobee.entity_id,
                    "secondary_entity": self.homekit.entity_id,
                },
            ),
        ):
            with self.subTest(allowed_kind=values["kind"]):
                row = _datapoint_from_input(self.hass, values)
                row["future_row"] = {"keep": True}
                if values["kind"] in {"number", "text"}:
                    row["semantic"] = "preserved_source_meaning"
                entry = self._entry([row])
                result = await self._next(await self._open(entry), "datapoint_edit")
                result = await self._submit(
                    result, {"datapoint_id": row["datapoint_id"]}
                )
                result = await self._submit(
                    result,
                    _datapoint_form_defaults(self.hass, row)
                    | changes
                    | {
                        "name": "Renamed same meaning",
                        "fallback": False,
                        "max_age_seconds": 300,
                        "confirm_equivalence": True,
                    },
                )
                self.assertEqual(
                    FlowResultType.MENU, result["type"], result.get("errors")
                )
                await self._save(result)
                saved = entry.data["datapoints"][0]
                self.assertEqual(row["datapoint_id"], saved["datapoint_id"])
                self.assertEqual(row["semantic"], saved["semantic"])
                self.assertEqual({"keep": True}, saved["future_row"])

    async def test_changed_sources_require_equivalence_again_but_name_and_fallback_do_not(
        self,
    ) -> None:
        original = _datapoint_from_input(self.hass, self._values())
        entry = self._entry([original])
        result = await self._next(await self._open(entry), "datapoint_edit")
        result = await self._submit(result, {"datapoint_id": original["datapoint_id"]})
        changed = self._values(
            primary_entity=self.ecobee.entity_id,
            secondary_entity=self.homekit.entity_id,
            confirm_equivalence=False,
        )
        result = await self._submit(result, changed)
        self.assertEqual("datapoint_equivalence_required", result["errors"]["base"])
        result = await self._submit(result, changed | {"confirm_equivalence": True})
        await self._save(result)
        self.assertEqual(
            self.ecobee.id, entry.data["datapoints"][0]["sources"][0]["entity"]
        )

    async def test_duplicate_saved_identity_cannot_remove_two_rows(self) -> None:
        row = _datapoint_from_input(self.hass, self._values())
        entry = self._entry([row, row | {"name": "Duplicate identity"}])
        original = deepcopy(dict(entry.data))
        with patch.object(self.hass.config_entries, "async_reload") as reload:
            result = await self._next(await self._open(entry), "datapoint_remove")
            result = await self._submit(result, {"datapoint_id": row["datapoint_id"]})
            self.assertEqual("invalid_datapoint_identity", result["reason"])
            self.assertEqual(original, entry.data)
            reload.assert_not_called()

    async def test_more_than_three_saved_sources_are_not_truncated(self) -> None:
        row = _datapoint_from_input(self.hass, self._values())
        row["sources"] = row["sources"] * 2
        entry = self._entry([row])
        result = await self._next(await self._open(entry), "datapoint_edit")
        result = await self._submit(result, {"datapoint_id": row["datapoint_id"]})
        self.assertEqual("datapoint_source_limit", result["reason"])
        self.assertEqual([row], entry.data["datapoints"])

    async def test_reconfigure_stale_data_or_options_aborts_without_write_or_reload(
        self,
    ) -> None:
        entry = self._entry()
        for owner in ("data", "options"):
            with self.subTest(owner=owner):
                result = await self._next(await self._open(entry), "datapoint_add")
                result = await self._submit(result, self._values())
                changed = dict(getattr(entry, owner)) | {"external_change": owner}
                self.hass.config_entries.async_update_entry(entry, **{owner: changed})
                with patch.object(self.hass.config_entries, "async_reload") as reload:
                    result = await self._next(result, "reconfigure_finish")
                    self.assertEqual("configuration_changed", result["reason"])
                    self.assertEqual(changed, getattr(entry, owner))
                    self.assertEqual([], entry.data["datapoints"])
                    reload.assert_not_called()

    async def test_read_policy_is_atomic_preserves_options_and_never_changes_writers(
        self,
    ) -> None:
        options = {
            "future_option": {"keep": True},
            "read_policies": {self.mapping.mapping_id: {"future_field": "keep"}},
        }
        entry = self._entry(options=options)
        original_data = deepcopy(dict(entry.data))
        result = await self.hass.config_entries.options.async_init(entry.entry_id)
        result = await self.hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "ecobee_stale_seconds": 1200,
                "confirmation_seconds": 600,
                "configure_read_policy": True,
            },
        )
        self.assertEqual(options, entry.options)
        result = await self.hass.config_entries.options.async_configure(
            result["flow_id"],
            {"mapping_id": self.mapping.mapping_id},
        )
        with patch.object(self.hass.config_entries, "async_reload", return_value=True):
            result = await self.hass.config_entries.options.async_configure(
                result["flow_id"],
                dict.fromkeys(READ_POLICY_FIELDS, "ecobee_only"),
            )
            await self.hass.async_block_till_done()
        self.assertEqual(FlowResultType.CREATE_ENTRY, result["type"])
        self.assertEqual(original_data, entry.data)
        self.assertEqual({"keep": True}, entry.options["future_option"])
        self.assertEqual(1200, entry.options["ecobee_stale_seconds"])
        self.assertEqual(600, entry.options["confirmation_seconds"])
        self.assertEqual(
            {"future_field": "keep"} | dict.fromkeys(READ_POLICY_FIELDS, "ecobee_only"),
            entry.options["read_policies"][self.mapping.mapping_id],
        )

    async def test_read_policy_stale_mapping_aborts_without_options_write_or_reload(
        self,
    ) -> None:
        entry = self._entry(options={"future_option": "keep"})
        result = await self.hass.config_entries.options.async_init(entry.entry_id)
        result = await self.hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "ecobee_stale_seconds": 1200,
                "confirmation_seconds": 600,
                "configure_read_policy": True,
            },
        )
        result = await self.hass.config_entries.options.async_configure(
            result["flow_id"],
            {"mapping_id": self.mapping.mapping_id},
        )
        self.hass.config_entries.async_update_entry(
            entry,
            data=dict(entry.data) | {"external_data_change": True},
        )
        with patch.object(self.hass.config_entries, "async_reload") as reload:
            result = await self.hass.config_entries.options.async_configure(
                result["flow_id"],
                dict.fromkeys(READ_POLICY_FIELDS, "ecobee_first"),
            )
            self.assertEqual("configuration_changed", result["reason"])
            self.assertEqual({"future_option": "keep"}, entry.options)
            reload.assert_not_called()

    async def test_saved_registry_references_survive_entity_rename(self) -> None:
        row = _datapoint_from_input(self.hass, self._values())
        er.async_get(self.hass).async_update_entity(
            self.homekit.entity_id,
            new_entity_id="climate.renamed_source",
        )
        defaults = _datapoint_form_defaults(self.hass, row)
        self.assertEqual("climate.renamed_source", defaults["primary_entity"])
        self.assertEqual(self.homekit.id, row["sources"][0]["entity"])

    async def test_interval_requires_source_timestamps_and_preserves_interval(
        self,
    ) -> None:
        entry = self._entry()
        result = await self._next(await self._open(entry), "datapoint_add")
        values = self._values(time_basis="interval", interval_seconds=300)
        result = await self._submit(result, values)
        self.assertEqual("interval_timestamp_required", result["errors"]["base"])
        for source in (self.homekit, self.ecobee):
            state = self.hass.states.get(source.entity_id)
            assert state is not None
            self.hass.states.async_set(
                source.entity_id,
                state.state,
                dict(state.attributes)
                | {"observed_at": state.last_reported.isoformat()},
            )
        result = await self._submit(
            result,
            values
            | {
                "primary_timestamp_attribute": "observed_at",
                "secondary_timestamp_attribute": "observed_at",
            },
        )
        await self._save(result)
        row = entry.data["datapoints"][0]
        self.assertEqual("interval", row["time_basis"])
        self.assertEqual(300, row["interval_seconds"])
        self.assertTrue(
            all(
                source["timestamp_attribute"] == "observed_at"
                for source in row["sources"]
            )
        )

    async def test_duration_form_distinguishes_elapsed_runtime_from_minimum_setting(
        self,
    ) -> None:
        source_entry = self.hass.config_entries.async_get_entry(
            self.ecobee.config_entry_id
        )
        assert source_entry is not None
        number = er.async_get(self.hass).async_get_or_create(
            "number",
            "ecobee",
            "ec_a_fan_min_on_time",
            config_entry=source_entry,
            device_id=self.ecobee.device_id,
            unit_of_measurement="min",
            translation_key="fan_min_on_time",
        )
        self.hass.states.async_set(
            number.entity_id, "15", {"unit_of_measurement": "min"}
        )
        entry = self._entry()
        result = await self._next(await self._open(entry), "datapoint_add")
        values = self._values(
            kind="duration",
            semantic="elapsed_duration",
            unit="min",
            primary_entity=self.ecobee.entity_id,
            primary_attribute="fan_min_on_time",
            secondary_entity=number.entity_id,
            secondary_attribute="",
        )
        result = await self._submit(result, values)
        self.assertEqual("duration_semantic_mismatch", result["errors"]["base"])
        result = await self._submit(
            result, values | {"semantic": "minimum_fan_runtime_per_hour"}
        )
        self.assertEqual(FlowResultType.MENU, result["type"], result.get("errors"))
        await self._save(result)
        self.assertEqual(
            "minimum_fan_runtime_per_hour", entry.data["datapoints"][0]["semantic"]
        )

    async def test_unknown_saved_datapoint_contract_fails_without_losing_the_row(
        self,
    ) -> None:
        row = _datapoint_from_input(self.hass, self._values()) | {
            "kind": "future_quantity"
        }
        entry = self._entry([row])
        result = await self._next(await self._open(entry), "datapoint_edit")
        result = await self._submit(result, {"datapoint_id": row["datapoint_id"]})
        self.assertEqual("datapoint_not_supported", result["reason"])
        self.assertEqual([row], entry.data["datapoints"])

    async def test_second_reconfigure_flow_cannot_overwrite_first_saved_flow(
        self,
    ) -> None:
        entry = self._entry()
        first = await self._next(await self._open(entry), "datapoint_add")
        second = await self._next(await self._open(entry), "datapoint_add")
        first = await self._submit(first, self._values(name="Winner"))
        second = await self._submit(second, self._values(name="Stale edit"))
        await self._save(first)
        accepted = deepcopy(dict(entry.data))
        with patch.object(self.hass.config_entries, "async_reload") as reload:
            second = await self._next(second, "reconfigure_finish")
            self.assertEqual("configuration_changed", second["reason"])
            self.assertEqual(accepted, entry.data)
            reload.assert_not_called()

    async def test_read_policy_uses_same_form_for_both_mappings_and_saves_together(
        self,
    ) -> None:
        entry = self._entry()
        second_mapping = self.mapping.as_dict() | {
            "mapping_id": "mapping_b",
            "name": "Zone B",
        }
        self.hass.config_entries.async_update_entry(
            entry,
            data=dict(entry.data)
            | {
                CONF_MAPPINGS: [self.mapping.as_dict(), second_mapping],
            },
        )
        result = await self.hass.config_entries.options.async_init(entry.entry_id)
        result = await self.hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "ecobee_stale_seconds": 1200,
                "confirmation_seconds": 600,
                "configure_read_policy": True,
            },
        )
        result = await self.hass.config_entries.options.async_configure(
            result["flow_id"],
            {"mapping_id": self.mapping.mapping_id},
        )
        result = await self.hass.config_entries.options.async_configure(
            result["flow_id"],
            dict.fromkeys(READ_POLICY_FIELDS, "homekit_only")
            | {"configure_another": True},
        )
        self.assertEqual({}, entry.options)
        result = await self.hass.config_entries.options.async_configure(
            result["flow_id"],
            {"mapping_id": "mapping_b"},
        )
        with patch.object(self.hass.config_entries, "async_reload", return_value=True):
            await self.hass.config_entries.options.async_configure(
                result["flow_id"],
                dict.fromkeys(READ_POLICY_FIELDS, "ecobee_first"),
            )
            await self.hass.async_block_till_done()
        self.assertEqual(
            {
                self.mapping.mapping_id: dict.fromkeys(
                    READ_POLICY_FIELDS, "homekit_only"
                ),
                "mapping_b": dict.fromkeys(READ_POLICY_FIELDS, "ecobee_first"),
            },
            entry.options["read_policies"],
        )

    async def test_configured_membership_accepts_configured_sources_but_rejects_in_use(
        self,
    ) -> None:
        source_entry = MockConfigEntry(domain="beestat_statistics")
        source_entry.add_to_hass(self.hass)
        device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=source_entry.entry_id,
            identifiers={("beestat_statistics", "stats_thermostat_a")},
            manufacturer="ecobee",
            serial_number="thermostat_a",
        )
        profile = er.async_get(self.hass).async_get_or_create(
            "sensor",
            "beestat_statistics",
            "stats_thermostat_a_profile",
            config_entry=source_entry,
            device_id=device.id,
        )
        members = ["Thermostat", "Living Room"]
        self.hass.states.async_set(
            profile.entity_id,
            "Home",
            {
                "profile_sensors": members,
                "profile_ref": "home",
                "in_use": members,
            },
        )
        cloud = self.hass.states.get(self.ecobee.entity_id)
        assert cloud is not None
        self.hass.states.async_set(
            self.ecobee.entity_id,
            cloud.state,
            dict(cloud.attributes) | {"active_sensors": members},
        )
        entry = self._entry()
        result = await self._next(await self._open(entry), "datapoint_add")
        values = self._values(
            name="Configured comfort sensors",
            kind="configured_membership",
            unit="",
            primary_entity=self.ecobee.entity_id,
            primary_attribute="active_sensors",
            secondary_entity=profile.entity_id,
            secondary_attribute="in_use",
        )
        result = await self._submit(result, values)
        self.assertEqual("membership_attribute_required", result["errors"]["base"])
        result = await self._submit(
            result, values | {"secondary_attribute": "profile_sensors"}
        )
        self.assertEqual(FlowResultType.MENU, result["type"], result.get("errors"))
        await self._save(result)
        row = entry.data["datapoints"][0]
        self.assertEqual("configured_membership", row["kind"])
        self.assertEqual("current", row["time_basis"])
        self.assertIsNone(row["semantic"])
        self.assertIsNone(row["unit"])
