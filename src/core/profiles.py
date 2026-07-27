"""Family profile system — maps Matrix user IDs to FamilyProfile entries.

Profiles control:
- trust_level: "admin" (full access) or "limited" (lights + blinds only)
- response_style: how the agent formats responses
- allowed_tools: explicit allowlist for "limited" users (optional)

Profiles are loaded from config.yaml at startup via load_profiles_from_config().

Used by:
- GuardrailProvider: kids_limited_tools rule checks trust_level == "limited"
- Supervisor prompt: includes response_style in embodied context
- MatrixBot: resolve_user() maps sender Matrix ID to profile
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FamilyProfile:
    """User profile controlling trust level and response style."""

    name: str
    trust_level: str  # "admin" | "limited"
    language: str = "casual German"
    response_style: str = "friendly, concise"
    allowed_tools: list[str] = field(default_factory=list)
    matrix_ids: list[str] = field(default_factory=list)


# Populated at startup by load_profiles_from_config()
FAMILY_PROFILES: dict[str, FamilyProfile] = {}


def load_profiles_from_config(profiles_config: list[dict]) -> None:
    """Load family profiles from config.yaml profiles section.

    Args:
        profiles_config: List of profile dicts from config.yaml, each with
            keys: key, name, trust_level, and optionally language,
            response_style, allowed_tools, matrix_ids.
    """
    FAMILY_PROFILES.clear()
    for entry in profiles_config:
        key = entry.get("key", entry.get("name", "").lower())
        if not key:
            continue
        FAMILY_PROFILES[key] = FamilyProfile(
            name=entry.get("name", key),
            trust_level=entry.get("trust_level", "admin"),
            language=entry.get("language", "casual German"),
            response_style=entry.get("response_style", "friendly, concise"),
            allowed_tools=entry.get("allowed_tools", []),
            matrix_ids=entry.get("matrix_ids", []),
        )


def format_profiles_for_prompt() -> str:
    """Format loaded profiles as a German prompt section for the supervisor."""
    if not FAMILY_PROFILES:
        return "Keine Familienprofile konfiguriert."
    lines: list[str] = []
    for profile in FAMILY_PROFILES.values():
        role = "Admin" if profile.trust_level == "admin" else "Eingeschränkt"
        tools_note = ""
        if profile.allowed_tools:
            tools_note = f", nur {', '.join(profile.allowed_tools)}"
        lines.append(f"- {profile.name} ({role}): {profile.response_style}{tools_note}")
    return "\n".join(lines)


def resolve_user(matrix_user_id: str) -> FamilyProfile | None:
    """Resolve a Matrix user ID to a FamilyProfile.

    Args:
        matrix_user_id: Full Matrix user ID, e.g. '@user:homeserver'.
    """
    user_lower = matrix_user_id.lower()
    for profile in FAMILY_PROFILES.values():
        for mid in profile.matrix_ids:
            if mid.lower() == user_lower:
                return profile
    return None


def get_profile(name: str) -> FamilyProfile | None:
    """Look up a profile by its key name.

    Args:
        name: Profile key name (case-insensitive).
    """
    return FAMILY_PROFILES.get(name.lower())
