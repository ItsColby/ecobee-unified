"""Typed runtime for Ecobee Unified."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry

from .datapoints import DatapointManager
from .manager import MappingManager


@dataclass(slots=True)
class EcobeeUnifiedRuntime:
    """Runtime objects owned by one config entry."""

    manager: MappingManager
    datapoints: DatapointManager | None = None


type EcobeeUnifiedConfigEntry = ConfigEntry[EcobeeUnifiedRuntime]
