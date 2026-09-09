"""Configuration must validate live semantics and prove new source associations."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import voluptuous as vol
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfDensity, UnitOfRatio
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.ecobee_unified.config_flow import (
    _mapping_form_defaults,
    _mapping_from_input,
)
from custom_components.ecobee_unified.const import (
    CONF_ECOBEE_AQI_ENTITY,
    CONF_ECOBEE_CO2_ENTITY,
    CONF_ECOBEE_NOTIFY_ENTITY,
    CONF_ECOBEE_VOC_ENTITY,
    CONF_HOMEKIT_CLEAR_HOLD_ENTITY,
    CONF_HOMEKIT_PRESET_ENTITY,
    CONF_HOMEKIT_TEMPERATURE_ENTITY,
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
                        {"source": "HomeKit temperature"},
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
