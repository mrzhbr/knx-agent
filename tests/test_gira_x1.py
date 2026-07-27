from __future__ import annotations

import pytest
from unittest.mock import AsyncMock
import httpx

from src.integrations.gira_x1 import GiraX1Adapter


class MockResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = str(json_data)

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("Error", request=None, response=self)


async def test_start_creates_client():
    adapter = GiraX1Adapter("10.0.0.1", "test_token")
    await adapter.start()
    assert adapter._client is not None
    await adapter.stop()


async def test_stop_closes_client():
    adapter = GiraX1Adapter("10.0.0.1", "test_token")
    await adapter.start()
    await adapter.stop()
    assert adapter._client is None


async def test_get_client_before_start_raises():
    adapter = GiraX1Adapter("10.0.0.1", "test_token")
    with pytest.raises(RuntimeError, match="not started"):
        adapter._get_client()


async def test_get_value():
    adapter = GiraX1Adapter("10.0.0.1", "test_token")
    await adapter.start()

    mock_response = MockResponse(
        json_data={
            "values": [
                {"uid": "dp-dimmer-1-on", "value": "1"},
                {"uid": "dp-dimmer-1-shift", "value": "80"},
            ]
        }
    )
    adapter._client.get = AsyncMock(return_value=mock_response)

    result = await adapter.get_value("fn-dimmer-1")
    assert result == {"dp-dimmer-1-on": "1", "dp-dimmer-1-shift": "80"}
    await adapter.stop()


async def test_set_value():
    adapter = GiraX1Adapter("10.0.0.1", "test_token")
    await adapter.start()

    mock_response = MockResponse(status_code=200)
    adapter._client.put = AsyncMock(return_value=mock_response)

    await adapter.set_value("dp-dimmer-1-on", "1")
    adapter._client.put.assert_called_once()
    call_kwargs = adapter._client.put.call_args
    assert "value" in call_kwargs[1]["json"] or "value" in str(call_kwargs)
    await adapter.stop()


async def test_set_values():
    adapter = GiraX1Adapter("10.0.0.1", "test_token")
    await adapter.start()

    mock_response = MockResponse(status_code=200)
    adapter._client.put = AsyncMock(return_value=mock_response)

    await adapter.set_values({"dp-dimmer-1-on": "1", "dp-dimmer-1-shift": "80"})
    call_kwargs = adapter._client.put.call_args
    body = call_kwargs[1]["json"]
    assert "values" in body
    assert len(body["values"]) == 2
    await adapter.stop()


async def test_api_v2_url():
    adapter = GiraX1Adapter("10.0.0.1", "tok123")
    url = adapter._api_v2_url("values/dp-dimmer-1-on")
    assert "https://10.0.0.1/api/v2/values/dp-dimmer-1-on" in url
    assert "tok123" in url


async def test_api_clients_url():
    adapter = GiraX1Adapter("10.0.0.1", "tok123")
    url = adapter._api_clients_url("tok123/callbacks")
    assert "https://10.0.0.1/api/clients/tok123/callbacks" in url
