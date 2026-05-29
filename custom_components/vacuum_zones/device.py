"""Общее устройство «Квартира» для группировки комнатных сущностей."""

from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, DEFAULT_APARTMENT_NAME, APARTMENT_ZONE_ID


def apartment_device_identifier(base_entity_id: str) -> str:
    return f"{base_entity_id}_{APARTMENT_ZONE_ID}"


def apartment_device_info(base_entity_id: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, apartment_device_identifier(base_entity_id))},
        name=f"Vacuum Zones - {DEFAULT_APARTMENT_NAME}",
        manufacturer="VacuumZones",
        model="Apartment Controller",
    )
