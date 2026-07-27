"""Deterministic, request-local safety guardrails for agent tool calls.

The Strands hook is an early enforcement and UX layer. Security-sensitive tools
also enforce the same request context themselves so direct scheduler/tool calls
cannot bypass policy.
"""

from __future__ import annotations

import contextvars
import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Callable, Iterator

from strands.hooks import (  # type: ignore[import-not-found]
    BeforeInvocationEvent,
    BeforeToolCallEvent,
    HookRegistry,
)

logger = logging.getLogger(__name__)


class RequestSource(StrEnum):
    """Trusted provenance for the current request."""

    USER = "user"
    AUTOMATION = "automation"
    INTERNAL = "internal"


@dataclass(frozen=True)
class RequestSecurityContext:
    """Immutable security state bound to one asyncio request context."""

    user_profile: Any = None
    source: RequestSource = RequestSource.INTERNAL
    confirmed_actions: frozenset[str] = frozenset()


_current_request_context: contextvars.ContextVar[RequestSecurityContext | None] = (
    contextvars.ContextVar("_current_request_context", default=None)
)
_current_tool_call_count: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "_current_tool_call_count", default=None
)
# Backward-compatible setter used by existing integrations/tests. New code should
# use request_security_context() so provenance and confirmation travel together.
_current_user_profile: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "_current_user_profile", default=None
)


@contextmanager
def request_security_context(
    *,
    user_profile: Any = None,
    source: RequestSource | str = RequestSource.INTERNAL,
    confirmed_actions: frozenset[str] | set[str] = frozenset(),
) -> Iterator[RequestSecurityContext]:
    """Bind immutable security state and an isolated call counter to this request."""
    request = RequestSecurityContext(
        user_profile=user_profile,
        source=RequestSource(source),
        confirmed_actions=frozenset(confirmed_actions),
    )
    request_token = _current_request_context.set(request)
    profile_token = _current_user_profile.set(user_profile)
    counter_token = _current_tool_call_count.set(0)
    try:
        yield request
    finally:
        _current_tool_call_count.reset(counter_token)
        _current_user_profile.reset(profile_token)
        _current_request_context.reset(request_token)


def get_request_security_context() -> RequestSecurityContext | None:
    """Return security state for the current task, if one was explicitly bound."""
    return _current_request_context.get()


def is_action_confirmed(action: str) -> bool:
    """Return whether a server-issued confirmation grant covers ``action``."""
    request = get_request_security_context()
    return request is not None and action in request.confirmed_actions


def set_request_user_profile(profile: Any) -> None:
    """Set the legacy request profile without granting provenance or confirmation."""
    _current_user_profile.set(profile)


@dataclass
class GuardrailContext:
    """Static dependencies and legacy fallback state for rule evaluators."""

    presence_manager: Any = None
    user_profile: Any = None
    tool_call_count: int = 0
    user_confirmed: bool = False
    source: str = RequestSource.INTERNAL.value


@dataclass
class GuardrailRule:
    """A single safety rule evaluated before tool calls."""

    name: str
    tool_selector: Callable | str
    evaluator: Callable
    action: str
    message: str | Callable


class GuardrailProvider:
    """Evaluate deterministic safety rules before every Strands tool call."""

    def __init__(self, context: GuardrailContext | None = None) -> None:
        self._context = context or GuardrailContext()
        self._rules = _build_default_rules()

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self._on_before_tool_call)
        registry.add_callback(BeforeInvocationEvent, self._on_before_invocation)

    def update_context(self, **kwargs: Any) -> None:
        """Update static dependencies only; request state belongs in ContextVars."""
        for key, value in kwargs.items():
            if hasattr(self._context, key):
                setattr(self._context, key, value)

    def _effective_context(self) -> GuardrailContext:
        request = get_request_security_context()
        if request is None:
            profile = _current_user_profile.get()
            if profile is None:
                return self._context
            return replace(self._context, user_profile=profile)
        return replace(
            self._context,
            user_profile=request.user_profile,
            source=request.source.value,
            user_confirmed="door_open" in request.confirmed_actions,
            tool_call_count=_current_tool_call_count.get() or 0,
        )

    def _on_before_tool_call(self, event: BeforeToolCallEvent) -> None:
        tool_name = event.tool_use["name"]
        tool_args = event.tool_use["input"]
        request = get_request_security_context()

        if request is None:
            self._context.tool_call_count += 1
        else:
            _current_tool_call_count.set((_current_tool_call_count.get() or 0) + 1)

        context = self._effective_context()
        for rule in self._rules:
            if not self._matches_selector(rule.tool_selector, tool_name):
                continue
            if not rule.evaluator(tool_name, tool_args, context):
                continue

            message = rule.message(context) if callable(rule.message) else rule.message
            logger.warning(
                "Guardrail triggered: rule=%s tool=%s action=%s",
                rule.name,
                tool_name,
                rule.action,
            )
            event.cancel_tool = message
            return

    def _on_before_invocation(self, event: BeforeInvocationEvent) -> None:
        """Reset only legacy state; request counters span nested agent invocations."""
        if get_request_security_context() is None:
            self._context.tool_call_count = 0

    @staticmethod
    def _matches_selector(selector: Callable | str, tool_name: str) -> bool:
        if callable(selector):
            return selector(tool_name)
        return tool_name == selector or tool_name.startswith(selector)


