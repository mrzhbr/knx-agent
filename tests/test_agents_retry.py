"""Tests for LLM API retry logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from strands.types.exceptions import EventLoopException, ModelThrottledException

from src.agents.retry import _is_transient, invoke_with_retry


# ── _is_transient ──────────────────────────────────────────────────────────


def test_throttled_is_transient():
    assert _is_transient(ModelThrottledException("rate limited")) is True


def test_event_loop_wrapping_throttled_is_transient():
    exc = EventLoopException(ModelThrottledException("429"), {})
    assert _is_transient(exc) is True


def test_event_loop_wrapping_timeout_is_transient():
    exc = EventLoopException(TimeoutError("connection timed out"), {})
    assert _is_transient(exc) is True


def test_event_loop_wrapping_502_is_transient():
    exc = EventLoopException(Exception("502 Bad Gateway"), {})
    assert _is_transient(exc) is True


def test_event_loop_wrapping_503_is_transient():
    exc = EventLoopException(Exception("503 Service Unavailable"), {})
    assert _is_transient(exc) is True


def test_permanent_error_not_transient():
    assert _is_transient(ValueError("invalid input")) is False


def test_context_window_not_transient():
    assert _is_transient(Exception("context window exceeded")) is False


def test_rate_substring_not_false_positive():
    """Ensure 'rate' alone doesn't match — only 'rate limit' does."""
    assert _is_transient(Exception("inaccurate value")) is False
    assert _is_transient(Exception("rate limit exceeded")) is True


# ── invoke_with_retry ──────────────────────────────────────────────────────


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.messages = MagicMock()
    agent.messages.clear = MagicMock()
    agent.invoke_async = AsyncMock()
    return agent


async def test_success_no_retry(mock_agent):
    mock_agent.invoke_async.return_value = "Licht ist an."
    result = await invoke_with_retry(mock_agent, "Licht an", label="test")
    assert result == "Licht ist an."
    assert mock_agent.invoke_async.call_count == 1


@patch("src.agents.retry.asyncio.sleep", new_callable=AsyncMock)
async def test_retry_on_transient_then_success(mock_sleep, mock_agent):
    mock_agent.invoke_async.side_effect = [
        ModelThrottledException("429 Too Many Requests"),
        "Licht ist an.",
    ]
    result = await invoke_with_retry(mock_agent, "Licht an", label="test")
    assert result == "Licht ist an."
    assert mock_agent.invoke_async.call_count == 2
    mock_sleep.assert_awaited_once_with(1)


@patch("src.agents.retry.asyncio.sleep", new_callable=AsyncMock)
async def test_retry_exhausted_raises(mock_sleep, mock_agent):
    exc = EventLoopException(Exception("502 Bad Gateway"), {})
    mock_agent.invoke_async.side_effect = [exc, exc, exc, exc]
    with pytest.raises(EventLoopException):
        await invoke_with_retry(mock_agent, "Licht an", label="test")
    assert mock_agent.invoke_async.call_count == 4
    assert mock_sleep.await_count == 3


async def test_permanent_error_no_retry(mock_agent):
    mock_agent.invoke_async.side_effect = ValueError("bad input")
    with pytest.raises(ValueError):
        await invoke_with_retry(mock_agent, "Licht an", label="test")
    assert mock_agent.invoke_async.call_count == 1


@patch("src.agents.retry.asyncio.sleep", new_callable=AsyncMock)
async def test_backoff_delays(mock_sleep, mock_agent):
    exc = ModelThrottledException("rate limited")
    mock_agent.invoke_async.side_effect = [exc, exc, exc, "ok"]
    result = await invoke_with_retry(mock_agent, "test", label="test")
    assert result == "ok"
    assert mock_sleep.await_args_list[0].args == (1,)
    assert mock_sleep.await_args_list[1].args == (3,)
    assert mock_sleep.await_args_list[2].args == (9,)
