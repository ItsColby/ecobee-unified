"""Coherent native weather projection with daily forecasts from its selected alias."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from copy import deepcopy
from math import isfinite
from typing import Any, Literal, cast, override

from homeassistant.components.weather import (
    Forecast,
    WeatherEntity,
)
from homeassistant.components.weather.const import DATA_COMPONENT, WeatherEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from .datapoints import DatapointConfig, DatapointManager
from .runtime import EcobeeUnifiedConfigEntry
from .weather_source import WeatherSnapshot, weather_snapshot

_NATIVE_FIELDS = {
    "temperature": ("native_temperature", "temperature_unit"),
    "templow": ("native_templow", "temperature_unit"),
    "apparent_temperature": ("native_apparent_temperature", "temperature_unit"),
    "dew_point": ("native_dew_point", "temperature_unit"),
    "pressure": ("native_pressure", "pressure_unit"),
    "wind_speed": ("native_wind_speed", "wind_speed_unit"),
    "wind_gust_speed": ("native_wind_gust_speed", "wind_speed_unit"),
    "precipitation": ("native_precipitation", "precipitation_unit"),
}
_NUMERIC_FIELDS = frozenset(
    {
        *_NATIVE_FIELDS,
        "humidity",
        "wind_bearing",
        "precipitation_probability",
        "cloud_coverage",
        "uv_index",
    }
)
_FORECAST_FIELDS = _NUMERIC_FIELDS | {"datetime", "condition", "is_daytime"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EcobeeUnifiedConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Expose only explicitly configured complete weather datapoints."""
    if (manager := entry.runtime_data.datapoints) is not None:
        async_add_entities(
            EcobeeUnifiedWeather(manager, config)
            for config in manager.configs
            if config.kind == "weather"
        )


