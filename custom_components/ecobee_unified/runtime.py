"""Typed runtime for Ecobee Unified."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry

from .datapoints import DatapointManager
from .historical import HistoricalManager
from .manager import MappingManager


@dataclass(slots=True)
class EcobeeUnifiedRuntime:
    """Runtime objects owned by one config entry."""

    manager: MappingManager
    datapoints: DatapointManager | None = None
    history: HistoricalManager | None = None


type EcobeeUnifiedConfigEntry = ConfigEntry[EcobeeUnifiedRuntime]
