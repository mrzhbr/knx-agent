"""Optional ETS validation for an authoritative KNX/IP device registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.hal.platforms.knx_ip_registry import (  # type: ignore[import-not-found]
    KNXDeviceRegistry,
    KNXMapError,
    group_address_raw,
    validate_role_dpt,
)


class KNXETSValidationError(ValueError):
    """Raised when an ETS project contradicts the authoritative device map."""


@dataclass(frozen=True, slots=True)
class KNXETSValidationReport:
    """Validation findings plus DPTs supplied by ETS for missing map values."""

    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    resolved_dpts: dict[tuple[str, str], str]
    data_secure_roles: tuple[tuple[str, str], ...]

    def dpt_for(self, device_id: str, role: str) -> str | None:
        return self.resolved_dpts.get((device_id, role))

    def require_valid(self) -> None:
        if self.errors:
            raise KNXETSValidationError("; ".join(self.errors))


_WRITE_ROLES = {
    "switch",
    "brightness",
    "move",
    "stop",
    "position",
    "setpoint",
    "scene",
    "trigger",
}


def validate_registry_against_project(
    registry: KNXDeviceRegistry,
    project: Mapping[str, Any],
) -> KNXETSValidationReport:
    """Validate explicit mappings without deriving semantic devices from ETS."""
    group_addresses = _group_addresses_by_raw_address(project)
    communication_objects = project.get("communication_objects", {})
    if not isinstance(communication_objects, Mapping):
        communication_objects = {}

    errors: list[str] = []
    warnings: list[str] = []
    resolved_dpts: dict[tuple[str, str], str] = {}
    data_secure_roles: list[tuple[str, str]] = []

    for device in registry.devices:
        for role, address in device.addresses.items():
            ga = group_addresses.get(group_address_raw(address))
            label = f"{device.device_id}.{role} ({address})"
            if ga is None:
                errors.append(f"Configured group address {label} is not present in the ETS project")
                continue

            if bool(ga.get("data_secure")):
                data_secure_roles.append((device.device_id, role))

            links = ga.get("communication_object_ids", [])
            if not isinstance(links, list):
                links = []
            if not links:
                message = (
                    f"Configured group address {label} is not linked to a communication object"
                )
                if role in _WRITE_ROLES:
                    errors.append(message)
                else:
                    warnings.append(message)
            elif any(str(link) not in communication_objects for link in links):
                errors.append(
                    f"Configured group address {label} references missing communication object(s)"
                )

            configured_dpt = device.dpt_for(role)
            project_dpt = _resolve_project_dpt(ga, links, communication_objects)
            if configured_dpt and project_dpt and configured_dpt != project_dpt:
                errors.append(
                    f"DPT conflict for {label}: mapping={configured_dpt}, ETS={project_dpt}"
                )
                continue
            resolved_dpt = configured_dpt or project_dpt
            if resolved_dpt is None:
                errors.append(f"Configured group address {label} has no resolvable DPT")
                continue
            try:
                resolved_dpts[(device.device_id, role)] = validate_role_dpt(role, resolved_dpt)
            except KNXMapError as exc:
                errors.append(f"Configured group address {label}: {exc}")

    return KNXETSValidationReport(
        tuple(errors),
        tuple(warnings),
        resolved_dpts,
        tuple(data_secure_roles),
    )


def _group_addresses_by_raw_address(
    project: Mapping[str, Any],
) -> dict[int, Mapping[str, Any]]:
    raw_group_addresses = project.get("group_addresses", {})
    if not isinstance(raw_group_addresses, Mapping):
        return {}
    result: dict[int, Mapping[str, Any]] = {}
    for key, value in raw_group_addresses.items():
        if not isinstance(value, Mapping):
            continue
        raw_address = value.get("raw_address")
        if isinstance(raw_address, int) and not isinstance(raw_address, bool):
            result[raw_address] = value
            continue
        address = value.get("address", key)
        if not isinstance(address, str):
            continue
        try:
            result[group_address_raw(address)] = value
        except KNXMapError:
            continue
    return result


def _resolve_project_dpt(
    group_address: Mapping[str, Any],
    links: list[Any],
    communication_objects: Mapping[str, Any],
) -> str | None:
    direct = _format_project_dpt(group_address.get("dpt"))
    if direct is not None:
        return direct

    candidates: set[str] = set()
    for communication_object_id in links:
        communication_object = communication_objects.get(str(communication_object_id))
        if not isinstance(communication_object, Mapping):
            continue
        dpts = communication_object.get("dpts", [])
        if not isinstance(dpts, list):
            continue
        for dpt in dpts:
            formatted = _format_project_dpt(dpt)
            if formatted is not None:
                candidates.add(formatted)
    if len(candidates) == 1:
        return candidates.pop()
    return None


def _format_project_dpt(raw: Any) -> str | None:
    if not isinstance(raw, Mapping):
        return None
    main = raw.get("main")
    sub = raw.get("sub")
    if not isinstance(main, int) or isinstance(main, bool) or not 1 <= main <= 999:
        return None
    if sub is None:
        return str(main)
    if not isinstance(sub, int) or isinstance(sub, bool) or not 0 <= sub <= 999:
        return None
    return f"{main}.{sub:03d}"
