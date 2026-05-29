"""Parse zone configuration from config entry / subentry data."""

from __future__ import annotations

import json
from typing import Any

import yaml
from homeassistant.const import CONF_SEQUENCE

from .const import CONF_ZONES


def prepare_zone_config(zone_data: dict[str, Any]) -> dict[str, Any]:
    """Return a mutable zone config with parsed JSON/YAML fields."""
    config = dict(zone_data)

    if isinstance(config.get("zone"), str):
        try:
            config["zone"] = json.loads(config["zone"])
        except (json.JSONDecodeError, TypeError):
            pass

    if isinstance(config.get("goto"), str):
        try:
            config["goto"] = json.loads(config["goto"])
        except (json.JSONDecodeError, TypeError):
            pass

    if isinstance(config.get(CONF_SEQUENCE), str):
        try:
            config[CONF_SEQUENCE] = yaml.safe_load(config[CONF_SEQUENCE])
        except (yaml.YAMLError, TypeError):
            pass

    return config
