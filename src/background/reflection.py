"""Reflection agent — nightly Opus analysis of weekly patterns and corrections.

Runs at 3 AM. Daily for the first 14 days, then weekly on Sundays.
Reviews events, corrections, routines, and health. Produces structured JSON
with routine updates, new suggestions, anomalies, and energy insights.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from src.hal.rules import CustomRule
from src.background.health_snapshot import HealthSnapshot, SnapshotDelta, compute_delta

if TYPE_CHECKING:
    from src.config import BrainConfig
    from src.hal.hal import HAL
    from src.memory.layer import MemoryLayer

logger = logging.getLogger(__name__)

# Module-level training writer — set by bootstrapper when training_data.enabled (AD-4)
_training_writer = None  # TrainingDataWriter | None


def set_training_writer(writer) -> None:
    """Inject the TrainingDataWriter instance for background task logging."""
    global _training_writer
    _training_writer = writer


# Key used to persist the first-run date in procedural memory preferences
_FIRST_RUN_KEY = "reflection_first_run_date"
_DAILY_WINDOW_DAYS = 14


class ReflectionAgent:
    """Nightly Opus reflection agent.

    Reviews the past week's events, corrections, and routines using Opus.
    Applies safe routine updates (confidence/enable changes) and sends
    new suggestions + anomalies to Matrix for user approval.

    Usage::

        agent = ReflectionAgent(memory_layer, bus, matrix, config)
        agent.start()
        # ...
        agent.stop()
    """

    def __init__(
        self,
        memory_layer: "MemoryLayer",
        hal: "HAL",
        matrix: Any,
        config: "BrainConfig",
        rule_engine: Any = None,
    ) -> None:
        self._memory = memory_layer
        self._hal = hal
        self._matrix = matrix
        self._config = config
        self._rule_engine = rule_engine
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Start the nightly reflection loop as an asyncio Task."""
        self._task = asyncio.create_task(self.run(), name="reflection_agent")
        logger.info("ReflectionAgent started")

    def stop(self) -> None:
        """Cancel the reflection loop."""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("ReflectionAgent stopped")

    async def run(self) -> None:
        """Infinite loop: sleep until 3 AM, then reflect."""
        logger.info("ReflectionAgent running (nightly at 03:00)")
        while True:
            try:
                await self._sleep_until(hour=3)
                if await self._should_reflect():
                    await self._reflect()
                else:
                    logger.debug("ReflectionAgent: skipping (not Sunday and past daily window)")
            except asyncio.CancelledError as cancellation:
                raise cancellation
            except Exception as e:
                logger.error("ReflectionAgent error: %s", e)

    async def _sleep_until(self, hour: int) -> None:
        """Sleep until the next occurrence of the given hour (local time)."""
        now = datetime.now()
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        delta = (target - now).total_seconds()
        logger.debug(
            "ReflectionAgent sleeping %.0f seconds until %s", delta, target.strftime("%H:%M")
        )
        await asyncio.sleep(delta)

    async def _should_reflect(self) -> bool:
        """Return True if today is a reflection day.

        Daily for the first 14 days, then weekly on Sundays.
        """
        # Check if we have a first-run date stored
        try:
            results = await self._memory.procedural.search_preferences(_FIRST_RUN_KEY)
            if not results:
                # First time — store the date
                today_str = datetime.now().date().isoformat()
                await self._memory.procedural.add_preference(
                    f"{_FIRST_RUN_KEY}:{today_str}", source="system"
                )
                return True
            # Find the stored date
            fact = results[0].get("fact", "")
            if ":" in fact:
                first_run_str = fact.split(":", 1)[1]
                first_run = datetime.fromisoformat(first_run_str).date()
                days_since = (datetime.now().date() - first_run).days
                if days_since < _DAILY_WINDOW_DAYS:
                    return True
        except Exception as e:
            logger.warning("ReflectionAgent: could not check first-run date: %s", e)

        # Weekly on Sundays (weekday 6)
        return datetime.now().weekday() == 6

    async def _reflect(self) -> None:
        """Run one reflection cycle."""
        logger.info("ReflectionAgent: starting reflection cycle")

        # ── Step 1: Gather context ────────────────────────────────────────────
        events_summary = ""
        corrections_summary = ""
        routines_text = ""
        candidates_text = ""
        health_summary = ""
        health: dict[str, bool] = {}

        try:
            if self._memory.episodic:
                events = await self._memory.episodic.search_recent(hours=168)
                event_lines = []
                for ev in events[:100]:
                    text = ev.get("text", "")
                    meta = ev.get("metadata", {})
                    ts = meta.get("timestamp", "")[:16]
                    event_lines.append(f"[{ts}] {text}")
                events_summary = "\n".join(event_lines) or "Keine Ereignisse."

                corrections = await self._memory.episodic.get_recent_corrections(days=7)
                corr_lines = []
                for c in corrections[:20]:
                    original = c.get("original", "")
                    correction = c.get("correction", "")
                    corr_lines.append(f"- Korrektur: '{original}' → '{correction}'")
                corrections_summary = "\n".join(corr_lines) or "Keine Korrekturen."
            else:
                events_summary = "Episodisches Gedächtnis nicht verfügbar."
                corrections_summary = "Korrekturen nicht verfügbar."
        except Exception as e:
            logger.warning("ReflectionAgent: episodic gather failed: %s", e)
            events_summary = f"Fehler beim Laden der Ereignisse: {e}"
            corrections_summary = "Korrekturen nicht verfügbar."

        try:
            all_routines = await self._memory.procedural.get_routines(enabled_only=False)
            active = [r for r in all_routines if r.get("enabled")]
            candidates = [r for r in all_routines if not r.get("enabled")]
            if active:
                routines_text = "\n".join(
                    f"- {r.get('name', '?')}: {r.get('description', '')} ({r.get('trigger', '')})"
                    for r in active[:20]
                )
            else:
                routines_text = "Keine aktiven Routinen."
            if candidates:
                candidates_text = "\n".join(
                    f"- {r.get('name', '?')}: {r.get('description', '')} "
                    f"(Konfidenz: {r.get('confidence', 0):.0%})"
                    for r in candidates[:10]
                )
            else:
                candidates_text = "Keine Routine-Kandidaten."
        except Exception as e:
            logger.warning("ReflectionAgent: routine gather failed: %s", e)
            routines_text = "Routinen nicht verfügbar."
            candidates_text = "Kandidaten nicht verfügbar."

        try:
            if self._hal:
                for name, platform in self._hal._platforms.items():
                    try:
                        health[name] = await platform.health_check()
                    except Exception:
                        health[name] = False
            health_lines = [f"- {name}: {'OK' if ok else 'FEHLER'}" for name, ok in health.items()]
            health_summary = "\n".join(health_lines) or "Kein Status verfügbar."
        except Exception as e:
            logger.warning("ReflectionAgent: health check failed: %s", e)
            health_summary = f"Gesundheitsstatus nicht verfügbar: {e}"

        # ── Step 2: Check if sufficient data for anomaly analysis ──────────────
        has_enough_data = await self._check_sufficient_data()
        if not has_enough_data:
            logger.info(
                "ReflectionAgent: skipping anomaly analysis (weniger als 3 historische Datenpunkte)"
            )

        # ── Step 2: Call reflection tier ────────────────────────────────────────
        analysis = await self._call_reflection_tier(
            events_summary=events_summary,
            corrections_summary=corrections_summary,
            routines=routines_text,
            candidates=candidates_text,
            health_summary=health_summary,
        )

        if not analysis:
            logger.warning("ReflectionAgent: no analysis produced")
            return

        # ── Step 3: Apply safe routine updates ───────────────────────────────
        for update in analysis.get("routine_updates", []):
            try:
                routine_id = update.get("id")
                if routine_id is not None:
                    await self._memory.procedural.update_routine(
                        routine_id,
                        confidence=update.get("confidence"),
                        enabled=update.get("enabled"),
                    )
                    logger.info("ReflectionAgent: updated routine %d", routine_id)
            except Exception as e:
                logger.warning("ReflectionAgent: routine update failed: %s", e)

        # ── Step 4: Send suggestions to Matrix ────────────────────────────────
        if not self._config.matrix.room_id:
            logger.debug("ReflectionAgent: no Matrix room configured, skipping notifications")
        else:
            for suggestion in analysis.get("new_suggestions", []):
                try:
                    description = suggestion.get("description", str(suggestion))
                    occurrences = suggestion.get("occurrences", "?")
                    confidence = suggestion.get("confidence", 0.0)
                    msg = (
                        f"Vorschlag: {description}\n"
                        f"Basierend auf {occurrences} Beobachtungen "
                        f"(Konfidenz: {confidence:.0%}).\n"
                        "Aktivieren?"
                    )
                    await self._matrix.send(self._config.matrix.room_id, msg)
                except Exception as e:
                    logger.warning("ReflectionAgent: suggestion send failed: %s", e)

            # Parse rule_suggestions from Opus output and persist as unapproved custom rules
            for rule_suggestion in analysis.get("rule_suggestions", []):
                if self._rule_engine is not None:
                    try:
                        custom_rule = CustomRule(
                            name=rule_suggestion.get("name", "unnamed"),
                            device_type=rule_suggestion.get("device_type", ""),
                            sensor_class=rule_suggestion.get("sensor_class", ""),
                            room=rule_suggestion.get("room", ""),
                            state_key=rule_suggestion.get("state_key", ""),
                            operator=rule_suggestion.get("operator", ">"),
                            threshold=float(rule_suggestion.get("threshold", 0)),
                            severity=rule_suggestion.get("severity", "suggestion"),
                            message_template=rule_suggestion.get("message_template", ""),
                            enabled=False,
                            approved=False,
                            source="reflection",
                        )
                        await self._rule_engine.add_custom_rule(custom_rule)
                        if self._config.matrix.room_id:
                            msg = (
                                f"Neue Hausregel vorgeschlagen: {custom_rule.name}\n"
                                f"{custom_rule.message_template}\n"
                                "Genehmigen?"
                            )
                            await self._matrix.send(self._config.matrix.room_id, msg)
                    except Exception as e:
                        logger.warning("ReflectionAgent: rule suggestion failed: %s", e)

            # Anomalies are now routed by severity in _route_anomalies (called after)
            pass  # High-severity anomalies sent in Step 6

        logger.info(
            "ReflectionAgent: reflection complete — %d updates, %d suggestions, %d anomalies",
            len(analysis.get("routine_updates", [])),
            len(analysis.get("new_suggestions", [])),
            len(analysis.get("anomalies", [])),
        )

        # ── Step 5: Write health snapshot (ChromaDB + SQLite) ──────────────────
        snapshot, delta = await self._write_health_snapshot(health, analysis)

        # ── Step 6: Route anomalies by severity ───────────────────────────────
        await self._route_anomalies(analysis.get("anomalies", []))

        # ── Step 7: Morning summary ────────────────────────────────────────────
        if self._config.matrix.room_id and snapshot:
            await self._send_morning_summary(snapshot, delta, analysis.get("anomalies", []))

    async def _check_sufficient_data(self) -> bool:
        """Check if there are enough health snapshots for anomaly analysis (>= 3 required)."""
        if not self._memory.episodic:
            return False
        try:
            # Query health snapshots from episodic memory
            snapshots = await self._memory.episodic.search(
                query="health check snapshot",
                n=5,
                filter={"type": "health_check"},
            )
            count = len(snapshots) if snapshots else 0
            if count < 3:
                logger.info(
                    "ReflectionAgent: only %d health snapshots, skipping anomaly analysis", count
                )
                return False
            return True
        except Exception as e:
            logger.debug("ReflectionAgent: health snapshot query failed: %s", e)
            return False

    async def _gather_snapshot_data(self, health: dict, analysis: dict) -> HealthSnapshot:
        """Gather all data needed for a HealthSnapshot. Each query is fault-tolerant."""
        timestamp = datetime.now().isoformat()

        # Platform health
        platform_health = health

        # Memory counts
        memory_episodic_count = 0
        try:
            if self._memory.episodic:
                memory_episodic_count = await self._memory.episodic.count_events()
        except Exception as e:
            logger.debug("_gather_snapshot_data: count_events failed: %s", e)

        memory_semantic_edges = 0
        try:
            if self._memory.semantic:
                memory_semantic_edges = self._memory.semantic.edge_count()
        except Exception as e:
            logger.debug("_gather_snapshot_data: edge_count failed: %s", e)

        memory_procedural_prefs = 0
        try:
            memory_procedural_prefs = await self._memory.procedural.count_preferences()
        except Exception as e:
            logger.debug("_gather_snapshot_data: count_preferences failed: %s", e)

        # Corrections
        corrections_count_7d = 0
        try:
            if self._memory.episodic:
                corrections_count_7d = await self._memory.episodic.count_recent_corrections()
        except Exception as e:
            logger.debug("_gather_snapshot_data: count_recent_corrections failed: %s", e)

        repeated_corrections: list[dict] = []
        try:
            if self._memory.episodic:
                repeated_corrections = await self._memory.episodic.get_repeated_corrections()
        except Exception as e:
            logger.debug("_gather_snapshot_data: get_repeated_corrections failed: %s", e)

        # Routines
        active_count = 0
        candidate_count = 0
        pattern_hits = 0
        try:
            all_routines = await self._memory.procedural.get_routines(enabled_only=False)
            active_routines_list = [
                r for r in all_routines if r.get("enabled") and r.get("confirmed")
            ]
            active_count = len(active_routines_list)
            candidate_routines_list = [
                r for r in all_routines if r.get("enabled") and not r.get("confirmed")
            ]
            candidate_count = len(candidate_routines_list)
            # Pattern detector hits: candidates created in last 7 days
            cutoff = (datetime.now() - timedelta(days=7)).isoformat()
            pattern_hits = len(
                [r for r in candidate_routines_list if r.get("created_at", "") >= cutoff]
            )
        except Exception as e:
            logger.debug("_gather_snapshot_data: routine query failed: %s", e)

        routine_fires_success = 0
        routine_fires_failure = 0
        try:
            stats = await self._memory.procedural.get_recent_routine_stats()
            routine_fires_success = stats.get("success", 0)
            routine_fires_failure = stats.get("failure", 0)
        except Exception as e:
            logger.debug("_gather_snapshot_data: get_recent_routine_stats failed: %s", e)

        # Top rooms/devices
        top_rooms: list[dict] = []
        try:
            top_rooms = await self._memory.procedural.get_top_rooms()
        except Exception as e:
            logger.debug("_gather_snapshot_data: get_top_rooms failed: %s", e)

        top_devices: list[dict] = []
        try:
            top_devices = await self._memory.procedural.get_top_devices()
        except Exception as e:
            logger.debug("_gather_snapshot_data: get_top_devices failed: %s", e)

        # Anomaly counts by severity
        anomalies = analysis.get("anomalies", [])
        anomalies_found = len(anomalies)
        anomalies_high = sum(1 for a in anomalies if a.get("severity") == "high")
        anomalies_medium = sum(1 for a in anomalies if a.get("severity") == "medium")
        anomalies_low = sum(1 for a in anomalies if a.get("severity") == "low")

        # Insights
        energy_insights = list(analysis.get("energy_insights", []))
        behavioral_changes = list(analysis.get("behavioral_changes", []))

        # Free text suggestion: first item from energy_insights or behavioral_changes
        free_text_suggestion = ""
        if energy_insights:
            free_text_suggestion = energy_insights[0]
        elif behavioral_changes:
            free_text_suggestion = behavioral_changes[0]

        return HealthSnapshot(
            timestamp=timestamp,
            platform_health=platform_health,
            memory_episodic_count=memory_episodic_count,
            memory_semantic_edges=memory_semantic_edges,
            memory_procedural_prefs=memory_procedural_prefs,
            corrections_count_7d=corrections_count_7d,
            repeated_corrections=repeated_corrections,
            active_routines=active_count,
            candidate_routines=candidate_count,
            routine_fires_success=routine_fires_success,
            routine_fires_failure=routine_fires_failure,
            pattern_detector_hits=pattern_hits,
            top_rooms=top_rooms,
            top_devices=top_devices,
            anomalies_found=anomalies_found,
            anomalies_high=anomalies_high,
            anomalies_medium=anomalies_medium,
            anomalies_low=anomalies_low,
            energy_insights=energy_insights,
            behavioral_changes=behavioral_changes,
            free_text_suggestion=free_text_suggestion,
        )

    async def _write_health_snapshot(
        self, health: dict, analysis: dict
    ) -> tuple[HealthSnapshot, SnapshotDelta | None]:
        """Write structured health snapshot to SQLite and ChromaDB.

        Returns (snapshot, delta). ChromaDB write is NOT optional — it is required
        by _check_sufficient_data() which queries filter={"type": "health_check"}.
        Each write (ChromaDB and SQLite) is in a separate try/except so one failure
        does not prevent the other.
        """
        snapshot = await self._gather_snapshot_data(health, analysis)

        integrations_healthy = sum(1 for ok in health.values() if ok)
        integrations_total = len(health)

        # ── ChromaDB write (MUST be preserved — required by _check_sufficient_data) ──
        try:
            if self._memory.episodic:
                await self._memory.episodic.add_event(
                    text=(
                        f"Health check: {integrations_healthy}/{integrations_total} integrations OK, "
                        f"{snapshot.anomalies_found} anomalies found"
                    ),
                    metadata={
                        "type": "health_check",
                        "timestamp": snapshot.timestamp,
                        "integrations_healthy": integrations_healthy,
                        "integrations_total": integrations_total,
                        "anomalies_found": snapshot.anomalies_found,
                    },
                )
                logger.info(
                    "ReflectionAgent: ChromaDB health snapshot written "
                    "(%d/%d integrations, %d anomalies)",
                    integrations_healthy,
                    integrations_total,
                    snapshot.anomalies_found,
                )
        except Exception as e:
            logger.warning("ReflectionAgent: ChromaDB health snapshot write failed: %s", e)

        # ── SQLite write (separate try/except — independent of ChromaDB) ──────────
        try:
            await self._memory.procedural.add_health_snapshot(
                snapshot.timestamp, snapshot.model_dump_json()
            )
            await self._memory.procedural.prune_health_snapshots()
            logger.info("ReflectionAgent: SQLite health snapshot written at %s", snapshot.timestamp)
        except Exception as e:
            logger.warning("ReflectionAgent: SQLite health snapshot write failed: %s", e)

        # ── Delta computation ─────────────────────────────────────────────────────
        delta: SnapshotDelta | None = None
        try:
            rows = await self._memory.procedural.get_latest_health_snapshot(n=2)
            if len(rows) >= 2:
                previous = HealthSnapshot.model_validate_json(rows[1]["payload"])
                delta = compute_delta(snapshot, previous)
        except Exception as e:
            logger.debug("ReflectionAgent: delta computation failed: %s", e)

        return snapshot, delta

    async def _send_morning_summary(
        self,
        snapshot: HealthSnapshot,
        delta: SnapshotDelta | None,
        anomalies: list[dict],
    ) -> None:
        """Send a short German first-person morning summary to Matrix."""
        lines = ["Guten Morgen! Ich habe heute Nacht reflektiert:"]

        # Platform health
        total_platforms = len(snapshot.platform_health)
        healthy_platforms = sum(1 for ok in snapshot.platform_health.values() if ok)
        if total_platforms == 0:
            lines.append("- Keine Plattformen konfiguriert")
        elif healthy_platforms == total_platforms:
            lines.append(f"- Alle {total_platforms} Plattformen gesund")
        else:
            unhealthy = total_platforms - healthy_platforms
            lines.append(
                f"- {healthy_platforms}/{total_platforms} Plattformen gesund ({unhealthy} Problem(e))"
            )

        # Memory growth
        episodic_str = str(snapshot.memory_episodic_count)
        if delta and delta.memory_episodic_count is not None:
            sign = "+" if delta.memory_episodic_count >= 0 else ""
            episodic_str += f" ({sign}{delta.memory_episodic_count} seit letzter Woche)"
        lines.append(f"- {episodic_str} Erinnerungen")

        # Corrections
        lines.append(f"- {snapshot.corrections_count_7d} Korrekturen diese Woche")

        # Routine success rate
        total_fires = snapshot.routine_fires_success + snapshot.routine_fires_failure
        if total_fires > 0:
            lines.append(
                f"- {total_fires} Routinen ausgefuehrt, {snapshot.routine_fires_success} erfolgreich"
            )

        # Anomaly summary with medium descriptions
        if snapshot.anomalies_found > 0:
            if snapshot.anomalies_high > 0:
                lines.append(f"- {snapshot.anomalies_high} Anomalie(n) (hoch) — bereits gemeldet")
            if snapshot.anomalies_medium > 0:
                medium_anomalies = [a for a in anomalies if a.get("severity") == "medium"]
                for a in medium_anomalies:
                    desc = a.get("description", "?")
                    lines.append(f"- 1 Anomalie (mittel): {desc}")
            if snapshot.anomalies_low > 0:
                lines.append(
                    f"- {snapshot.anomalies_low} Anomalie(n) (niedrig) — nur protokolliert"
                )
        else:
            lines.append("- Keine Anomalien gefunden")

        # Free text suggestion
        if snapshot.free_text_suggestion:
            lines.append(f"- Vorschlag: {snapshot.free_text_suggestion}")

        msg = "\n".join(lines)
        try:
            await self._matrix.send(self._config.matrix.room_id, msg)
            logger.info("ReflectionAgent: morning summary sent to Matrix")
        except Exception as e:
            logger.warning("ReflectionAgent: morning summary send failed: %s", e)

    async def _route_anomalies(self, anomalies: list[dict]) -> None:
        """Route anomalies by severity. High → immediate Matrix, medium → log, low → log only.

        Backward compatible: anomalies without severity field are sent to Matrix (legacy format).
        """
        if not anomalies:
            return
        if not self._config.matrix.room_id:
            return

        for anomaly in anomalies:
            try:
                description = anomaly.get("description", str(anomaly))
                severity = anomaly.get("severity")  # None if not present (legacy)

                if severity == "medium":
                    # Medium anomalies logged, not immediately sent
                    logger.info("ReflectionAgent: MEDIUM anomaly (weekly summary): %s", description)
                elif severity == "low":
                    # Low severity: log only
                    logger.debug("ReflectionAgent: LOW anomaly (logged only): %s", description)
                else:
                    # "high" or legacy format (no severity) → send to Matrix immediately
                    if severity == "high":
                        logger.warning("ReflectionAgent: HIGH anomaly: %s", description)
                    msg = self._format_anomaly_notification(anomaly)
                    await self._matrix.send(self._config.matrix.room_id, msg)
            except Exception as e:
                logger.warning("ReflectionAgent: anomaly routing failed: %s", e)

    def _format_anomaly_notification(self, anomaly: dict) -> str:
        """Format an anomaly as a German notification with severity prefix."""
        severity = anomaly.get("severity", "low")
        description = anomaly.get("description", "?")
        evidence = anomaly.get("evidence", "")
        category = anomaly.get("category", "")

        prefix = "[Warnung]" if severity == "high" else "[Hinweis]"
        category_map = {
            "device": "Gerät",
            "behavior": "Verhalten",
            "energy": "Energie",
            "security": "Sicherheit",
        }
        cat_str = category_map.get(category, category)

        msg = f"{prefix} {cat_str}-Anomalie: {description}"
        if evidence:
            msg += f"\nBelege: {evidence}"
        return msg

    async def _call_reflection_tier(
        self,
        events_summary: str,
        corrections_summary: str,
        routines: str,
        candidates: str,
        health_summary: str,
    ) -> dict[str, Any]:
        """Call reflection tier with the reflection prompt. Returns parsed JSON dict."""
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

        _request_id = f"ref-{_uuid.uuid4().hex[:10]}"
        set_request_id(_request_id)
        set_user("system:reflection_agent")
        set_agent_name("reflection")
        set_model_id(self._config.models.reflection.model_id)

        from src.agents.prompts import REFLECTION_PROMPT

        prompt = REFLECTION_PROMPT.format(
            events_summary=events_summary,
            corrections_summary=corrections_summary,
            routines=routines,
            candidates=candidates,
            health_summary=health_summary,
        )

        try:
            from strands import Agent  # type: ignore[import-not-found]

            from src.agents import create_model_for_slot
            from src.config import ModelSlot

            model = create_model_for_slot(self._config, ModelSlot.REFLECTION_TIER, max_tokens=4096)
            agent = Agent(model=model, tools=[])
            response_text = str(agent(prompt))

            # Extract JSON from response
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            parsed: dict = {}
            if start >= 0 and end > start:
                parsed = json.loads(response_text[start:end])
            else:
                logger.warning("ReflectionAgent: no JSON in reflection tier response")

            # Log to training data (AD-4: explicit logging for background agents)
            if _training_writer is not None:
                import asyncio as _asyncio

                model_id = self._config.models.reflection.model_id
                analysis_keys = list(parsed.keys()) if parsed else []
                _asyncio.create_task(
                    _training_writer.log_background(
                        {
                            "request_id": get_request_id(),
                            "user": get_user(),
                            "agent": get_agent_name(),
                            "model": model_id,
                            "task": "reflection",
                            "prompt_length": len(prompt),
                            "prompt_preview": prompt[:500],
                            "response": response_text[:5000],
                            "analysis_keys_found": analysis_keys,
                        }
                    )
                )

            return parsed
        except Exception as e:
            logger.warning("ReflectionAgent: reflection tier call failed: %s", e)
            return {}
