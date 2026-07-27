"""Pattern detector — hourly Haiku-based analysis of episodic memory for routines.

Scans the past week's events, asks Haiku to find repeating patterns,
and stores candidate routines in procedural memory when confidence > 0.8.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import BrainConfig
    from src.core.awareness import HouseAwareness
    from src.memory.layer import MemoryLayer

logger = logging.getLogger(__name__)

# Module-level training writer — set by bootstrapper when training_data.enabled (AD-4)
_training_writer = None  # TrainingDataWriter | None


def set_training_writer(writer) -> None:
    """Inject the TrainingDataWriter instance for background task logging."""
    global _training_writer
    _training_writer = writer


_MIN_EVENTS = 20
_MIN_CONFIDENCE = 0.8
_MIN_OCCURRENCES = 5


class PatternDetector:
    """Hourly pattern analysis using Haiku + episodic memory.

    Reads past week's events, invokes Haiku for JSON pattern analysis,
    stores high-confidence candidates as routines in procedural memory.

    Usage::

        detector = PatternDetector(memory_layer, config)
        detector.start()
        # ... later:
        detector.stop()
    """

    def __init__(
        self,
        memory_layer: "MemoryLayer",
        config: "BrainConfig",
        awareness: "HouseAwareness | None" = None,
    ) -> None:
        self._memory = memory_layer
        self._config = config
        self._awareness = awareness
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Start the hourly pattern detection loop as an asyncio Task."""
        self._task = asyncio.create_task(self.run(), name="pattern_detector")
        logger.info("PatternDetector started")

    def stop(self) -> None:
        """Cancel the pattern detection loop."""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("PatternDetector stopped")

    async def run(self) -> None:
        """Infinite loop: analyze patterns every 3600 seconds."""
        logger.info("PatternDetector running (hourly)")
        while True:
            try:
                await asyncio.sleep(3600)
                await self._analyze()
            except asyncio.CancelledError as cancellation:
                raise cancellation
            except Exception as e:
                logger.error("PatternDetector error: %s", e)

    async def _analyze(self) -> None:
        """Run one pattern analysis cycle."""
        if not self._memory.episodic:
            logger.debug("PatternDetector: no episodic memory, skipping")
            return

        # Step 1: Fetch past week's events
        try:
            events = await self._memory.episodic.search_recent(hours=168)
        except Exception as e:
            logger.warning("PatternDetector: search_recent failed: %s", e)
            return

        # Step 2: Skip if insufficient data
        if len(events) < _MIN_EVENTS:
            logger.debug(
                "PatternDetector: only %d events, need %d — skipping", len(events), _MIN_EVENTS
            )
            return

        # Step 3: Format events as text
        event_lines = []
        for ev in events[:200]:  # Cap to avoid massive prompts
            text = ev.get("text", "")
            meta = ev.get("metadata", {})
            ts = meta.get("timestamp", "")[:16]
            event_lines.append(f"[{ts}] {text}")
        events_text = "\n".join(event_lines)

        # Step 3b: Gather presence context for habit correlation
        presence_patterns = await self._gather_presence_patterns()

        # Step 4: Call router tier for pattern analysis (with presence context)
        try:
            patterns = await self._call_router_tier(events_text, presence_patterns)
        except Exception as e:
            logger.warning("PatternDetector: _call_router_tier raised: %s", e)
            return

        # Step 5: Store high-confidence patterns as candidate routines
        procedural = self._memory.procedural
        stored = 0
        for p in patterns:
            pattern = p.get("pattern", "")
            occurrences = p.get("occurrences", 0)
            typical_time = p.get("typical_time", "")
            confidence = p.get("confidence", 0.0)

            if confidence >= _MIN_CONFIDENCE and occurrences >= _MIN_OCCURRENCES:
                description = f"{occurrences} Beobachtungen um {typical_time}"
                try:
                    # Enrich trigger with presence context if available
                    who = p.get("who", "")
                    days = p.get("days", [])
                    trigger_context = p.get("trigger_context", "")
                    enriched_trigger = typical_time
                    if trigger_context:
                        enriched_trigger = f"{typical_time} ({trigger_context})"
                    enriched_actions = [{"description": pattern}]
                    if who or days:
                        enriched_actions[0]["who"] = who
                        enriched_actions[0]["days"] = days
                    await procedural.store_candidate_routine(
                        name=pattern,
                        description=description,
                        trigger=enriched_trigger,
                        actions=enriched_actions,
                        confidence=confidence,
                    )
                    stored += 1
                    logger.info(
                        "PatternDetector: stored routine '%s' (conf=%.2f, n=%d)",
                        pattern,
                        confidence,
                        occurrences,
                    )
                except Exception as e:
                    logger.warning("PatternDetector: store failed for '%s': %s", pattern, e)

        logger.info("PatternDetector: %d events analyzed, %d routines stored", len(events), stored)

    async def _gather_presence_patterns(self) -> str:
        """Gather presence context from awareness stream or routine executions.

        Primary: awareness stream (last 7 days = 10080 minutes).
        Fallback: routine execution history.
        Returns formatted presence patterns string, or empty string if insufficient data.
        """
        presence_events = []

        # Primary source: awareness stream
        if self._awareness is not None:
            try:
                recent = await self._awareness.get_recent_events(minutes=10080)  # 7 days
                presence_events = [e for e in recent if e.get("device_type") == "presence_sensor"]
            except Exception as e:
                logger.debug("PatternDetector: awareness presence query failed: %s", e)

        # Fallback: routine execution history
        if len(presence_events) < 5:
            try:
                executions = await self._memory.procedural.get_routine_executions()
                if len(executions) > 0:
                    logger.debug(
                        "PatternDetector: using %d routine executions as presence fallback",
                        len(executions),
                    )
            except Exception as e:
                logger.debug("PatternDetector: routine execution fallback failed: %s", e)

        if len(presence_events) < 5:
            return "Unzureichende Anwesenheitsdaten -- Korrelation nicht moeglich."

        # Format presence events (dict format from StateStore)
        lines = []
        for ev in presence_events[-50:]:  # Cap for prompt size
            ts = ev.get("timestamp", "")[:16]
            new_state = ev.get("new_state", {})
            state = new_state.get("status", str(new_state))
            person = ev.get("device_name", "")
            lines.append(f"{ts} — {person}: {state}")

        return "\n".join(lines) if lines else "Keine Anwesenheitsdaten."

    async def _call_router_tier(self, events_text: str, presence_patterns: str = "") -> list[dict]:
        """Call router tier to analyze event patterns. Returns list of pattern dicts."""
        # Set training contextvars for this background task (Issue #34)
        import uuid as _uuid
        from src.training.context import (
            set_request_id,
            set_user,
            set_agent_name,
            set_model_id,
            get_request_id,
            get_user,
            get_agent_name,
        )

        _request_id = f"pat-{_uuid.uuid4().hex[:10]}"
        set_request_id(_request_id)
        set_user("system:pattern_detector")
        set_agent_name("pattern_detector")
        set_model_id("")

        presence_section = ""
        if presence_patterns and "Unzureichende" not in presence_patterns:
            presence_section = f"""
Anwesenheitsdaten der letzten Woche:
{presence_patterns}

Korreliere die Muster mit der Anwesenheit. Routinen sollen Kontextbedingungen enthalten:
- Wer muss zuhause sein (who: Name des Bewohners)?
- An welchen Wochentagen tritt das Muster auf (days: ["Mo", "Di", ...])?
- Gibt es Zusammenhaenge mit Ankommen/Verlassen (trigger_context: "arriving"/"leaving"/"time")?
"""
        prompt = f"""Analysiere die folgenden Hausautomatisierungs-Ereignisse und finde wiederkehrende Verhaltensmuster.

Ereignisse der letzten Woche:
{events_text}
{presence_section}
Gib ein JSON-Array zurück mit Mustern in folgendem Format:
[
  {{
    "pattern": "Kurze Beschreibung des Musters (deutsch)",
    "occurrences": <Anzahl der Beobachtungen>,
    "typical_time": "HH:MM oder Beschreibung des Zeitpunkts",
    "confidence": <0.0-1.0>,
    "who": "Name der Person oder leer",
    "days": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
    "trigger_context": "arriving|leaving|time oder leer"
  }}
]

Gib NUR das JSON-Array zurück, keinen anderen Text."""

        try:
            from strands import Agent  # type: ignore[import-not-found]

            from src.agents import create_model_for_slot
            from src.config import ModelSlot

            model = create_model_for_slot(self._config, ModelSlot.ROUTER_TIER, max_tokens=1024)
            agent = Agent(model=model, tools=[])
            response_text = str(agent(prompt))

            # Extract JSON from response
            start = response_text.find("[")
            end = response_text.rfind("]") + 1
            parsed_patterns: list = []
            if start >= 0 and end > start:
                json_str = response_text[start:end]
                parsed_patterns = json.loads(json_str)
            else:
                logger.warning("PatternDetector: no JSON array in Haiku response")

            # Log to training data (AD-4: explicit logging for background agents)
            if _training_writer is not None:
                import asyncio as _asyncio

                model_id = str(getattr(model, "model_id", model))
                _asyncio.create_task(
                    _training_writer.log_background(
                        {
                            "request_id": get_request_id(),
                            "user": get_user(),
                            "agent": get_agent_name(),
                            "model": model_id,
                            "task": "pattern_detector",
                            "prompt_length": len(prompt),
                            "prompt_preview": prompt[:500],
                            "response": response_text[:5000],
                            "patterns_found": len(parsed_patterns),
                        }
                    )
                )

            return parsed_patterns
        except Exception as e:
            logger.warning("PatternDetector: Haiku call failed: %s", e)
            return []
