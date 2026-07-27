from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from src.integrations.door_station import DoorStationAdapter


class MockResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


async def test_trigger_door_relay_success():
    """trigger_door_relay sends correct POST request to T24M endpoint."""
    adapter = DoorStationAdapter(host="10.0.0.249", password="testpass")

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=MockResponse(200))
    adapter._client = mock_client

    result = await adapter.trigger_door_relay()

    assert result
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    url = call_args[0][0]
    assert "10.0.0.249" in url
    assert "openDoor" in url


async def test_trigger_door_relay_not_started():
    """trigger_door_relay should return False if not started."""
    adapter = DoorStationAdapter()
    result = await adapter.trigger_door_relay()
    assert not result


async def test_trigger_door_relay_failure():
    """trigger_door_relay should return False on HTTP error."""
    adapter = DoorStationAdapter(host="10.0.0.249")

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=MockResponse(503))
    adapter._client = mock_client

    result = await adapter.trigger_door_relay()
    assert not result


async def test_on_bell_fires_bus_event():
    """Bell webhook: bus.on() removed in Plan 04, test that _on_bell runs without bus."""
    adapter = DoorStationAdapter(webhook_token="test-webhook-token")

    mock_request = MagicMock()
    mock_request.headers = {"X-Gira-Webhook-Token": "test-webhook-token"}
    mock_request.query = {}

    response = await adapter._on_bell(mock_request)
    assert response.status == 200


async def test_on_bell_calls_handler():
    """Bell webhook should call registered bell handler."""
    called = []

    async def my_handler():
        called.append(True)

    adapter = DoorStationAdapter(
        bell_handler=my_handler,
        webhook_token="test-webhook-token",
    )

    mock_request = MagicMock()
    mock_request.headers = {"X-Gira-Webhook-Token": "test-webhook-token"}
    mock_request.query = {}

    await adapter._on_bell(mock_request)
    assert len(called) == 1


async def test_on_bell_rejects_missing_token():
    handler = AsyncMock()
    adapter = DoorStationAdapter(
        bell_handler=handler,
        webhook_token="test-webhook-token",
    )
    request = MagicMock()
    request.headers = {}
    request.query = {}

    response = await adapter._on_bell(request)

    assert response.status == 403
    handler.assert_not_called()
