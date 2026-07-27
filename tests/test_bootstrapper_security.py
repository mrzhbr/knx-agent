from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.bootstrapper import Bootstrapper
from src.core.guardrails import get_request_security_context
from src.core.profiles import FAMILY_PROFILES, load_profiles_from_config


def _load_admin() -> None:
    load_profiles_from_config(
        [
            {
                "key": "admin",
                "name": "Admin",
                "trust_level": "admin",
                "matrix_ids": ["@admin:test.local"],
            }
        ]
    )


async def test_unknown_matrix_principal_fails_closed() -> None:
    FAMILY_PROFILES.clear()
    bootstrapper = Bootstrapper(Path("config.yaml"))

    response = await bootstrapper._on_message(
        "!private:test.local", "@unknown:test.local", "Licht an"
    )

    assert response == "Nicht autorisiert."


async def test_door_confirmation_is_bound_and_consumed_once() -> None:
    _load_admin()
    bootstrapper = Bootstrapper(Path("config.yaml"))
    key = ("!private:test.local", "@admin:test.local")
    bootstrapper._pending_security_actions[key] = ("door_open", time.monotonic() + 60)

    async def confirmed_door_open() -> str:
        request = get_request_security_context()
        assert request is not None
        assert "door_open" in request.confirmed_actions
        return "Tür geöffnet."

    try:
        with patch(
            "src.tools.security.door_open", AsyncMock(side_effect=confirmed_door_open)
        ) as tool:
            response = await bootstrapper._on_message(*key, "Ja")
        assert response == "Tür geöffnet."
        tool.assert_awaited_once()
        assert key not in bootstrapper._pending_security_actions
    finally:
        FAMILY_PROFILES.clear()


async def test_expired_confirmation_is_not_executed() -> None:
    _load_admin()
    bootstrapper = Bootstrapper(Path("config.yaml"))
    key = ("!private:test.local", "@admin:test.local")
    bootstrapper._pending_security_actions[key] = ("door_open", time.monotonic() - 1)

    try:
        with (
            patch.object(
                bootstrapper,
                "_handle_message",
                AsyncMock(return_value="Keine Aktion."),
            ),
            patch("src.tools.security.door_open", AsyncMock()) as tool,
        ):
            response = await bootstrapper._on_message(*key, "Ja")
        assert response == "Keine Aktion."
        tool.assert_not_awaited()
        assert key not in bootstrapper._pending_security_actions
    finally:
        FAMILY_PROFILES.clear()