class EcobeeUnifiedWeather(WeatherEntity):
    """Read native current/forecast surfaces without a provider API or polling loop."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: DatapointManager, config: DatapointConfig) -> None:
        self._manager = manager
        self._config = config
        self._attr_unique_id = manager.unique_id(config.datapoint_id)
        self._attr_name = config.name
        self._native_weather: WeatherEntity | None = None
        self._forecast_unsub: Callable[[], None] | None = None
        self._daily_listeners = False
        self._unloaded = False
        self._refresh_requested = False
        self._refresh_task: asyncio.Task[None] | None = None
        self._generation: tuple[object, ...] | None = None
        self._generation_revision = 0
        self._sync_snapshot()

    def _selection(self) -> tuple[str, WeatherSnapshot] | None:
        point = self._manager.snapshot(self._config.datapoint_id)
        if (
            point.available
            and point.selected_source
            and isinstance(point.value, WeatherSnapshot)
        ):
            return point.selected_source, point.value
        return None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        selection = self._selection()
        source, report = selection if selection else (None, None)
        return {
            "source": source,
            "provider_reported_at": report.provider_reported_at if report else None,
            "ha_reported_at": report.ha_reported_at if report else None,
            "timing_quality": "provider_forecast_timestamp",
            "feed_relationship": "same_ecobee_feed_aliases",
        }

    def _sync_snapshot(self) -> None:
        selection = self._selection()
        report = selection[1] if selection else None
        generation = _generation(*selection) if selection else None
        if generation != self._generation:
            self._generation = generation
            self._generation_revision += 1
        self._attr_available = report is not None
        for field, native in (
            ("condition", "condition"),
            ("temperature", "native_temperature"),
            ("humidity", "humidity"),
            ("pressure", "native_pressure"),
            ("wind_bearing", "wind_bearing"),
            ("wind_speed", "native_wind_speed"),
            ("visibility", "native_visibility"),
            ("temperature_unit", "native_temperature_unit"),
            ("pressure_unit", "native_pressure_unit"),
            ("wind_speed_unit", "native_wind_speed_unit"),
            ("visibility_unit", "native_visibility_unit"),
            ("precipitation_unit", "native_precipitation_unit"),
        ):
            setattr(self, f"_attr_{native}", getattr(report, field) if report else None)
        self._attr_supported_features = (
            WeatherEntityFeature.FORECAST_DAILY
            if report
            and report.supported_features & WeatherEntityFeature.FORECAST_DAILY
            else WeatherEntityFeature(0)
        )

    @override
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unloaded = False
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._manager.signal(self._config.datapoint_id),
                self._manager_updated,
            )
        )

    @callback
    def _manager_updated(self) -> None:
        if self._unloaded:
            return
        previous_revision = self._generation_revision
        self._sync_snapshot()
        self.async_write_ha_state()
        self._bind_forecast_source()
        if previous_revision != self._generation_revision:
            self._schedule_forecast_update()

    @callback
    @override
    def _async_subscription_started(
        self, forecast_type: Literal["daily", "hourly", "twice_daily"]
    ) -> None:
        if forecast_type == "daily":
            self._daily_listeners = True
            self._bind_forecast_source()

    @callback
    @override
    def _async_subscription_ended(
        self, forecast_type: Literal["daily", "hourly", "twice_daily"]
    ) -> None:
        if forecast_type == "daily":
            self._daily_listeners = False
            self._unbind_forecast_source()

    @callback
    def _unbind_forecast_source(self) -> None:
        if self._forecast_unsub is not None:
            self._forecast_unsub()
        self._forecast_unsub = None
        self._native_weather = None

    @callback
    def _bind_forecast_source(self) -> None:
        if not self._daily_listeners or self._unloaded:
            return
        selection = self._selection()
        component = self.hass.data.get(DATA_COMPONENT)
        native = (
            component.get_entity(selection[0])
            if selection and isinstance(component, EntityComponent)
            else None
        )
        if native is self._native_weather:
            return
        self._unbind_forecast_source()
        if (
            isinstance(native, WeatherEntity)
            and native is not self
            and (native.supported_features or 0) & WeatherEntityFeature.FORECAST_DAILY
        ):
            self._native_weather = native
            self._forecast_unsub = native.async_subscribe_forecast(
                "daily", self._native_forecast_updated
            )

    @callback
    def _native_forecast_updated(self, forecast: list[Any] | None) -> None:
        self._schedule_forecast_update()

    @callback
    def _schedule_forecast_update(self) -> None:
        if not self._daily_listeners or self._unloaded:
            return
        self._refresh_requested = True
        if self._refresh_task is None:
            self._refresh_task = self.hass.async_create_task(
                self._publish_forecast(), eager_start=False
            )

    async def _publish_forecast(self) -> None:
        try:
            while (
                self._refresh_requested and self._daily_listeners and not self._unloaded
            ):
                self._refresh_requested = False
                await self.async_update_listeners(("daily",))
        finally:
            self._refresh_task = None

    @override
    async def async_will_remove_from_hass(self) -> None:
        self._unloaded = True
        self._daily_listeners = False
        self._unbind_forecast_source()
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            await asyncio.gather(self._refresh_task, return_exceptions=True)
        await super().async_will_remove_from_hass()

    @override
    async def async_forecast_daily(self) -> list[Forecast] | None:
        selection = self._selection()
        if self._unloaded or selection is None:
            return None
        source, report = selection
        if not report.supported_features & WeatherEntityFeature.FORECAST_DAILY:
            return None
        generation = _generation(source, report)
        revision = self._generation_revision
        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": source, "type": "daily"},
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError:
            return None
        current = self._selection()
        if (
            self._unloaded
            or revision != self._generation_revision
            or current is None
            or _generation(*current) != generation
        ):
            return None
        entry = er.async_get(self.hass).async_get(source)
        if entry is None:
            return None
        try:
            live = weather_snapshot(
                entry,
                self.hass.states.get(source),
                expected_station=self._config.weather_station or "",
                expected_config_entry_id=self._config.weather_config_entry_id or "",
            )
            if _generation(source, live) != generation:
                return None
            source_response = (
                response.get(source) if isinstance(response, dict) else None
            )
            forecast = (
                source_response.get("forecast")
                if isinstance(source_response, dict)
                else None
            )
            return _native_forecast(forecast, report)
        except KeyError, TypeError, ValueError:
            return None


def _generation(source: str, report: WeatherSnapshot) -> tuple[object, ...]:
    return (
        source,
        report.station,
        report.provider_reported_at,
        report.temperature_unit,
        report.pressure_unit,
        report.wind_speed_unit,
        report.visibility_unit,
        report.precipitation_unit,
        report.supported_features,
    )


def _native_forecast(value: Any, report: WeatherSnapshot) -> list[Forecast]:
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("invalid_weather_forecast")
    result: list[Forecast] = []
    for row in value:
        if not isinstance(row, dict) or not isinstance(row.get("datetime"), str):
            raise ValueError("invalid_weather_forecast")  # noqa: TRY004 -- Bounded source-data failure.
        timestamp = dt_util.parse_datetime(row["datetime"])
        if timestamp is None or timestamp.tzinfo is None:
            raise ValueError("invalid_weather_forecast")
        accepted: dict[str, Any] = {}
        for key, raw in row.items():
            if key not in _FORECAST_FIELDS:
                continue
            if raw is not None:
                _validate_forecast_value(key, raw)
            if key in _NATIVE_FIELDS:
                target, unit_field = _NATIVE_FIELDS[key]
                if raw is not None and getattr(report, unit_field) is None:
                    raise ValueError("invalid_weather_forecast")
                if raw is not None and unit_field == "temperature_unit":
                    kelvin = TemperatureConverter.convert(
                        raw, cast(str, report.temperature_unit), "K"
                    )
                    if not isfinite(kelvin) or kelvin < 0:
                        raise ValueError("invalid_weather_forecast")
                accepted[target] = raw
            else:
                accepted[key] = deepcopy(raw)
        result.append(cast(Forecast, accepted))
    return result


def _validate_forecast_value(key: str, value: Any) -> None:
    if key in _NUMERIC_FIELDS:
        try:
            finite = isfinite(value)
        except TypeError, ValueError, OverflowError:
            finite = False
        if isinstance(value, bool) or not isinstance(value, int | float) or not finite:
            raise ValueError("invalid_weather_forecast")
        if (
            key in {"humidity", "precipitation_probability", "cloud_coverage"}
            and not 0 <= value <= 100
        ):
            raise ValueError("invalid_weather_forecast")
        if key == "wind_bearing" and not 0 <= value <= 360:
            raise ValueError("invalid_weather_forecast")
        if (
            key in {"wind_speed", "wind_gust_speed", "precipitation", "uv_index"}
            and value < 0
        ):
            raise ValueError("invalid_weather_forecast")
        if key == "pressure" and value <= 0:
            raise ValueError("invalid_weather_forecast")
    elif key == "is_daytime":
        if not isinstance(value, bool):
            raise ValueError("invalid_weather_forecast")
    elif not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("invalid_weather_forecast")
