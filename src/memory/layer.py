"""MemoryLayer — unified interface over all three memory stores.

Assembles context from episodic (ChromaDB), semantic (networkx graph),
and procedural (SQLite) memory for LLM system prompt injection.
Owns the lifecycle of all three stores.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from src.memory.episodic import EpisodicMemory
from src.memory.procedural import ProceduralMemory
from src.memory.semantic import SemanticMemory

logger = logging.getLogger(__name__)


class MemoryLayer:
    """Unified memory interface — owns all three stores.

    Usage::

        memory = MemoryLayer(db_path="data/brain.db", chromadb_path="./data/chromadb",
                             ollama_host="http://localhost:11434")
        await memory.open()
        context = await memory.build_context(user="Max", intent="Licht einschalten")
        await memory.close()
    """

    def __init__(
        self,
        db_path: str | Path = "data/brain.db",
        chromadb_path: str | Path = "./data/chromadb",
        ollama_host: str = "http://localhost:11434",
    ) -> None:
        self._db_path = str(db_path)
        self._chromadb_path = str(chromadb_path)
        self._ollama_host = ollama_host
        self.procedural: ProceduralMemory = ProceduralMemory(self._db_path)
        self.semantic: SemanticMemory = SemanticMemory()
        self.episodic: EpisodicMemory | None = None

    async def open(self) -> None:
        """Open all three stores in correct order.

        1. ProceduralMemory opens its SQLite connection
        2. SemanticMemory shares that connection
        3. EpisodicMemory opens ChromaDB in a thread
        """
        await self.procedural.open()
        await self.semantic.open(self.procedural._db)  # type: ignore[arg-type]
        try:
            self.episodic = await EpisodicMemory.open(
                self._chromadb_path, ollama_host=self._ollama_host
            )
        except Exception as e:
            logger.warning("EpisodicMemory failed to open: %s — episodic memory disabled", e)
            self.episodic = None
        logger.info("MemoryLayer opened (procedural + semantic + episodic)")

    async def close(self) -> None:
        """Close all stores."""
        if self.episodic:
            await self.episodic.close()
        await self.semantic.close()
        await self.procedural.close()
        logger.info("MemoryLayer closed")

    async def _fetch_similar(self, intent: str, user: str) -> str:
        """Fetch similar past situations from episodic memory."""
        if self.episodic is None:
            return ""
        try:
            result = await self.episodic.format_similar(intent, user, n=3)
            return result or ""
        except Exception as e:
            logger.debug("Episodic similar search failed: %s", e)
            return ""

    async def _fetch_preferences(self, user: str) -> str:
        """Fetch user preferences from semantic memory."""
        try:
            result = await self.semantic.format_preferences(user)
            return result or ""
        except Exception as e:
            logger.debug("Semantic preferences failed: %s", e)
            return ""

    async def _fetch_routines(self) -> str:
        """Fetch active routines from procedural memory."""
        try:
            routines = await self.procedural.get_routines(enabled_only=True)
            if not routines:
                return ""
            lines = ["Meine aktiven Routinen:"]
            for r in routines:
                name = r.get("name", "")
                desc = r.get("description", "")
                trigger = r.get("trigger", "")
                lines.append(f"- {name}: {desc} ({trigger})")
            return "\n".join(lines)
        except Exception as e:
            logger.debug("Procedural routines failed: %s", e)
            return ""

    async def _fetch_corrections(self, user: str) -> str:
        """Fetch recent corrections from episodic memory."""
        if self.episodic is None:
            return ""
        try:
            result = await self.episodic.format_recent_corrections(user, n=3)
            return result or ""
        except Exception as e:
            logger.debug("Episodic corrections failed: %s", e)
            return ""

    async def build_context(self, user: str, intent: str) -> str:
        """Assemble memory context string for LLM system prompt injection.

        Returns a multi-section German string. Empty sections are omitted.
        Returns empty string if all sections are empty.
        """
        results = await asyncio.gather(
            self._fetch_similar(intent, user),
            self._fetch_preferences(user),
            self._fetch_routines(),
            self._fetch_corrections(user),
        )
        sections = [r for r in results if r]
        return "\n\n".join(sections)

    async def log_interaction(
        self,
        user: str,
        text: str,
        response: str,
        tool_calls: Any = None,
    ) -> None:
        """Log an interaction to all relevant stores.

        Writes to episodic as event. tool_calls is optional — pass None
        when tool call history is not available.
        """
        event_text = f"[{user}] {text} → {response}"
        metadata: dict[str, Any] = {"user": user, "type": "interaction"}

        # Episodic: log as event
        if self.episodic:
            try:
                await self.episodic.add_event(event_text, metadata=metadata)
            except Exception as e:
                logger.debug("Episodic log_interaction failed: %s", e)

        # Semantic: strengthen edges for mentioned facts (simple heuristic)
        # If response contains "gespeichert" (a preference was stored), strengthen "stated" edge
        try:
            if "gespeichert" in response.lower() and text:
                await self.semantic.add_edge(user, "stated", text[:100], source="interaction")
        except Exception as e:
            logger.debug("Semantic edge strengthen failed: %s", e)

        # Procedural: log state change
        try:
            await self.procedural.log_state_change(
                uid=f"interaction_{user}",
                new_value=text[:200],
                room=None,
                device=f"Benutzer {user}",
                source="matrix",
            )
        except Exception as e:
            logger.debug("Procedural log_interaction failed: %s", e)
