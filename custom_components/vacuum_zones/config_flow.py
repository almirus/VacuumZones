"""Config flow router: subentries on HA 2025.2+, legacy flow otherwise."""

from __future__ import annotations

from .compat import SUPPORT_SUBENTRIES

if SUPPORT_SUBENTRIES:
    from .config_flow_subentries import VacuumZonesConfigFlow
else:
    from .config_flow_legacy import VacuumZonesConfigFlow

__all__ = ["VacuumZonesConfigFlow", "SUPPORT_SUBENTRIES"]
