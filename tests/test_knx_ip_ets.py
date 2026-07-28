"""Tests for optional ETS validation of the authoritative KNX device map."""

from __future__ import annotations

import pytest  # type: ignore[import-not-found]

from src.hal.platforms.knx_ip_ets import (  # type: ignore[import-not-found]
    KNXETSValidationError,
    validate_registry_against_project,
)
from src.hal.platforms.knx_ip_registry import (  # type: ignore[import-not-found]
    KNXDeviceRegistry,
)


def _registry(*, dpts: dict[str, str] | None = None) -> KNXDeviceRegistry:
    return KNXDeviceRegistry.from_dict(
        {
            "schema_version": 1,
            "devices": {
                "window": {
                    "name": "Window",
                    "room": "Living room",
                    "type": "binary_sensor",
                    "sensor_class": "window",
                    "addresses": {"value_state": "2/1/1"},
                    "dpts": dpts or {},
                }
            },
        }
    )


def _project(
    *,
    address: str = "2/1/1",
    dpt: dict[str, int | None] | None = None,
    links: list[str] | None = None,
    communication_object_dpts: list[dict[str, int | None]] | None = None,
) -> dict:
    object_links = ["co-1"] if links is None else links
    return {
        "group_addresses": {
            address: {
                "address": address,
                "dpt": dpt,
                "communication_object_ids": object_links,
                "data_secure": False,
            }
        },
        "communication_objects": {
            "co-1": {"dpts": communication_object_dpts or [], "flags": {"read": True}}
        },
    }


def test_ets_supplies_missing_unambiguous_group_address_dpt():
    report = validate_registry_against_project(
        _registry(),
        _project(dpt={"main": 1, "sub": 19}),
    )

    assert not report.errors
    assert report.dpt_for("window", "value_state") == "1.019"


def test_ets_can_supply_dpt_from_one_linked_communication_object_type():
    report = validate_registry_against_project(
        _registry(),
        _project(dpt=None, communication_object_dpts=[{"main": 1, "sub": 19}]),
    )

    assert not report.errors
    assert report.dpt_for("window", "value_state") == "1.019"


def test_ets_validation_matches_equivalent_short_and_long_address_styles():
    registry = KNXDeviceRegistry.from_dict(
        {
            "schema_version": 1,
            "devices": {
                "contact": {
                    "name": "Contact",
                    "room": "Living room",
                    "type": "binary_sensor",
                    "addresses": {"value_state": "2/1"},
                }
            },
        }
    )
    project = _project(address="2/0/1", dpt={"main": 1, "sub": 19})
    project["group_addresses"]["2/0/1"]["raw_address"] = 4097

    report = validate_registry_against_project(registry, project)

    assert not report.errors
    assert report.dpt_for("contact", "value_state") == "1.019"


def test_missing_configured_address_is_an_error():
    report = validate_registry_against_project(
        _registry(),
        _project(address="2/1/2", dpt={"main": 1, "sub": 19}),
    )

    assert any("2/1/1" in error and "not present" in error for error in report.errors)
    with pytest.raises(KNXETSValidationError):
        report.require_valid()


def test_explicit_mapping_dpt_conflict_is_an_error():
    report = validate_registry_against_project(
        _registry(dpts={"value_state": "1.019"}),
        _project(dpt={"main": 1, "sub": 2}),
    )

    assert any("DPT conflict" in error for error in report.errors)


def test_ets_supplied_dpt_must_be_compatible_with_mapped_role():
    report = validate_registry_against_project(
        _registry(),
        _project(dpt={"main": 5, "sub": 1}),
    )

    assert any("not compatible" in error for error in report.errors)


def test_untyped_address_is_an_error_when_no_source_can_resolve_it():
    report = validate_registry_against_project(_registry(), _project(dpt=None))

    assert any("no resolvable DPT" in error for error in report.errors)


def test_dangling_communication_object_link_is_an_error():
    project = _project(dpt={"main": 1, "sub": 19}, links=["missing"])

    report = validate_registry_against_project(_registry(), project)

    assert any("missing communication object" in error for error in report.errors)


def test_unlinked_read_only_address_is_reported_as_warning():
    report = validate_registry_against_project(
        _registry(),
        _project(dpt={"main": 1, "sub": 19}, links=[]),
    )

    assert not report.errors
    assert any("not linked" in warning for warning in report.warnings)


def test_ets_reports_data_secure_for_mapped_address():
    project = _project(dpt={"main": 1, "sub": 19})
    project["group_addresses"]["2/1/1"]["data_secure"] = True

    report = validate_registry_against_project(_registry(), project)

    assert len(report.data_secure_roles) == 1
    device_id, role = report.data_secure_roles[0]
    assert device_id == "window"
    assert role == "value_state"


def test_validation_does_not_change_authoritative_device_metadata():
    registry = _registry()

    report = validate_registry_against_project(
        registry,
        _project(dpt={"main": 1, "sub": 19}),
    )

    device = registry.get("window")
    assert device is not None
    assert device.name == "Window"
    assert device.room == "Living room"
    assert device.addresses == {"value_state": "2/1/1"}
    assert report.dpt_for("window", "value_state") == "1.019"
