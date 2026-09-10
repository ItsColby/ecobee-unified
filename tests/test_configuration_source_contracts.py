"""Configuration must validate live semantics and prove new source associations."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any
from unittest.mock import patch

import voluptuous as vol
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import UnitOfDensity, UnitOfRatio
from homeassistant.core import ServiceCall
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ecobee_unified.config_flow import (
    OPTIONAL_SOURCE_KEYS,
    EcobeeUnifiedConfigFlow,
    _mapping_form_defaults,
    _mapping_from_input,
)
from custom_components.ecobee_unified.const import (
    CONF_ADD_ANOTHER,
    CONF_CONFIRM_CHANGE,
    CONF_CONFIRMATION_SECONDS,
    CONF_ECOBEE_AQI_ENTITY,
    CONF_ECOBEE_CO2_ENTITY,
    CONF_ECOBEE_ENTITY,
    CONF_ECOBEE_NOTIFY_ENTITY,
    CONF_ECOBEE_STALE_SECONDS,
    CONF_ECOBEE_VOC_ENTITY,
    CONF_HOMEKIT_CLEAR_HOLD_ENTITY,
    CONF_HOMEKIT_ENTITY,
    CONF_HOMEKIT_PRESET_ENTITY,
    CONF_HOMEKIT_TEMPERATURE_ENTITY,
    CONF_MAPPING_ID,
    CONF_MAPPINGS,
    CONF_NAME,
    DOMAIN,
    HOMEKIT_PAIR_SETTLE_SECONDS,
)
from custom_components.ecobee_unified.manager import MappingManager
from custom_components.ecobee_unified.models import MappingConfig, SourceHealth

from .runtime_fixture import CoreRuntimeTestCase


class ConfigurationSourceContractTests(CoreRuntimeTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.registry = er.async_get(self.hass)

    async def test_native_action_names_are_trimmed_before_length_validation(
        self,
    ) -> None:
        calls: list[ServiceCall] = []

        async def capture(call: ServiceCall) -> None:
            calls.append(call)

        assert self.ecobee.device_id is not None
        cases = (
            (
                "create_vacation",
                "vacation_name",
                12,
                {"cool_temp": 28, "heat_temp": 15},
            ),
            ("delete_vacation", "vacation_name", 12, {}),
            (
                "set_sensors_used_in_climate",
                "preset_mode",
                64,
                {"device_ids": [self.ecobee.device_id]},
            ),
        )
        for service, _field, _limit, _data in cases:
            self.hass.services.async_register("ecobee", service, capture)
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=DOMAIN,
            data={CONF_MAPPINGS: [self.mapping.as_dict()]},
            version=1,
            minor_version=3,
        )
        entry.add_to_hass(self.hass)
        self.assertTrue(await self.hass.config_entries.async_setup(entry.entry_id))
        await self.hass.async_block_till_done()
        try:
            unified_id = self.registry.async_get_entity_id(
                "climate", DOMAIN, self.mapping.mapping_id
            )
            assert unified_id is not None
            for service, field, limit, data in cases:
                with self.subTest(service=service):
                    name = "A" + "b" * (limit - 1)
                    calls.clear()
                    await self.hass.services.async_call(
                        DOMAIN,
                        service,
                        {"entity_id": unified_id, field: f"  {name}  ", **data},
                        blocking=True,
                    )
                    self.assertEqual(1, len(calls))
                    self.assertEqual(service, calls[0].service)
                    self.assertEqual(self.ecobee.entity_id, calls[0].data["entity_id"])
                    self.assertEqual(name, calls[0].data[field])

                    for invalid_name in ("   ", f"  {name}x  "):
                        calls.clear()
                        with self.assertRaises(vol.Invalid):
                            await self.hass.services.async_call(
                                DOMAIN,
                                service,
                                {"entity_id": unified_id, field: invalid_name, **data},
                                blocking=True,
                            )
                        self.assertFalse(calls)

            state = self.hass.states.get(self.ecobee.entity_id)
            assert state is not None
            self.hass.states.async_set(
                self.ecobee.entity_id,
                state.state,
                dict(state.attributes) | {"climate_mode": "Away"},
            )
            await self.hass.async_block_till_done()
            calls.clear()
            await self.hass.services.async_call(
                DOMAIN,
                "set_sensors_used_in_climate",
                {"entity_id": unified_id, "device_ids": [self.ecobee.device_id]},
                blocking=True,
            )
            self.assertEqual(1, len(calls))
            self.assertEqual("Away", calls[0].data["preset_mode"])
        finally:
            self.assertTrue(await self.hass.config_entries.async_unload(entry.entry_id))

    async def test_repairs_identify_mappings_after_rename_and_recover_independently(
        self,
    ) -> None:
        await self.manager.async_stop()
        homekit_b = self._source(
            "homekit_controller",
            "hk_repair_b",
            device=True,
            physical_identity="thermostat_repair_b",
        )
        ecobee_b = self._source(
            "ecobee",
            "ec_repair_b",
            device=True,
            physical_identity="thermostat_repair_b",
        )
        mapping_b = MappingConfig("mapping_b", "Zone B", homekit_b.id, ecobee_b.id)
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=DOMAIN,
            data={CONF_MAPPINGS: [self.mapping.as_dict(), mapping_b.as_dict()]},
            version=1,
            minor_version=3,
        )
        entry.add_to_hass(self.hass)
        self.assertTrue(await self.hass.config_entries.async_setup(entry.entry_id))
        await self.hass.async_block_till_done()
        try:
            for source in (self.ecobee, ecobee_b):
                self.registry.async_update_entity(
                    source.entity_id, disabled_by=er.RegistryEntryDisabler.USER
                )
            await self.hass.async_block_till_done()
            issues = ir.async_get(self.hass)
            for mapping in (self.mapping, mapping_b):
                issue = issues.async_get_issue(DOMAIN, f"mapping_{mapping.mapping_id}")
                assert issue is not None
                self.assertEqual(
                    {"mapping": mapping.name, "source": "ecobee disabled"},
                    issue.translation_placeholders,
                )

            result = await self.hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "reconfigure", "entry_id": entry.entry_id},
            )
            result = await self.hass.config_entries.flow.async_configure(
                result["flow_id"], {"next_step_id": "reconfigure_edit"}
            )
            result = await self.hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_MAPPING_ID: self.mapping.mapping_id}
            )
            form_values = {
                key: value
                for key, value in _mapping_form_defaults(
                    self.hass, self.mapping.as_dict()
                ).items()
                if value is not None
            }
            result = await self.hass.config_entries.flow.async_configure(
                result["flow_id"], form_values | {CONF_NAME: "Renamed Zone A"}
            )
            result = await self.hass.config_entries.flow.async_configure(
                result["flow_id"], {"next_step_id": "reconfigure_finish"}
            )
            self.assertEqual("reconfigure_successful", result["reason"])
            await self.hass.async_block_till_done()
            issue_a = issues.async_get_issue(DOMAIN, "mapping_mapping_a")
            assert issue_a is not None
            self.assertEqual(
                {"mapping": "Renamed Zone A", "source": "ecobee disabled"},
                issue_a.translation_placeholders,
            )
            issue_b = issues.async_get_issue(DOMAIN, "mapping_mapping_b")
            assert issue_b is not None
            self.assertEqual(
                {"mapping": "Zone B", "source": "ecobee disabled"},
                issue_b.translation_placeholders,
            )

            self.registry.async_update_entity(self.ecobee.entity_id, disabled_by=None)
            await self.hass.async_block_till_done()
            self.assertIsNone(issues.async_get_issue(DOMAIN, "mapping_mapping_a"))
            self.assertIsNotNone(issues.async_get_issue(DOMAIN, "mapping_mapping_b"))
            self.registry.async_update_entity(ecobee_b.entity_id, disabled_by=None)
            await self.hass.async_block_till_done()
            self.assertIsNone(issues.async_get_issue(DOMAIN, "mapping_mapping_b"))
        finally:
            self.assertTrue(await self.hass.config_entries.async_unload(entry.entry_id))

    async def test_mapping_error_preserves_add_another_choice(self) -> None:
        result = await self.hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        submitted = {
            CONF_NAME: "   ",
            CONF_HOMEKIT_ENTITY: self.homekit.entity_id,
            CONF_ECOBEE_ENTITY: self.ecobee.entity_id,
            CONF_ADD_ANOTHER: True,
        }
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"], submitted
        )
        self.assertIs(FlowResultType.FORM, result["type"])
        self.assertEqual("invalid_name", result["errors"]["base"])
        fields = {field.schema: field for field in result["data_schema"].schema}
        self.assertTrue(fields[CONF_ADD_ANOTHER].default())

        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"],
            submitted
            | {
                CONF_NAME: "Zone A",
                CONF_ADD_ANOTHER: fields[CONF_ADD_ANOTHER].default(),
            },
        )
        self.assertIs(FlowResultType.FORM, result["type"])
        self.assertEqual("mapping", result["step_id"])
        self.hass.config_entries.flow.async_abort(result["flow_id"])

    async def test_edit_errors_keep_cleared_optional_sources_empty(self) -> None:
        co2 = self._ecobee_sensor(
            "clear_co2", SensorDeviceClass.CO2, UnitOfRatio.PARTS_PER_MILLION
        )
        voc = self._ecobee_sensor(
            "clear_voc",
            SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS,
            UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
        )
        mapping = replace(
            self.mapping,
            homekit_temperature_entity=self.homekit_temperature.id,
            ecobee_notify_entity=self.ecobee_notify.id,
            ecobee_co2_entity=co2.id,
            ecobee_voc_entity=voc.id,
        )
        future_mapping = {"opaque": ["preserve"]}
        original_data = {
            CONF_MAPPINGS: [mapping.as_dict() | {"future_mapping": future_mapping}],
            "future_entry": {"opaque": ["preserve"]},
        }
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=DOMAIN,
            data=original_data,
            version=1,
            minor_version=3,
        )
        entry.add_to_hass(self.hass)
        for name, confirm, error in (
            ("   ", True, "invalid_name"),
            ("Zone A", False, "confirmation_required"),
        ):
            with self.subTest(error=error):
                result = await self.hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": "reconfigure", "entry_id": entry.entry_id},
                )
                result = await self.hass.config_entries.flow.async_configure(
                    result["flow_id"], {"next_step_id": "reconfigure_edit"}
                )
                result = await self.hass.config_entries.flow.async_configure(
                    result["flow_id"], {CONF_MAPPING_ID: mapping.mapping_id}
                )
                # The frontend omits optional fields that the user clears.
                submitted = {
                    CONF_NAME: name,
                    CONF_HOMEKIT_ENTITY: self.homekit.entity_id,
                    CONF_ECOBEE_ENTITY: self.ecobee.entity_id,
                    CONF_CONFIRM_CHANGE: confirm,
                }
                result = await self.hass.config_entries.flow.async_configure(
                    result["flow_id"], submitted
                )
                self.assertIs(FlowResultType.FORM, result["type"])
                self.assertEqual(error, result["errors"]["base"])
                fields = {field.schema: field for field in result["data_schema"].schema}
                for key in OPTIONAL_SOURCE_KEYS:
                    self.assertIsNone(fields[key].description["suggested_value"])
                self.assertEqual(original_data, dict(entry.data))
                if error == "invalid_name":
                    self.hass.config_entries.flow.async_abort(result["flow_id"])

        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"], submitted | {CONF_CONFIRM_CHANGE: True}
        )
        self.assertIs(FlowResultType.MENU, result["type"])
        with patch.object(
            self.hass.config_entries, "async_schedule_reload"
        ) as schedule_reload:
            result = await self.hass.config_entries.flow.async_configure(
                result["flow_id"], {"next_step_id": "reconfigure_finish"}
            )
        self.assertEqual("reconfigure_successful", result["reason"])
        schedule_reload.assert_called_once_with(entry.entry_id)
        saved = entry.data[CONF_MAPPINGS][0]
        self.assertEqual(mapping.mapping_id, saved[CONF_MAPPING_ID])
        self.assertEqual(future_mapping, saved["future_mapping"])
        self.assertEqual(original_data["future_entry"], entry.data["future_entry"])
        self.assertTrue(all(key not in saved for key in OPTIONAL_SOURCE_KEYS))

    async def test_options_error_preserves_both_submitted_timings(self) -> None:
        original_options = {
            CONF_ECOBEE_STALE_SECONDS: 1800,
            CONF_CONFIRMATION_SECONDS: 1800,
            "future_option": {"opaque": ["preserve"]},
        }
        original_data = {CONF_MAPPINGS: [self.mapping.as_dict()]}
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=DOMAIN,
            data=original_data,
            options=original_options,
            version=1,
            minor_version=3,
        )
        entry.add_to_hass(self.hass)
        result = await self.hass.config_entries.options.async_init(entry.entry_id)
        submitted = {
            CONF_ECOBEE_STALE_SECONDS: 1210,
            CONF_CONFIRMATION_SECONDS: 720,
        }
        result = await self.hass.config_entries.options.async_configure(
            result["flow_id"], submitted
        )
        self.assertIs(FlowResultType.FORM, result["type"])
        self.assertEqual("invalid_timing", result["errors"]["base"])
        displayed = result["data_schema"]({})
        self.assertEqual(submitted, displayed)
        self.assertEqual(original_options, entry.options)

        result = await self.hass.config_entries.options.async_configure(
            result["flow_id"], displayed | {CONF_ECOBEE_STALE_SECONDS: 1200}
        )
        self.assertIs(FlowResultType.CREATE_ENTRY, result["type"])
        self.assertEqual(
            original_options
            | {CONF_ECOBEE_STALE_SECONDS: 1200, CONF_CONFIRMATION_SECONDS: 720},
            entry.options,
        )
        self.assertEqual(original_data, dict(entry.data))

    async def test_last_mapping_keeps_staged_changes_available_to_save(self) -> None:
        homekit_b = self._source(
            "homekit_controller",
            "hk_staged_b",
            device=True,
            physical_identity="thermostat_staged_b",
        )
        ecobee_b = self._source(
            "ecobee",
            "ec_staged_b",
            device=True,
            physical_identity="thermostat_staged_b",
        )
        mapping_b = MappingConfig("mapping_b", "Zone B", homekit_b.id, ecobee_b.id)
        original_data = {
            CONF_MAPPINGS: [self.mapping.as_dict(), mapping_b.as_dict()],
            "future_entry": {"opaque": ["preserve"]},
        }
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=DOMAIN,
            data=original_data,
            version=1,
            minor_version=3,
        )
        entry.add_to_hass(self.hass)
        result = await self.hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reconfigure", "entry_id": entry.entry_id},
        )
        self.assertIn("reconfigure_remove", result["menu_options"])
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_edit"}
        )
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_MAPPING_ID: self.mapping.mapping_id}
        )
        form_values = {
            key: value
            for key, value in _mapping_form_defaults(
                self.hass, self.mapping.as_dict()
            ).items()
            if value is not None
        }
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"],
            form_values | {CONF_NAME: "Renamed Zone A"},
        )
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "reconfigure_remove"}
        )
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_MAPPING_ID: mapping_b.mapping_id}
        )
        result = await self.hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CONFIRM_CHANGE: True}
        )
        self.assertIs(FlowResultType.MENU, result["type"])
        self.assertNotIn("reconfigure_remove", result["menu_options"])
        self.assertIn("reconfigure_finish", result["menu_options"])
        self.assertEqual(original_data, dict(entry.data))

        with self.assertRaises(InvalidData):
            await self.hass.config_entries.flow.async_configure(
                result["flow_id"], {"next_step_id": "reconfigure_remove"}
            )
        # Exercise the defensive step directly on the native in-progress flow.
        flow = self.hass.config_entries.flow._progress[result["flow_id"]]
        assert isinstance(flow, EcobeeUnifiedConfigFlow)
        guarded = await flow.async_step_reconfigure_remove()
        self.assertIs(FlowResultType.MENU, guarded["type"])
        self.assertNotIn("reconfigure_remove", guarded["menu_options"])
        self.assertEqual(original_data, dict(entry.data))

        with patch.object(
            self.hass.config_entries, "async_schedule_reload"
        ) as schedule_reload:
            result = await self.hass.config_entries.flow.async_configure(
                result["flow_id"], {"next_step_id": "reconfigure_finish"}
            )
        self.assertEqual("reconfigure_successful", result["reason"])
        schedule_reload.assert_called_once_with(entry.entry_id)
        self.assertEqual(
            original_data
            | {CONF_MAPPINGS: [self.mapping.as_dict() | {CONF_NAME: "Renamed Zone A"}]},
            dict(entry.data),
        )

    async def test_live_temperature_metadata_cannot_be_masked_by_registry(
        self,
    ) -> None:
        inputs = _mapping_form_defaults(self.hass, self.mapping.as_dict())
        inputs[CONF_HOMEKIT_TEMPERATURE_ENTITY] = self.homekit_temperature.entity_id
        for attributes in (
            {"device_class": "humidity", "unit_of_measurement": "%"},
            {"device_class": "temperature", "unit_of_measurement": "widgets"},
            {"device_class": None, "unit_of_measurement": "°C"},
            {"device_class": "temperature", "unit_of_measurement": None},
        ):
            with self.subTest(attributes=attributes):
                self.hass.states.async_set(
                    self.homekit_temperature.entity_id, "20.04", attributes
                )
                with self.assertRaisesRegex(
                    vol.Invalid, "invalid_homekit_temperature_source"
                ):
                    _mapping_from_input(self.hass, inputs)

    async def test_valid_live_temperature_unit_and_registry_fallback_are_supported(
        self,
    ) -> None:
        inputs = _mapping_form_defaults(self.hass, self.mapping.as_dict())
        inputs[CONF_HOMEKIT_TEMPERATURE_ENTITY] = self.homekit_temperature.entity_id
        for state, attributes in (
            ("68.072", {"device_class": "temperature", "unit_of_measurement": "°F"}),
            ("293.19", {"device_class": "temperature", "unit_of_measurement": "K"}),
            ("20.04", {}),
        ):
            with self.subTest(attributes=attributes):
                self.hass.states.async_set(
                    self.homekit_temperature.entity_id, state, attributes
                )
                configured = _mapping_from_input(self.hass, inputs)
                self.assertEqual(
                    self.homekit_temperature.id,
                    configured[CONF_HOMEKIT_TEMPERATURE_ENTITY],
                )
        self.registry.async_update_entity(
            self.homekit_temperature.entity_id,
            original_device_class=SensorDeviceClass.HUMIDITY,
            unit_of_measurement="widgets",
        )
        self.hass.states.async_set(
            self.homekit_temperature.entity_id,
            "20.04",
            {"device_class": "temperature", "unit_of_measurement": "°C"},
        )
        configured = _mapping_from_input(self.hass, inputs, mapping_id="live_contract")
        self.assertEqual(
            self.homekit_temperature.id,
            configured[CONF_HOMEKIT_TEMPERATURE_ENTITY],
        )
        mapping = MappingConfig.from_dict(configured)
        manager = MappingManager(self.hass, "live_contract_entry", (mapping,), {})
        await manager.async_start()
        try:
            snapshot = manager.snapshot(mapping.mapping_id)
            self.assertEqual(20.04, snapshot.current_temperature)
            self.assertEqual(
                "homekit_temperature", snapshot.provenance["current_temperature"]
            )
            self.assertFalse(snapshot.degradation)
            self.assertIsNone(
                ir.async_get(self.hass).async_get_issue(DOMAIN, "mapping_live_contract")
            )
        finally:
            await manager.async_stop()

    async def test_invalid_live_contract_degrades_repairs_and_recovers_consistently(
        self,
    ) -> None:
        inputs = _mapping_form_defaults(self.hass, self.mapping.as_dict())
        inputs[CONF_HOMEKIT_TEMPERATURE_ENTITY] = self.homekit_temperature.entity_id
        mapping = MappingConfig.from_dict(
            _mapping_from_input(self.hass, inputs, mapping_id="contract_recovery")
        )
        manager = MappingManager(self.hass, "contract_recovery_entry", (mapping,), {})
        valid_attributes = {"device_class": "temperature", "unit_of_measurement": "°C"}
        await manager.async_start()
        try:
            for state, attributes, health, reason in (
                *(
                    ("20.04", attributes, SourceHealth.UNAVAILABLE, "unavailable")
                    for attributes in (
                        {"device_class": "humidity", "unit_of_measurement": "%"},
                        {
                            "device_class": "temperature",
                            "unit_of_measurement": "widgets",
                        },
                        {"device_class": None, "unit_of_measurement": "°C"},
                        {"device_class": "", "unit_of_measurement": "°C"},
                        {"device_class": "temperature", "unit_of_measurement": None},
                        {"device_class": "temperature", "unit_of_measurement": ""},
                    )
                ),
                ("NaN", valid_attributes, SourceHealth.HEALTHY, "invalid"),
            ):
                with self.subTest(state=state, attributes=attributes):
                    self.hass.states.async_set(
                        self.homekit_temperature.entity_id, state, attributes
                    )
                    await self.hass.async_block_till_done()
                    await asyncio.sleep(HOMEKIT_PAIR_SETTLE_SECONDS + 0.01)
                    with self.assertRaisesRegex(
                        vol.Invalid, "invalid_homekit_temperature_source"
                    ):
                        _mapping_from_input(self.hass, inputs)
                    snapshot = manager.snapshot(mapping.mapping_id)
                    self.assertEqual(20, snapshot.current_temperature)
                    self.assertEqual(
                        "homekit", snapshot.provenance["current_temperature"]
                    )
                    self.assertIs(health, snapshot.source_health["homekit_temperature"])
                    self.assertIn(f"homekit_temperature_{reason}", snapshot.degradation)
                    issue = ir.async_get(self.hass).async_get_issue(
                        DOMAIN, "mapping_contract_recovery"
                    )
                    assert issue is not None
                    self.assertEqual(
                        {"mapping": mapping.name, "source": "HomeKit temperature"},
                        issue.translation_placeholders,
                    )

                    self.hass.states.async_set(
                        self.homekit_temperature.entity_id,
                        "20.04",
                        valid_attributes,
                    )
                    await self.hass.async_block_till_done()
                    await asyncio.sleep(HOMEKIT_PAIR_SETTLE_SECONDS + 0.01)
                    _mapping_from_input(self.hass, inputs)
                    recovered = manager.snapshot(mapping.mapping_id)
                    self.assertEqual(20.04, recovered.current_temperature)
                    self.assertEqual(
                        "homekit_temperature",
                        recovered.provenance["current_temperature"],
                    )
                    self.assertFalse(recovered.degradation)
                    self.assertIsNone(
                        ir.async_get(self.hass).async_get_issue(
                            DOMAIN, "mapping_contract_recovery"
                        )
                    )
        finally:
            await manager.async_stop()

    async def test_unreadable_temperature_state_retains_valid_mapping_contract(
        self,
    ) -> None:
        inputs = _mapping_form_defaults(self.hass, self.mapping.as_dict())
        inputs[CONF_HOMEKIT_TEMPERATURE_ENTITY] = self.homekit_temperature.entity_id
        mapping = MappingConfig.from_dict(
            _mapping_from_input(self.hass, inputs, mapping_id="contract_unreadable")
        )
        manager = MappingManager(self.hass, "contract_unreadable_entry", (mapping,), {})
        await manager.async_start()
        try:
            for state in ("unknown", "unavailable", None):
                with self.subTest(state=state):
                    if state is None:
                        self.hass.states.async_remove(
                            self.homekit_temperature.entity_id
                        )
                    else:
                        self.hass.states.async_set(
                            self.homekit_temperature.entity_id,
                            state,
                            {
                                "device_class": "temperature",
                                "unit_of_measurement": "°C",
                            },
                        )
                    await self.hass.async_block_till_done()
                    configured = _mapping_from_input(self.hass, inputs)
                    self.assertEqual(
                        self.homekit_temperature.id,
                        configured[CONF_HOMEKIT_TEMPERATURE_ENTITY],
                    )
                    snapshot = manager.snapshot(mapping.mapping_id)
                    self.assertEqual(
                        "homekit", snapshot.provenance["current_temperature"]
                    )
                    self.assertIsNone(
                        ir.async_get(self.hass).async_get_issue(
                            DOMAIN, "mapping_contract_unreadable"
                        )
                    )
        finally:
            await manager.async_stop()

    async def test_missing_homekit_parent_preserves_saved_sources_only(self) -> None:
        await self._assert_missing_parent_boundary(
            self.homekit,
            (
                (CONF_HOMEKIT_PRESET_ENTITY, self.homekit_preset),
                (CONF_HOMEKIT_CLEAR_HOLD_ENTITY, self.homekit_clear_hold),
                (CONF_HOMEKIT_TEMPERATURE_ENTITY, self.homekit_temperature),
            ),
        )

    async def test_missing_ecobee_parent_preserves_saved_sources_only(self) -> None:
        co2 = self._ecobee_sensor(
            "configuration_co2", SensorDeviceClass.CO2, UnitOfRatio.PARTS_PER_MILLION
        )
        voc = self._ecobee_sensor(
            "configuration_voc",
            SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS,
            UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
        )
        await self._assert_missing_parent_boundary(
            self.ecobee,
            (
                (CONF_ECOBEE_AQI_ENTITY, self.ecobee_aqi),
                (CONF_ECOBEE_CO2_ENTITY, co2),
                (CONF_ECOBEE_VOC_ENTITY, voc),
                (CONF_ECOBEE_NOTIFY_ENTITY, self.ecobee_notify),
            ),
        )

    def _ecobee_sensor(
        self, unique_id: str, device_class: SensorDeviceClass, unit: str
    ) -> er.RegistryEntry:
        config_entry = self.hass.config_entries.async_get_entry(
            self.ecobee.config_entry_id
        )
        assert config_entry is not None
        entry = self.registry.async_get_or_create(
            "sensor",
            "ecobee",
            unique_id,
            config_entry=config_entry,
            device_id=self.ecobee.device_id,
            original_device_class=device_class,
            unit_of_measurement=unit,
        )
        self.hass.states.async_set(
            entry.entity_id,
            "125",
            {"device_class": device_class, "unit_of_measurement": unit},
        )
        return entry

    async def _assert_missing_parent_boundary(
        self,
        parent: er.RegistryEntry,
        sources: tuple[tuple[str, er.RegistryEntry], ...],
    ) -> None:
        self.registry.async_remove(parent.entity_id)
        await self.hass.async_block_till_done()
        for key, original in sources:
            with self.subTest(role=key):
                mapping = replace(self.mapping, **{key: original.id})
                defaults = _mapping_form_defaults(self.hass, mapping.as_dict())

                def configure(
                    values: dict[str, Any], preserved: MappingConfig = mapping
                ) -> dict[str, str]:
                    return _mapping_from_input(
                        self.hass,
                        values,
                        mapping_id=preserved.mapping_id,
                        preserved=preserved,
                    )

                self.assertEqual(original.id, configure(defaults)[key])
                foreign = self._source(
                    original.platform,
                    f"foreign_{key}",
                    domain=original.domain,
                    device=True,
                )
                original_state = self.hass.states.get(original.entity_id)
                assert original_state is not None
                self.hass.states.async_set(
                    foreign.entity_id, original_state.state, original_state.attributes
                )
                with self.assertRaisesRegex(
                    vol.Invalid, f"invalid_{original.platform}_source"
                ):
                    configure({**defaults, key: foreign.entity_id})
                self.assertNotIn(key, configure({**defaults, key: None}))
                self.registry.async_remove(original.entity_id)
                missing_defaults = _mapping_form_defaults(self.hass, mapping.as_dict())
                self.assertEqual(original.id, configure(missing_defaults)[key])
