"""Agent module — Strands multi-agent system."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from strands import Agent
from strands.models.openai import OpenAIModel
from src.config import ModelSlot

if TYPE_CHECKING:
    from src.config import BrainConfig

logger = logging.getLogger(__name__)


# Suppress noisy "reasoningContent is not supported" warnings from Strands OpenAI model.
# MiniMax M2.5 always sends reasoning tokens; Strands filters them but warns on every turn.
class _SuppressReasoningContentWarning(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "reasoningContent is not supported" not in record.getMessage()


_strands_openai_logger = logging.getLogger("strands.models.openai")
_strands_openai_logger.addFilter(_SuppressReasoningContentWarning())


def create_model_for_slot(
    config: "BrainConfig", slot: ModelSlot, max_tokens: int = 16384
) -> OpenAIModel:
    """Create an OpenAIModel for the given slot using per-slot config."""
    slot_config = config.resolve_model(slot)
    api_key = os.environ.get(slot_config.api_key_env, "")
    if not api_key:
        raise RuntimeError(
            f"Missing API key env var {slot_config.api_key_env} for slot {slot.value}"
        )
    return OpenAIModel(
        model_id=slot_config.model_id,
        params={"max_tokens": max_tokens},
        client_args={"base_url": slot_config.base_url, "api_key": api_key},
    )


def create_specialist_agent(
    config: "BrainConfig",
    system_prompt: str,
    tools: list,
    model_slot: ModelSlot,
    domain: str = "",
    hooks: list | None = None,
) -> Agent:
    """Create a specialist agent with the given prompt and tools.

    Args:
        config: BrainConfig with model slot settings
        system_prompt: System prompt for the agent
        tools: List of @tool-decorated functions
        model_slot: ModelSlot enum value for model construction (required)
        domain: Label for logging (e.g. "comfort", "energy")
        hooks: List of HookProvider instances for guardrails (optional)
    """
    model = create_model_for_slot(config, model_slot)
    agent = Agent(model=model, system_prompt=system_prompt, tools=tools, hooks=hooks)
    logger.info(
        "%s agent created with model slot %s",
        domain.title() or "Specialist",
        model_slot.value,
    )
    return agent
