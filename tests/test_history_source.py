"""Native registry and cached-service boundaries for historical source binding."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import skipUnless
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, ServiceCall, SupportsResponse
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ecobee_unified.const import DOMAIN
from custom_components.ecobee_unified.datapoints import DatapointConfig, SourceBinding
from custom_components.ecobee_unified.history_source import (
    async_capture_source,
    async_validate_source,
)

from .runtime_fixture import CoreRuntimeTestCase


class HistorySourceTests(CoreRuntimeTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.registry = er.async_get(self.hass)
        self.devices = dr.async_get(self.hass)
        assert self.homekit.device_id
        self.devices.async_update_device(
            self.homekit.device_id, manufacturer="ecobee", model="Thermostat"
        )
        self.metadata: dict[str, dict[str, Any]] = {}
        self.metadata_mock = AsyncMock(side_effect=self._metadata)
        self.metadata_patch = patch(
            "custom_components.ecobee_unified.history_source.async_list_statistic_ids",
            self.metadata_mock,
        )
        self.metadata_patch.start()
        self.addCleanup(self.metadata_patch.stop)
        self.temperature = self._sensor("temperature", "°F")
        self.humidity = self._sensor("humidity", "%")
        self.co2 = self._sensor("carbon_dioxide", "ppm")
        self.aqi = self._sensor("aqi", None)
        self.voc = self._sensor("volatile_organic_compounds", "μg/m³")
        self.beestat = MockConfigEntry(
            domain="beestat_statistics", state=ConfigEntryState.LOADED
        )
        self.beestat.add_to_hass(self.hass)
        self.response: dict[str, Any] = {
            "config_entry_id": self.beestat.entry_id,
            "effective_configuration": {
                "thermostats": [
                    {
                        "thermostat_id": 101,
                        "slug": "zone_a",
                        "climate_entity_id": self.homekit.entity_id,
                        "temperature_entity_id": self.temperature.entity_id,
                    }
                ],
                "sensors": [
                    {
                        "sensor_id": 202,
                        "thermostat_id": 101,
                        "thermostat_slug": "zone_a",
                        "slug": "zone_a",
                        "temperature_entity_id": self.temperature.entity_id,
                        "include_temperature": True,
                        "include_co2": True,
                        "include_air_quality": True,
                        "include_voc": True,
                    }
                ],
            },
            "source_details": {"private_property": "must_not_be_returned"},
            "saved_overrides": {"sensors": {"source": "automatic", "items": []}},
        }
        self.calls: list[ServiceCall] = []

        async def configuration(call: ServiceCall) -> dict[str, Any]:
            self.calls.append(call)
            return deepcopy(self.response)

        self.hass.services.async_register(
            "beestat_statistics",
            "get_configuration",
            configuration,
            supports_response=SupportsResponse.ONLY,
        )

    async def _metadata(self, hass, *, statistic_ids):
        self.assertIs(hass, self.hass)
        self.assertIsInstance(statistic_ids, set)
        self.assertEqual(len(statistic_ids), 1)
        return [
            deepcopy(self.metadata[key])
            for key in statistic_ids
            if key in self.metadata
        ]

    def _sensor(self, device_class: str, unit: str | None) -> er.RegistryEntry:
        config_entry = self.hass.config_entries.async_get_entry(
            self.ecobee.config_entry_id
        )
        assert config_entry is not None
        entry = self.registry.async_get_or_create(
            "sensor",
            "ecobee",
            f"thermostat_a-ei:0-{device_class}",
            config_entry=config_entry,
            device_id=self.ecobee.device_id,
            original_device_class=device_class,
            unit_of_measurement=unit,
            suggested_object_id=f"zone_a_{device_class}",
        )
        attrs = {"device_class": device_class, "state_class": "measurement"}
        if unit:
            attrs["unit_of_measurement"] = unit
        self.hass.states.async_set(entry.entity_id, "30", attrs)
        unit_class = (
            "temperature"
            if device_class == "temperature"
            else "concentration"
            if device_class == "volatile_organic_compounds"
            else "unitless"
        )
        self._statistic(entry.entity_id, unit, unit_class)
        return entry

    def _statistic(
        self, statistic_id: str, unit: str | None, unit_class: str = "unitless"
    ) -> str:
        self.metadata[statistic_id] = {
            "statistic_id": statistic_id,
            "source": "beestat" if statistic_id.startswith("beestat:") else "recorder",
            "statistics_unit_of_measurement": unit,
            "display_unit_of_measurement": unit,
            "unit_class": unit_class,
            "mean_type": 1,
            "has_mean": True,
            "has_sum": False,
        }
        return statistic_id

    def _diagnostic(self, unique_id: str, state: str, **attributes) -> er.RegistryEntry:
        entry = self.registry.async_get_or_create(
            "sensor",
            "beestat_statistics",
            unique_id,
            config_entry=self.beestat,
            device_id=self.homekit.device_id,
        )
        self.hass.states.async_set(entry.entity_id, state, attributes)
        return entry

    def _composed_humidity(self) -> tuple[MockConfigEntry, er.RegistryEntry, dict]:
        config = DatapointConfig(
            "measured",
            "Measured humidity",
            "humidity",
            (
                SourceBinding(self.homekit.id, "current_humidity"),
                SourceBinding(self.ecobee.id, "current_humidity"),
            ),
            unit="%",
        ).as_dict()
        owner = MockConfigEntry(domain=DOMAIN, data={"datapoints": [config]})
        owner.add_to_hass(self.hass)
        output = self.registry.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{owner.entry_id}_datapoint_measured",
            config_entry=owner,
            device_id=self.homekit.device_id,
            original_device_class="humidity",
            unit_of_measurement="%",
        )
        self.hass.states.async_set(
            output.entity_id,
            "40",
            {
                "device_class": "humidity",
                "unit_of_measurement": "%",
                "state_class": "measurement",
                "semantic": "measured_humidity",
            },
        )
        self._statistic(output.entity_id, "%")
        return owner, output, config

    async def test_composed_humidity_revalidates_the_saved_role_owner(self) -> None:
        """Matching state labels cannot substitute for the saved native role proof."""
        owner, output, config = self._composed_humidity()
        captured = await async_capture_source(
            self.hass, output.entity_id, "humidity", self.humidity.id
        )
        variants = {}
        for name, attributes in (
            ("target", ("humidity", "humidity")),
            ("mixed", ("current_humidity", "humidity")),
            ("opaque", ("opaque_humidity", "opaque_humidity")),
        ):
            row = deepcopy(config)
            for binding, attribute in zip(row["sources"], attributes, strict=True):
                binding["attribute"] = attribute
            variants[name] = [row]
        interval = deepcopy(config)
        interval.update(time_basis="interval", interval_seconds=3600)
        for binding in interval["sources"]:
            binding["timestamp_attribute"] = "observed_at"
        missing = deepcopy(config)
        missing["sources"][0]["entity"] = "deleted_registry_uuid"
        malformed = deepcopy(config)
        del malformed["kind"]
        variants.update(
            interval=[interval],
            missing=[missing],
            malformed=[malformed],
            duplicate=[config, deepcopy(config)],
            absent=[],
        )
        for name, rows in variants.items():
            with self.subTest(name=name):
                self.hass.config_entries.async_update_entry(
                    owner, data={"datapoints": rows}
                )
                for source, anchor in (
                    (output.entity_id, self.humidity.id),
                    (self.humidity.entity_id, output.id),
                ):
                    with self.assertRaisesRegex(
                        ValueError, "historical_source_role_mismatch"
                    ):
                        await async_capture_source(
                            self.hass, source, "humidity", anchor
                        )
                with self.assertRaisesRegex(
                    ValueError, "historical_source_role_mismatch"
                ):
                    await async_validate_source(
                        self.hass, captured, "humidity", self.humidity.id
                    )
        self.hass.config_entries.async_update_entry(
            owner, data={"datapoints": [config]}
        )
        self.assertEqual(
            await async_validate_source(
                self.hass, captured, "humidity", self.humidity.id
            ),
            captured,
        )

    async def test_composed_humidity_requires_output_and_binding_identity_agreement(
        self,
    ) -> None:
        _owner, output, _config = self._composed_humidity()
        captured = await async_capture_source(
            self.hass, output.entity_id, "humidity", self.humidity.id
        )
        other = self._source(
            "ecobee", "other_thermostat", device=True, physical_identity="thermostat_b"
        )
        self.registry.async_update_entity(output.entity_id, device_id=other.device_id)
        with self.assertRaisesRegex(ValueError, "historical_source_identity_mismatch"):
            await async_capture_source(
                self.hass, output.entity_id, "humidity", output.id
            )
        self.registry.async_update_entity(
            output.entity_id, device_id=self.homekit.device_id
        )
        self.assertEqual(
            await async_validate_source(
                self.hass, captured, "humidity", self.humidity.id
            ),
            captured,
        )

    async def test_native_families_keep_units_and_raw_aqi_forward_transform(
        self,
    ) -> None:
        for entry, quantity in (
            (self.temperature, "temperature"),
            (self.humidity, "humidity"),
            (self.co2, "co2"),
            (self.aqi, "aqi"),
            (self.voc, "voc"),
        ):
            with self.subTest(quantity=quantity):
                source = await async_capture_source(
                    self.hass, entry.entity_id, quantity, entry.id
                )
                self.assertEqual(source["method"], "recorder_hourly_time_weighted")
                self.assertEqual(source["entity_ref"], entry.id)
                self.assertEqual(source["anchor_ref"], entry.id)
                self.assertIsNone(source["settlement_basis"])
                self.assertEqual(
                    source["transformation"],
                    "aqi_raw_div350_times100" if quantity == "aqi" else "identity",
                )
                self.assertEqual(
                    source["identity_evidence"]["historical_continuity"], "unknown"
                )
        self.assertFalse(self.calls)

    async def test_unavailable_current_value_preserves_registry_history_identity(
        self,
    ) -> None:
        self.hass.states.async_set(self.co2.entity_id, "unavailable")
        source = await async_capture_source(
            self.hass, self.co2.entity_id, "co2", self.co2.id
        )
        self.assertEqual(source["native_unit"], "ppm")

    @skipUnless(
        hasattr(dr.DeviceRegistry, "async_get_or_create_child"),
        "This Core version predates native child devices",
    )
    async def test_native_child_device_cannot_supply_physical_history_identity(
        self,
    ) -> None:
        """A logical child cannot inherit its parent's physical sensor proof."""
        config = self.hass.config_entries.async_get_entry(self.ecobee.config_entry_id)
        assert config is not None and self.ecobee.device_id is not None
        child = self.devices.async_get_or_create_child(
            config_entry_id=config.entry_id,
            parent_device_id=self.ecobee.device_id,
            identifiers={("ecobee", "logical_child")},
        )
        for entry, quantity in (
            (self.temperature, "temperature"),
            (self.co2, "co2"),
        ):
            with self.subTest(quantity=quantity):
                self.registry.async_update_entity(entry.entity_id, device_id=child.id)
                with self.assertRaisesRegex(
                    ValueError, "historical_source_identity_unproven"
                ):
                    await async_capture_source(
                        self.hass, entry.entity_id, quantity, entry.id
                    )

    async def test_linked_parent_requires_physical_serial_and_recovers(self) -> None:
        """An explicit parent association must retain its own physical proof."""
        assert self.ecobee.device_id is not None
        assert self.homekit.device_id is not None
        self.devices.async_update_device(
            self.ecobee.device_id, via_device_id=self.homekit.device_id
        )
        source = await async_capture_source(
            self.hass, self.co2.entity_id, "co2", self.co2.id
        )
        self.devices.async_update_device(self.homekit.device_id, serial_number=None)
        with self.assertRaisesRegex(ValueError, "historical_source_identity_unproven"):
            await async_validate_source(self.hass, source, "co2", self.co2.id)
        self.devices.async_update_device(
            self.homekit.device_id, serial_number="thermostat_a"
        )
        recovered = await async_validate_source(self.hass, source, "co2", self.co2.id)
        self.assertEqual(recovered, source)

    async def test_parent_accepts_ecobee_identifier_but_rejects_conflicting_serial(
        self,
    ) -> None:
        """Parents use the same physical evidence contract as selected sources."""
        config = self.hass.config_entries.async_get_entry(self.ecobee.config_entry_id)
        assert config is not None and self.ecobee.device_id is not None
        parent = self.devices.async_get_or_create(
            config_entry_id=config.entry_id,
            identifiers={("ecobee", "parent_a")},
        )
        self.devices.async_update_device(self.ecobee.device_id, via_device_id=parent.id)
        source = await async_capture_source(
            self.hass, self.co2.entity_id, "co2", self.co2.id
        )
        self.assertEqual(
            source["identity_evidence"]["source"]["hardware"]["parent_serial"],
            "parent_a",
        )
        self.devices.async_update_device(parent.id, serial_number="parent_b")
        with self.assertRaisesRegex(ValueError, "historical_source_identity_mismatch"):
            await async_validate_source(self.hass, source, "co2", self.co2.id)
        self.devices.async_update_device(parent.id, serial_number="parent_a")
        self.assertEqual(
            await async_validate_source(self.hass, source, "co2", self.co2.id), source
        )

    async def test_identifier_only_parents_cannot_hide_ambiguous_remote_serial(
        self,
    ) -> None:
        """Cross-device matching includes parents with only Ecobee identifiers."""
        config = self.hass.config_entries.async_get_entry(self.homekit.config_entry_id)
        assert config is not None
        anchor = self.registry.async_get_or_create(
            "sensor",
            "homekit_controller",
            "anchor_co2",
            config_entry=config,
            device_id=self.homekit.device_id,
            original_device_class="carbon_dioxide",
            unit_of_measurement="ppm",
        )
        for index in range(2):
            parent = self.devices.async_get_or_create(
                config_entry_id=config.entry_id,
                identifiers={("ecobee", f"parent_{index}")},
            )
            remote = self.devices.async_get_or_create(
                config_entry_id=config.entry_id,
                identifiers={("homekit_controller", f"remote_{index}")},
                manufacturer="ecobee",
                serial_number="thermostat_a",
            )
            self.devices.async_update_device(remote.id, via_device_id=parent.id)
            if index == 0:
                await async_capture_source(
                    self.hass, self.co2.entity_id, "co2", anchor.id
                )
        with self.assertRaisesRegex(ValueError, "historical_source_identity_unproven"):
            await async_capture_source(self.hass, self.co2.entity_id, "co2", anchor.id)

    async def test_control_temperature_cannot_be_physical_history_anchor(self) -> None:
        self._statistic(self.homekit_temperature.entity_id, "°C", "temperature")
        with self.assertRaisesRegex(ValueError, "historical_source_role_mismatch"):
            await async_capture_source(
                self.hass,
                self.homekit_temperature.entity_id,
                "temperature",
                self.homekit_temperature.id,
            )

    async def test_changed_native_identity_and_same_name_replacement_are_rejected(
        self,
    ) -> None:
        source = await async_capture_source(
            self.hass, self.co2.entity_id, "co2", self.co2.id
        )
        self.registry.async_remove(self.co2.entity_id)
        self.hass.states.async_remove(self.co2.entity_id)
        config = self.hass.config_entries.async_get_entry(self.ecobee.config_entry_id)
        assert config is not None
        replacement = self.registry.async_get_or_create(
            "sensor",
            "ecobee",
            "replacement-ei:0-carbon_dioxide",
            config_entry=config,
            device_id=self.ecobee.device_id,
            original_device_class="carbon_dioxide",
            unit_of_measurement="ppm",
            suggested_object_id=self.co2.entity_id.split(".", 1)[1],
        )
        self.assertEqual(replacement.entity_id, source["statistic_id"])
        self.assertNotEqual(replacement.id, source["entity_ref"])
        with self.assertRaisesRegex(ValueError, "historical_source_changed"):
            await async_validate_source(self.hass, source, "co2", replacement.id)

    async def test_metadata_drift_cannot_reinterpret_saved_units(self) -> None:
        source = await async_capture_source(
            self.hass, self.temperature.entity_id, "temperature", self.temperature.id
        )
        self.metadata[self.temperature.entity_id]["statistics_unit_of_measurement"] = (
            "°C"
        )
        with self.assertRaisesRegex(ValueError, "historical_source_changed"):
            await async_validate_source(
                self.hass, source, "temperature", self.temperature.id
            )
        self.metadata[self.temperature.entity_id]["mean_type"] = 2
        with self.assertRaisesRegex(ValueError, "historical_source_metadata_invalid"):
            await async_capture_source(
                self.hass,
                self.temperature.entity_id,
                "temperature",
                self.temperature.id,
            )

    async def test_missing_statistics_and_unsupported_owners_are_rejected(self) -> None:
        self.metadata.pop(self.co2.entity_id)
        with self.assertRaisesRegex(
            ValueError, "historical_source_metadata_unavailable"
        ):
            await async_capture_source(
                self.hass, self.co2.entity_id, "co2", self.co2.id
            )
        with self.assertRaisesRegex(ValueError, "historical_unsupported_quantity"):
            await async_capture_source(
                self.hass, self.co2.entity_id, "occupancy", self.co2.id
            )

    async def test_beestat_exact_all_quantity_contracts_preserve_owner_proof_limit(
        self,
    ) -> None:
        cases = (
            ("temperature", self.temperature, "temperature", "°F", "temperature"),
            ("humidity", self.humidity, "indoor_humidity", "%", "unitless"),
            ("co2", self.co2, "co2_concentration", "ppm", "unitless"),
            ("aqi", self.aqi, "air_quality", "%", "unitless"),
            ("voc", self.voc, "voc_concentration", "ppb", "unitless"),
        )
        context = Context()
        for quantity, anchor, suffix, unit, unit_class in cases:
            with self.subTest(quantity=quantity):
                statistic = self._statistic(
                    f"beestat:zone_a_{suffix}", unit, unit_class
                )
                source = await async_capture_source(
                    self.hass, statistic, quantity, anchor.id, context=context
                )
                expected = (
                    "beestat_legacy_daily_humidity_summary"
                    if quantity == "humidity"
                    else "beestat_legacy_daily_sample_mean"
                )
                self.assertEqual(source["method"], expected)
                self.assertEqual(source["transformation"], "identity")
                self.assertEqual(
                    source["identity_evidence"]["basis"],
                    "beestat_owner_association_requires_confirmation",
                )
                self.assertEqual(
                    source["identity_evidence"]["provider_serial_identity"], "unproven"
                )
                self.assertNotIn("provider_data_through", source)
                self.assertNotIn("source_details", source)
                self.assertNotIn("must_not_be_returned", str(source))
                self.assertIs(self.calls[-1].context, context)
                self.assertEqual(
                    dict(self.calls[-1].data),
                    {"config_entry_id": self.beestat.entry_id},
                )

    async def test_beestat_watermarks_are_optional_transient_and_never_settlement(
        self,
    ) -> None:
        statistic = self._statistic("beestat:zone_a_temperature", "°F", "temperature")
        source = await async_capture_source(
            self.hass, statistic, "temperature", self.temperature.id
        )
        source["source_id"] = "stable_source_a"
        source["extension"] = {"preserve": True}
        beginning = datetime(2024, 1, 1, tzinfo=UTC).isoformat()
        end = datetime(2024, 1, 2, tzinfo=UTC).isoformat()
        diagnostic = self._diagnostic(
            "thermostat_101_cloud_data_end", end, data_begin=beginning
        )
        self._diagnostic(
            "statistics_last_import_success",
            datetime(2024, 1, 3, tzinfo=UTC).isoformat(),
        )
        refreshed = await async_validate_source(
            self.hass, source, "temperature", self.temperature.id
        )
        self.assertEqual(refreshed["provider_data_through"], end)
        self.assertEqual(refreshed["provider_data_through_ref"], diagnostic.id)
        self.assertEqual(refreshed["provider_window_begin"], beginning)
        self.assertEqual(refreshed["source_id"], "stable_source_a")
        self.assertEqual(refreshed["extension"], {"preserve": True})
        self.assertIsNone(refreshed["settlement_basis"])
        self.hass.states.async_set(diagnostic.entity_id, "unavailable")
        unavailable = await async_validate_source(
            self.hass, refreshed, "temperature", self.temperature.id
        )
        self.assertNotIn("provider_data_through", unavailable)
        self.assertNotIn("provider_data_through_ref", unavailable)
        self.assertNotIn("provider_window_begin", unavailable)

    async def test_future_or_disabled_watermark_does_not_claim_provider_progress(
        self,
    ) -> None:
        statistic = self._statistic("beestat:zone_a_temperature", "°F", "temperature")
        future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        diagnostic = self._diagnostic("thermostat_101_cloud_data_end", future)
        source = await async_capture_source(
            self.hass, statistic, "temperature", self.temperature.id
        )
        self.assertNotIn("provider_data_through", source)
        self.hass.states.async_set(diagnostic.entity_id, "2024-01-02T00:00:00+00:00")
        self.registry.async_update_entity(
            diagnostic.entity_id, disabled_by=er.RegistryEntryDisabler.USER
        )
        source = await async_capture_source(
            self.hass, statistic, "temperature", self.temperature.id
        )
        self.assertNotIn("provider_data_through", source)

    async def test_mapping_identity_mismatch_duplicate_and_successor_rejected(
        self,
    ) -> None:
        statistic = self._statistic("beestat:zone_a_temperature", "°F", "temperature")
        source = await async_capture_source(
            self.hass, statistic, "temperature", self.temperature.id
        )
        self.response["effective_configuration"]["sensors"][0]["sensor_id"] = 303
        with self.assertRaisesRegex(ValueError, "historical_source_changed"):
            await async_validate_source(
                self.hass, source, "temperature", self.temperature.id
            )
        self.response["effective_configuration"]["sensors"].append(
            deepcopy(self.response["effective_configuration"]["sensors"][0])
        )
        with self.assertRaisesRegex(ValueError, "historical_beestat_mapping_ambiguous"):
            await async_capture_source(
                self.hass, statistic, "temperature", self.temperature.id
            )
        successor = self._statistic(f"{statistic}_hourly_v2", "°F", "temperature")
        with self.assertRaisesRegex(ValueError, "historical_unsupported_source"):
            await async_capture_source(
                self.hass, successor, "temperature", self.temperature.id
            )

    async def test_wrong_selected_entry_response_and_bad_parent_rejected(self) -> None:
        statistic = self._statistic("beestat:zone_a_temperature", "°F", "temperature")
        self.response["config_entry_id"] = "another_entry"
        with self.assertRaisesRegex(ValueError, "historical_beestat_mapping_invalid"):
            await async_capture_source(
                self.hass, statistic, "temperature", self.temperature.id
            )
        self.response["config_entry_id"] = self.beestat.entry_id
        self.response["effective_configuration"]["sensors"][0]["thermostat_id"] = 999
        with self.assertRaisesRegex(ValueError, "historical_beestat_mapping_invalid"):
            await async_capture_source(
                self.hass, statistic, "temperature", self.temperature.id
            )

    async def test_native_foreign_hardware_rejected_even_with_same_quantity(
        self,
    ) -> None:
        assert self.ecobee.device_id
        source = await async_capture_source(
            self.hass, self.co2.entity_id, "co2", self.co2.id
        )
        other = self._source(
            "ecobee",
            "other",
            domain="sensor",
            device=True,
            physical_identity="thermostat_b",
        )
        self.registry.async_update_entity(
            other.entity_id, original_device_class="carbon_dioxide"
        )
        self.hass.states.async_set(
            other.entity_id,
            "400",
            {"device_class": "carbon_dioxide", "unit_of_measurement": "ppm"},
        )
        with self.assertRaisesRegex(ValueError, "historical_source_identity_mismatch"):
            await async_validate_source(self.hass, source, "co2", other.id)

    async def test_battery_notes_mirror_requires_native_battery_role_and_same_device(
        self,
    ) -> None:
        homekit_entry = self.hass.config_entries.async_get_entry(
            self.homekit.config_entry_id
        )
        assert homekit_entry is not None
        battery = self.registry.async_get_or_create(
            "sensor",
            "homekit_controller",
            "battery_a",
            config_entry=homekit_entry,
            device_id=self.homekit.device_id,
            original_device_class="battery",
            unit_of_measurement="%",
        )
        notes_config = MockConfigEntry(domain="battery_notes")
        notes_config.add_to_hass(self.hass)
        notes = self.registry.async_get_or_create(
            "sensor",
            "battery_notes",
            "battery_a_note",
            config_entry=notes_config,
            device_id=self.homekit.device_id,
            original_device_class="battery",
            unit_of_measurement="%",
        )
        self._statistic(battery.entity_id, "%")
        self._statistic(notes.entity_id, "%")
        source = await async_capture_source(
            self.hass, notes.entity_id, "battery", battery.id
        )
        self.assertEqual(source["entity_ref"], notes.id)
        self.assertEqual(
            source["identity_evidence"]["source"]["platform"], "battery_notes"
        )
        self.hass.states.async_set(
            notes.entity_id,
            "2.9",
            {"device_class": "battery", "unit_of_measurement": "V"},
        )
        with self.assertRaisesRegex(ValueError, "historical_source_unit_mismatch"):
            await async_validate_source(self.hass, source, "battery", battery.id)

    def _weather(self, serial: str, device_id: str) -> er.RegistryEntry:
        config = self.hass.config_entries.async_get_entry(self.ecobee.config_entry_id)
        assert config is not None
        weather = self.registry.async_get_or_create(
            "weather",
            "ecobee",
            serial,
            config_entry=config,
            device_id=device_id,
        )
        self.hass.states.async_set(
            weather.entity_id,
            "sunny",
            {
                "temperature": 70,
                "temperature_unit": "°F",
                "humidity": 40,
                "attribution": "Ecobee weather provided by Station A at 2024-01-02 00:00:00 UTC",
            },
        )
        return weather

    async def test_weather_aliases_require_current_native_feed_and_keep_history_unknown(
        self,
    ) -> None:
        assert self.ecobee.device_id
        first = self._weather("thermostat_a", self.ecobee.device_id)
        config = self.hass.config_entries.async_get_entry(self.ecobee.config_entry_id)
        assert config is not None
        device = self.devices.async_get_or_create(
            config_entry_id=config.entry_id,
            identifiers={("ecobee", "thermostat_b")},
        )
        second = self._weather("thermostat_b", device.id)
        parent = self._source(
            "homekit_controller",
            "weather_parent_b",
            device=True,
            physical_identity="thermostat_b",
        )
        assert parent.device_id
        self.devices.async_update_device(parent.device_id, manufacturer="ecobee")
        self.response["effective_configuration"]["thermostats"].append(
            {
                "thermostat_id": 303,
                "slug": "zone_b",
                "climate_entity_id": parent.entity_id,
            }
        )
        for quantity, suffix, unit, unit_class in (
            ("weather_temperature", "outdoor_temperature", "°F", "temperature"),
            ("weather_humidity", "outdoor_humidity", "%", "unitless"),
        ):
            for zone in ("zone_a", "zone_b"):
                statistic = self._statistic(
                    f"beestat:{zone}_{suffix}", unit, unit_class
                )
                source = await async_capture_source(
                    self.hass, statistic, quantity, first.id
                )
                self.assertEqual(
                    source["method"], "beestat_legacy_daily_weather_summary"
                )
                self.assertEqual(
                    source["identity_evidence"]["current_native_feed"]["station"],
                    "Station A",
                )
                self.assertEqual(
                    source["identity_evidence"]["historical_provider_station"],
                    "unknown",
                )
        state = self.hass.states.get(second.entity_id)
        assert state is not None
        self.hass.states.async_set(
            second.entity_id,
            "sunny",
            dict(state.attributes)
            | {
                "attribution": "Ecobee weather provided by Other Station at 2024-01-02 00:00:00 UTC",
            },
        )
        with self.assertRaisesRegex(ValueError, "historical_source_identity_mismatch"):
            await async_validate_source(self.hass, source, "weather_humidity", first.id)

    async def test_builtin_probe_cannot_be_associated_with_another_thermostat(
        self,
    ) -> None:
        statistic = self._statistic("beestat:zone_a_temperature", "°F", "temperature")
        parent = self._source(
            "homekit_controller",
            "wrong_parent",
            device=True,
            physical_identity="thermostat_b",
        )
        assert parent.device_id
        self.devices.async_update_device(parent.device_id, manufacturer="ecobee")
        self.response["effective_configuration"]["thermostats"][0][
            "climate_entity_id"
        ] = parent.entity_id
        with self.assertRaisesRegex(ValueError, "historical_source_identity_mismatch"):
            await async_capture_source(
                self.hass, statistic, "temperature", self.temperature.id
            )

    async def test_validation_phase_cache_omits_private_payload_and_refreshes_next_phase(
        self,
    ) -> None:
        statistic = self._statistic("beestat:zone_a_temperature", "°F", "temperature")
        cache: dict[str, Any] = {}
        source = await async_capture_source(
            self.hass,
            statistic,
            "temperature",
            self.temperature.id,
            validation_cache=cache,
        )
        await async_validate_source(
            self.hass,
            source,
            "temperature",
            self.temperature.id,
            validation_cache=cache,
        )
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("source_details", str(cache))
        self.assertNotIn("saved_overrides", str(cache))
        self.assertNotIn("must_not_be_returned", str(cache))
        self.response["effective_configuration"]["sensors"][0]["sensor_id"] = 303
        with self.assertRaisesRegex(ValueError, "historical_source_changed"):
            await async_validate_source(
                self.hass,
                source,
                "temperature",
                self.temperature.id,
                validation_cache={},
            )
        self.assertEqual(len(self.calls), 2)
