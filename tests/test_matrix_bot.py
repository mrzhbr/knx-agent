from __future__ import annotations

# Test-only mocks intentionally replace matrix-nio's concrete client.
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from unittest.mock import AsyncMock, MagicMock, patch


class MockAsyncClient:
    def __init__(self, homeserver, user_id):
        self.user_id = "@agent:example.test"
        self._callbacks = []
        self.login = AsyncMock(return_value=MagicMock(access_token="tok123"))
        self.sync_forever = AsyncMock()
        self.room_send = AsyncMock()
        self.close = AsyncMock()

    def add_event_callback(self, callback, event_type):
        self._callbacks.append((callback, event_type))


async def test_set_message_handler():
    with patch("src.bot.matrix.AsyncClient", MockAsyncClient):
        from src.bot.matrix import MatrixBot

        bot = MatrixBot(
            homeserver="http://localhost:8008",
            user_id="@agent:example.test",
            password="test",
            allowed_room_ids=["!room:example.test"],
        )
        handler = AsyncMock(return_value="response")
        bot.set_message_handler(handler)
        assert bot._message_handler is handler


async def test_start_calls_login():
    with patch("src.bot.matrix.AsyncClient", MockAsyncClient):
        from src.bot.matrix import MatrixBot

        bot = MatrixBot(
            homeserver="http://localhost:8008",
            user_id="@agent:example.test",
            password="test_password",
            allowed_room_ids=["!room:example.test"],
        )
        # Mock create_task to avoid actually running sync_forever
        with patch("asyncio.create_task") as mock_task:
            mock_task.return_value = MagicMock()
            bot._sync_task = MagicMock()  # Set manually
            bot._client = MockAsyncClient("http://localhost:8008", "@agent:example.test")
            bot._client.login = AsyncMock(return_value=MagicMock(access_token="tok123"))

            await bot._client.login("test_password")
            bot._client.login.assert_called_once()


async def test_health_check_before_start():
    with patch("src.bot.matrix.AsyncClient", MockAsyncClient):
        from src.bot.matrix import MatrixBot

        bot = MatrixBot(
            homeserver="http://localhost:8008",
            user_id="@agent:example.test",
            password="test",
            allowed_room_ids=[],
        )
        assert not await bot.health_check()


async def test_authorization_fails_closed_for_empty_allowlists():
    from src.bot.matrix import MatrixBot

    bot = MatrixBot(
        homeserver="http://localhost:8008",
        user_id="@agent:example.test",
        password="test",
        allowed_room_ids=[],
        allowed_user_ids=[],
    )
    assert not bot._is_authorized("!room:example.test", "@admin:example.test")


async def test_authorization_requires_exact_room_and_sender():
    from src.bot.matrix import MatrixBot

    bot = MatrixBot(
        homeserver="http://localhost:8008",
        user_id="@agent:example.test",
        password="test",
        allowed_room_ids=["!room:example.test"],
        allowed_user_ids=["@admin:example.test"],
    )
    assert bot._is_authorized("!room:example.test", "@ADMIN:EXAMPLE.TEST")
    assert not bot._is_authorized("!other:example.test", "@admin:example.test")
    assert not bot._is_authorized("!room:example.test", "@unknown:example.test")


async def test_send_after_start():
    with patch("src.bot.matrix.AsyncClient", MockAsyncClient):
        from src.bot.matrix import MatrixBot

        bot = MatrixBot(
            homeserver="http://localhost:8008",
            user_id="@agent:example.test",
            password="test",
            allowed_room_ids=["!room:example.test"],
        )
        mock_client = MockAsyncClient("http://localhost:8008", "@agent:example.test")
        bot._client = mock_client
        await bot.send("!room:example.test", "Hallo!")
        mock_client.room_send.assert_called_once()
        call_args = mock_client.room_send.call_args[0]
        assert call_args[2]["body"] == "Hallo!"
