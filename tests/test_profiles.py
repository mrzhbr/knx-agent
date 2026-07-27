from __future__ import annotations

import pytest

from src.core.profiles import (
    FamilyProfile,
    FAMILY_PROFILES,
    load_profiles_from_config,
    format_profiles_for_prompt,
    resolve_user,
    get_profile,
)

_TEST_PROFILES = [
    {
        "key": "admin1",
        "name": "Admin",
        "trust_level": "admin",
        "response_style": "short, no fluff",
        "matrix_ids": ["@admin:test.local"],
    },
    {
        "key": "user2",
        "name": "User",
        "trust_level": "admin",
        "response_style": "friendly",
        "matrix_ids": ["@user:test.local"],
    },
    {
        "key": "kids",
        "name": "Kids",
        "trust_level": "limited",
        "response_style": "playful",
        "allowed_tools": ["lights_control", "blinds_control"],
        "matrix_ids": ["@kid:test.local"],
    },
]


@pytest.fixture(autouse=True)
def _load_test_profiles():
    """Load test profiles before each test, clear after."""
    load_profiles_from_config(_TEST_PROFILES)
    yield
    FAMILY_PROFILES.clear()


def test_profiles_loaded():
    """load_profiles_from_config populates FAMILY_PROFILES."""
    assert "admin1" in FAMILY_PROFILES
    assert "user2" in FAMILY_PROFILES
    assert "kids" in FAMILY_PROFILES


def test_admin_trust_level():
    assert FAMILY_PROFILES["admin1"].trust_level == "admin"


def test_kids_is_limited():
    kids = FAMILY_PROFILES["kids"]
    assert kids.trust_level == "limited"
    assert "lights_control" in kids.allowed_tools
    assert "blinds_control" in kids.allowed_tools


def test_resolve_user_by_matrix_id():
    profile = resolve_user("@admin:test.local")
    assert profile is not None
    assert profile.name == "Admin"


def test_resolve_user_case_insensitive():
    profile = resolve_user("@ADMIN:TEST.LOCAL")
    assert profile is not None
    assert profile.name == "Admin"


def test_resolve_user_unknown_returns_none():
    assert resolve_user("@unknown:other.server") is None


def test_resolve_user_kids():
    profile = resolve_user("@kid:test.local")
    assert profile is not None
    assert profile.trust_level == "limited"


def test_get_profile_by_name():
    profile = get_profile("admin1")
    assert profile is not None
    assert profile.name == "Admin"


def test_get_profile_case_insensitive():
    profile = get_profile("USER2")
    assert profile is not None
    assert profile.name == "User"


def test_get_profile_unknown():
    assert get_profile("nonexistent") is None


def test_format_profiles_for_prompt():
    result = format_profiles_for_prompt()
    assert "Admin" in result
    assert "Kids" in result
    assert "Eingeschränkt" in result


def test_format_profiles_empty():
    FAMILY_PROFILES.clear()
    result = format_profiles_for_prompt()
    assert "Keine" in result


def test_family_profile_dataclass():
    p = FamilyProfile(
        name="Test",
        trust_level="limited",
        allowed_tools=["lights_control"],
        matrix_ids=["@test:example.com"],
    )
    assert p.name == "Test"
    assert p.trust_level == "limited"
