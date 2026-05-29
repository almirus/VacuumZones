"""Runtime capability flags (no config_flow import)."""

SUPPORT_SUBENTRIES = False

try:
    from homeassistant.config_entries import ConfigSubentryFlow  # noqa: F401

    SUPPORT_SUBENTRIES = True
except ImportError:
    pass