_KIDS_SAFE_ACTIONS = frozenset(
    {"lights_on", "lights_off", "lights_dim", "blinds_up", "blinds_down", "blinds_stop"}
)


def _kids_batch_evaluator(tool_name: str, args: dict, ctx: Any) -> bool:
    if ctx.user_profile is None:
        return False
    if getattr(ctx.user_profile, "trust_level", "limited") != "limited":
        return False

    try:
        cmd_list = json.loads(args.get("commands", "[]"))
    except (json.JSONDecodeError, TypeError):
        return True
    if not isinstance(cmd_list, list):
        return True
    return any(
        not isinstance(command, dict) or command.get("action", "") not in _KIDS_SAFE_ACTIONS
        for command in cmd_list
    )


def _alarm_presence_evaluator(tool_name: str, args: dict, ctx: Any) -> bool:
    """Use sync legacy presence when available; HAL presence is checked in the async tool."""
    if args.get("action") != "arm":
        return False
    presence = ctx.presence_manager
    if presence is None:
        return True
    if getattr(presence, "_hal", None) is not None:
        return False
    return not presence.nobody_home()


def _build_default_rules() -> list[GuardrailRule]:
    return [
        GuardrailRule(
            name="security_actions_never_automated",
            tool_selector=lambda name: name in {"alarm_control", "door_open"},
            evaluator=lambda name, args, ctx: ctx.source == RequestSource.AUTOMATION.value,
            action="deny",
            message="Sicherheitskritische Aktionen dürfen nie automatisch ausgeführt werden.",
        ),
        GuardrailRule(
            name="alarm_requires_presence_check",
            tool_selector=lambda name: name == "alarm_control",
            evaluator=_alarm_presence_evaluator,
            action="deny",
            message=(
                "Alarm kann nicht scharf gestellt werden: Jemand ist zuhause "
                "oder Anwesenheit ist nicht sicher ausgeschlossen."
            ),
        ),
        GuardrailRule(
            name="alarm_requires_windows_closed",
            tool_selector=lambda name: name == "alarm_control",
            evaluator=lambda name, args, ctx: (
                args.get("action") == "arm"
                and ctx.presence_manager is not None
                and len(ctx.presence_manager.open_windows) > 0
            ),
            action="deny",
            message=lambda ctx: (
                f"Fenster offen: {', '.join(ctx.presence_manager.open_windows)}. "
                "Alarm kann nicht scharf gestellt werden."
            ),
        ),
        GuardrailRule(
            name="door_requires_confirmation",
            tool_selector="door_open",
            evaluator=lambda name, args, ctx: not ctx.user_confirmed,
            action="steer",
            message="Frage den Benutzer: 'Soll ich die Tür wirklich öffnen?'",
        ),
        GuardrailRule(
            name="kids_batch_restricted",
            tool_selector=lambda name: name == "execute_batch",
            evaluator=_kids_batch_evaluator,
            action="deny",
            message="Du hast keine Berechtigung fuer diese Aktionen im Batch-Befehl.",
        ),
        GuardrailRule(
            name="kids_limited_tools",
            tool_selector=lambda name: (
                name not in ("lights_control", "blinds_control", "execute_batch")
            ),
            evaluator=lambda name, args, ctx: (
                ctx.user_profile is not None
                and getattr(ctx.user_profile, "trust_level", "limited") == "limited"
            ),
            action="deny",
            message="Du hast keine Berechtigung für diese Aktion.",
        ),
        GuardrailRule(
            name="max_tool_calls",
            tool_selector=lambda name: True,
            evaluator=lambda name, args, ctx: ctx.tool_call_count > 15,
            action="deny",
            message="Zu viele Tool-Aufrufe. Abbruch.",
        ),
    ]
