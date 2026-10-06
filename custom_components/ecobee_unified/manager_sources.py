"""Source reads, source health, and Repair evaluation for mapped entities."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import (
    HomeAssistant,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import (
    DOMAIN,
    SERVICE_CREATE_VACATION,
    SERVICE_DELETE_VACATION,
    SERVICE_SET_OCCUPANCY_MODES,
    SERVICE_SET_SENSORS_USED_IN_CLIMATE,
)
from .models import (
    MappingConfig,
    RawSource,
    SourceHealth,
    finite_number,
    finite_temperature,
)
from .source_contracts import (
    AIR_QUALITY_SENSOR_CONTRACTS,
    PhysicalIdentityStatus,
    homekit_action_contract_valid,
    physical_identity_status,
    sensor_contract_valid,
    temperature_source_unit,
)

UNCONFIRMABLE_VENDOR_ACTIONS = frozenset(
    {
        SERVICE_CREATE_VACATION,
        SERVICE_DELETE_VACATION,
        SERVICE_SET_OCCUPANCY_MODES,
        SERVICE_SET_SENSORS_USED_IN_CLIMATE,
    }
)


def _state_age_seconds(last_reported: datetime, now: datetime) -> int:
    return max(0, int((now - last_reported).total_seconds()))


class SourceReader:
    """Read mapped sources and derive their health without owning subscriptions."""

    hass: HomeAssistant
    _mapping_by_id: dict[str, MappingConfig]

    def _optional_raw_source(
        self,
        entity_reference: str | None,
        stale_seconds: int | None,
        *,
        now: datetime,
        report_times: Mapping[str, datetime] | None,
        required_device_id: str | None,
    ) -> RawSource | None:
        return (
            self._raw_source(
                entity_reference,
                stale_seconds,
                now=now,
                report_times=report_times,
                required_device_id=required_device_id,
                require_matching_device=True,
            )
            if entity_reference
            else None
        )

    def _climate_raw_source(
        self,
        entity_reference: str,
        stale_seconds: int | None,
        *,
        require_device: bool = False,
        now: datetime,
        report_times: Mapping[str, datetime] | None,
    ) -> RawSource:
        """Read a climate source using Core's configured-unit state contract."""

        source = self._raw_source(
            entity_reference,
            stale_seconds,
            require_device=require_device,
            now=now,
            report_times=report_times,
        )
        return RawSource(
            source.state,
            {
                **source.attributes,
                ATTR_UNIT_OF_MEASUREMENT: self.hass.config.units.temperature_unit,
            },
            age_seconds=source.age_seconds,
            health=source.health,
            reported_at=source.reported_at,
        )

    def _air_quality_raw_source(
        self,
        entity_reference: str | None,
        contract_name: str,
        stale_seconds: int,
        *,
        now: datetime,
        report_times: Mapping[str, datetime] | None,
        required_device_id: str | None,
        physical_identity_proven: bool,
    ) -> RawSource | None:
        source = self._optional_raw_source(
            entity_reference,
            stale_seconds,
            now=now,
            report_times=report_times,
            required_device_id=required_device_id,
        )
        if source is None or not source.usable:
            return source
        if (
            not physical_identity_proven
            or entity_reference is None
            or not sensor_contract_valid(
                self.hass,
                entity_reference,
                AIR_QUALITY_SENSOR_CONTRACTS[contract_name],
            )
        ):
            return self._invalid_source(source)
        return source

    def _temperature_raw_source(
        self,
        entity_reference: str | None,
        homekit: RawSource,
        *,
        now: datetime,
        report_times: Mapping[str, datetime] | None,
        required_device_id: str | None,
    ) -> RawSource | None:
        """Read and unit-normalize an explicit same-device temperature sensor."""

        if entity_reference is None:
            return None
        source = self._raw_source(
            entity_reference,
            None,
            now=now,
            report_times=report_times,
            required_device_id=required_device_id,
            require_matching_device=True,
        )
        if not source.usable:
            return source
        value = finite_number(source.state, allow_text=True)
        if value is None:
            # Invalid measurements do not establish a transport outage.
            return RawSource(
                None,
                source.attributes,
                age_seconds=source.age_seconds,
                health=source.health,
                reported_at=source.reported_at,
            )
        source_unit = temperature_source_unit(self.hass, entity_reference)
        target_unit = homekit.attributes.get(
            ATTR_UNIT_OF_MEASUREMENT, self.hass.config.units.temperature_unit
        )
        try:
            if source_unit is None:
                raise ValueError
            target_unit_enum = UnitOfTemperature(str(target_unit))
        except TypeError, ValueError:
            return self._invalid_source(source)
        value = finite_temperature(value, source_unit)
        try:
            converted = (
                finite_temperature(
                    TemperatureConverter.convert(value, source_unit, target_unit_enum),
                    target_unit_enum,
                )
                if value is not None
                else None
            )
        except TypeError, ValueError, OverflowError:
            converted = None
        return RawSource(
            str(converted) if converted is not None else None,
            source.attributes,
            age_seconds=source.age_seconds,
            health=source.health,
            reported_at=source.reported_at,
        )

    def _raw_source(
        self,
        entity_reference: str,
        stale_seconds: int | None,
        *,
        require_device: bool = False,
        now: datetime | None = None,
        report_times: Mapping[str, datetime] | None = None,
        required_device_id: str | None = None,
        require_matching_device: bool = False,
    ) -> RawSource:
        registry = er.async_get(self.hass)
        entity_id = er.async_resolve_entity_id(registry, entity_reference)
        registry_entry = registry.async_get(entity_id) if entity_id else None
        if registry_entry is None:
            return RawSource(None, health=SourceHealth.MISSING)
        assert entity_id is not None
        if registry_entry.disabled:
            return RawSource(None, health=SourceHealth.UNAVAILABLE)
        if require_matching_device and (
            required_device_id is None or registry_entry.device_id != required_device_id
        ):
            return RawSource(None, health=SourceHealth.MISSING)
        if require_device and (
            registry_entry.device_id is None
            or dr.async_get(self.hass).async_get(registry_entry.device_id) is None
        ):
            return RawSource(None, health=SourceHealth.MISSING)
        state = self.hass.states.get(entity_id)
        if state is None:
            return RawSource(None, health=SourceHealth.UNAVAILABLE)
        observed_at = (
            report_times.get(entity_id, state.last_reported)
            if report_times is not None
            else state.last_reported
        )
        age = _state_age_seconds(observed_at, now or dt_util.utcnow())
        if state.state == STATE_UNKNOWN:
            health = SourceHealth.UNKNOWN
        elif state.state == STATE_UNAVAILABLE:
            health = SourceHealth.UNAVAILABLE
        elif stale_seconds is not None and age > stale_seconds:
            health = SourceHealth.STALE
        else:
            health = SourceHealth.HEALTHY
        return RawSource(
            state.state,
            state.attributes,
            age_seconds=age,
            health=health,
            reported_at=observed_at.isoformat(),
        )

    @staticmethod
    def _invalid_source(source: RawSource) -> RawSource:
        return RawSource(
            None,
            source.attributes,
            age_seconds=source.age_seconds,
            health=SourceHealth.UNAVAILABLE,
            reported_at=source.reported_at,
        )

    @staticmethod
    def _confirmation_reference(
        mapping: MappingConfig, operation: str | None
    ) -> str | None:
        """Return the one source allowed to confirm the pending operation."""

        if operation == "set_preset_mode":
            return mapping.homekit_preset_entity
        if operation == "set_humidity":
            return mapping.homekit_entity
        if operation in UNCONFIRMABLE_VENDOR_ACTIONS:
            return None
        return mapping.ecobee_entity if operation is not None else None

    def _source_device_id(self, entity_reference: str) -> str | None:
        registry = er.async_get(self.hass)
        entity_id = er.async_resolve_entity_id(registry, entity_reference)
        registry_entry = registry.async_get(entity_id) if entity_id else None
        if (
            registry_entry is None
            or registry_entry.device_id is None
            or dr.async_get(self.hass).async_get(registry_entry.device_id) is None
        ):
            return None
        return registry_entry.device_id

    def _writer_available(
        self,
        entity_reference: str | None,
        *,
        required_device_id: str | None,
    ) -> bool:
        if entity_reference is None or required_device_id is None:
            return False
        registry = er.async_get(self.hass)
        entity_id = er.async_resolve_entity_id(registry, entity_reference)
        registry_entry = registry.async_get(entity_id) if entity_id else None
        state = self.hass.states.get(entity_id) if entity_id else None
        return bool(
            registry_entry is not None
            and not registry_entry.disabled
            and registry_entry.device_id == required_device_id
            and state is not None
            and state.state != STATE_UNAVAILABLE
        )

    def _homekit_action_available(
        self, mapping: MappingConfig, role: Literal["preset", "clear_hold"]
    ) -> bool:
        reference = (
            mapping.homekit_preset_entity
            if role == "preset"
            else mapping.homekit_clear_hold_entity
        )
        return bool(
            reference is not None
            and self._writer_available(
                reference,
                required_device_id=self._source_device_id(mapping.homekit_entity),
            )
            and homekit_action_contract_valid(self.hass, reference, role)
        )

    def ecobee_sensor_devices_valid(
        self, mapping_id: str, device_ids: list[str]
    ) -> bool:
        """Prove selected sensor devices belong to the mapped Ecobee account."""

        mapping = self._mapping_by_id[mapping_id]
        registry = er.async_get(self.hass)
        entity_id = er.async_resolve_entity_id(registry, mapping.ecobee_entity)
        source_entry = registry.async_get(entity_id) if entity_id else None
        source_config_entry_id = source_entry.config_entry_id if source_entry else None
        if source_config_entry_id is None:
            return False
        device_registry = dr.async_get(self.hass)
        return all(
            (device := device_registry.async_get(device_id)) is not None
            and source_config_entry_id == device.config_entry_id
            and any(domain == "ecobee" for domain, _value in device.identifiers)
            for device_id in device_ids
        )

    def _refresh_mapping_issue(self, mapping: MappingConfig) -> None:
        invalid, homekit_device_id, ecobee_device_id = self._required_source_issues(
            mapping
        )
        invalid.extend(
            self._optional_source_issues(mapping, homekit_device_id, ecobee_device_id)
        )
        issue_id = f"mapping_{mapping.mapping_id}"
        if invalid:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="mapping_source_missing",
                translation_placeholders={
                    "mapping": mapping.name,
                    "source": ", ".join(invalid),
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def _required_source_issues(
        self, mapping: MappingConfig
    ) -> tuple[list[str], str | None, str | None]:
        registry = er.async_get(self.hass)
        homekit_entity_id = er.async_resolve_entity_id(registry, mapping.homekit_entity)
        homekit_entry = (
            registry.async_get(homekit_entity_id) if homekit_entity_id else None
        )
        homekit_device_id = self._source_device_id(mapping.homekit_entity)
        ecobee_device_id = self._source_device_id(mapping.ecobee_entity)
        invalid = []
        if homekit_entry is None:
            invalid.append("homekit")
        elif homekit_entry.disabled:
            invalid.append("homekit disabled")
        elif (
            homekit_entry.device_id is None
            or dr.async_get(self.hass).async_get(homekit_entry.device_id) is None
        ):
            invalid.append("homekit device")
        ecobee_entity_id = er.async_resolve_entity_id(registry, mapping.ecobee_entity)
        ecobee_entry = (
            registry.async_get(ecobee_entity_id) if ecobee_entity_id else None
        )
        if ecobee_entry is None:
            invalid.append("ecobee")
        elif ecobee_entry.disabled:
            invalid.append("ecobee disabled")
        elif ecobee_device_id is None:
            invalid.append("ecobee device")
        elif (
            homekit_entry is not None
            and homekit_device_id is not None
            and physical_identity_status(
                self.hass, mapping.homekit_entity, mapping.ecobee_entity
            )
            is not PhysicalIdentityStatus.MATCH
        ):
            invalid.append("physical device identity")
        return invalid, homekit_device_id, ecobee_device_id

    def _optional_source_issues(
        self,
        mapping: MappingConfig,
        homekit_device_id: str | None,
        ecobee_device_id: str | None,
    ) -> list[str]:
        registry = er.async_get(self.hass)
        invalid: list[str] = []
        optional_sources = (
            (
                "HomeKit preset",
                mapping.homekit_preset_entity,
                homekit_device_id,
                None,
            ),
            (
                "HomeKit clear hold",
                mapping.homekit_clear_hold_entity,
                homekit_device_id,
                None,
            ),
            (
                "HomeKit temperature",
                mapping.homekit_temperature_entity,
                homekit_device_id,
                None,
            ),
            (
                "Ecobee air quality",
                mapping.ecobee_aqi_entity,
                ecobee_device_id,
                "aqi",
            ),
            (
                "Ecobee carbon dioxide",
                mapping.ecobee_co2_entity,
                ecobee_device_id,
                "co2",
            ),
            (
                "Ecobee volatile organic compounds",
                mapping.ecobee_voc_entity,
                ecobee_device_id,
                "voc",
            ),
            (
                "Ecobee notification",
                mapping.ecobee_notify_entity,
                ecobee_device_id,
                None,
            ),
        )
        for label, reference, required_device_id, contract_name in optional_sources:
            if not reference:
                continue
            entity_id = er.async_resolve_entity_id(registry, reference)
            entry = registry.async_get(entity_id) if entity_id else None
            if entry is None:
                invalid.append(label)
            elif entry.disabled:
                invalid.append(f"{label} disabled")
            elif (
                required_device_id is not None and entry.device_id != required_device_id
            ):
                invalid.append(f"{label} association")
            elif (
                (
                    label == "HomeKit temperature"
                    and temperature_source_unit(self.hass, reference) is None
                )
                or (
                    label == "HomeKit preset"
                    and not homekit_action_contract_valid(
                        self.hass, reference, "preset"
                    )
                )
                or (
                    label == "HomeKit clear hold"
                    and not homekit_action_contract_valid(
                        self.hass, reference, "clear_hold"
                    )
                )
                or (
                    contract_name is not None
                    and not sensor_contract_valid(
                        self.hass,
                        reference,
                        AIR_QUALITY_SENSOR_CONTRACTS[contract_name],
                    )
                )
            ):
                invalid.append(label)
        return invalid
