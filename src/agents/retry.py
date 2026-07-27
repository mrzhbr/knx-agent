"""Retry logic for LLM API calls via Strands invoke_async().

Retries on transient errors (connection timeouts, rate limits, server errors)
with exponential backoff. Permanent failures are re-raised immediately.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from strands.types.exceptions import EventLoopException, ModelThrottledException

if TYPE_CHECKING:
    from strands import Agent

logger = logging.getLogger(__name__)

# Substrings in error messages that indicate transient server errors
_TRANSIENT_PATTERNS = (
    "429",
    "500",
    "502",
    "503",
    "rate limit",
    "ratelimit",
    "too many requests",
    "timeout",
    "timed out",
    "connection",
)

MAX_ATTEMPTS = 4
BACKOFF_SECONDS = (1, 3, 9)


def _is_transient(exc: Exception) -> bool:
    """Check if an exception is transient and worth retrying."""
    if isinstance(exc, ModelThrottledException):
        return True

    if isinstance(exc, EventLoopException):
        original = exc.original_exception
        if isinstance(original, ModelThrottledException):
            return True
        msg = str(original).lower()
        return any(p in msg for p in _TRANSIENT_PATTERNS)

    msg = str(exc).lower()
    return any(p in msg for p in _TRANSIENT_PATTERNS)


async def invoke_with_retry(agent: "Agent", text: str, label: str = "agent") -> str:
    """Call agent.invoke_async() with retry on transient errors.

    Args:
        agent: Strands Agent instance (messages are cleared before each attempt).
        text: The prompt text to send.
        label: Human-readable label for log messages.

    Returns:
        The agent response as a string.

    Raises:
        The original exception if all retries are exhausted or the error is not transient.
    """
    last_exc: Exception | None = None

    for attempt in range(MAX_ATTEMPTS):
        try:
            agent.messages.clear()
            result = await agent.invoke_async(text)
            return str(result).strip()
        except Exception as exc:
            last_exc = exc
            if not _is_transient(exc) or attempt == MAX_ATTEMPTS - 1:
                raise
            delay = BACKOFF_SECONDS[attempt]
            logger.warning(
                "%s: transient error (attempt %d/%d), retrying in %ds: %s",
                label,
                attempt + 1,
                MAX_ATTEMPTS,
                delay,
                exc,
            )
            await asyncio.sleep(delay)

    raise last_exc  # type: ignore[misc]  # unreachable, but satisfies type checker
