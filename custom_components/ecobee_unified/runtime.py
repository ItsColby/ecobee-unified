"""Typed runtime for Ecobee Unified."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry

from .manager import MappingManager


@dataclass(slots=True)
class EcobeeUnifiedRuntime:
    """Runtime objects owned by one config entry."""

    manager: MappingManager


type EcobeeUnifiedConfigEntry = ConfigEntry[EcobeeUnifiedRuntime]
