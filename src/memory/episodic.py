"""Episodic memory — ChromaDB vector store for events and corrections.

Uses local Ollama nomic-embed-text (768-dim) for embeddings.
Two collections: 'events' (state changes, interactions) and 'corrections' (user feedback).
Falls back to document-only mode if Ollama is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


class EpisodicMemory:
    """ChromaDB-backed episodic memory with Ollama embeddings.

    Do NOT call __init__ directly. Use the classmethod::

        memory = await EpisodicMemory.open("./data/chromadb", ollama_host="http://localhost:11434")
    """

    def __init__(self) -> None:
        # Private — use open() classmethod
        self._client: Any = None
        self._events_col: Any = None
        self._corrections_col: Any = None
        self._embeddings_available: bool = True
        self._embed_failures: int = 0
        self._max_embed_failures: int = 3
        self._ollama_host: str = "http://localhost:11434"

    @classmethod
    async def open(cls, path: str, ollama_host: str = "http://localhost:11434") -> "EpisodicMemory":
        """Open or create a ChromaDB persistent store.

        PersistentClient.__init__ blocks on disk I/O, so it runs in a thread.
        """
        import chromadb

        instance = cls.__new__(cls)
        instance._embeddings_available = True
        instance._embed_failures = 0
        instance._max_embed_failures = 3
        instance._ollama_host = ollama_host

        client = await asyncio.to_thread(chromadb.PersistentClient, path=str(path))
        instance._client = client
        instance._events_col = await asyncio.to_thread(client.get_or_create_collection, "events")
        instance._corrections_col = await asyncio.to_thread(
            client.get_or_create_collection, "corrections"
        )
        logger.info("EpisodicMemory opened at %s", path)
        return instance

    async def close(self) -> None:
        """No-op — PersistentClient has no explicit close."""

    async def _embed(self, text: str) -> list[float] | None:
        """Embed text via Ollama. Returns None on failure.

        Disables embeddings after ``_max_embed_failures`` consecutive failures.
        Re-enables on any success so a temporary Ollama hiccup doesn't
        permanently degrade memory search.
        """
        if not self._embeddings_available:
            return None
        try:
            import ollama

            response = await asyncio.to_thread(ollama.embed, model="nomic-embed-text", input=text)
            self._embed_failures = 0  # reset on success
            return response["embeddings"][0]
        except Exception as e:
            self._embed_failures += 1
            if self._embed_failures >= self._max_embed_failures:
                logger.warning(
                    "Ollama embedding failed %d times: %s — switching to document-only mode",
                    self._embed_failures,
                    e,
                )
                self._embeddings_available = False
            else:
                logger.debug(
                    "Ollama embedding failed (%d/%d): %s",
                    self._embed_failures,
                    self._max_embed_failures,
                    e,
                )
            return None

    async def add_event(self, text: str, metadata: dict | None = None) -> None:
        """Add an event to the events collection."""
        meta = dict(metadata) if metadata else {}
        meta.setdefault("timestamp", datetime.now().isoformat())
        embedding = await self._embed(text)
        doc_id = str(uuid.uuid4())
        if embedding is not None:
            await asyncio.to_thread(
                self._events_col.add,
                ids=[doc_id],
                documents=[text],
                metadatas=[meta],
                embeddings=[embedding],
            )
        else:
            await asyncio.to_thread(
                self._events_col.add,
                ids=[doc_id],
                documents=[text],
                metadatas=[meta],
            )

    async def search(self, query: str, n: int = 5, filter: dict | None = None) -> list[dict]:
        """Search events by semantic similarity."""
        embedding = await self._embed(query)
        kwargs: dict[str, Any] = {"n_results": n}
        if filter:
            kwargs["where"] = filter
        if embedding is not None:
            kwargs["query_embeddings"] = [embedding]
            results = await asyncio.to_thread(self._events_col.query, **kwargs)
        else:
            kwargs["query_texts"] = [query]
            results = await asyncio.to_thread(self._events_col.query, **kwargs)
        return self._parse_results(results)

    async def add_correction(
        self, original: str, correction: str, metadata: dict | None = None
    ) -> None:
        """Store a user correction in the corrections collection."""
        meta = dict(metadata) if metadata else {}
        meta["original"] = original
        meta["correction"] = correction
        meta["timestamp"] = datetime.now().isoformat()
        embedding = await self._embed(f"{original} → {correction}")
        doc_id = str(uuid.uuid4())
        text = f"{original} → {correction}"
        if embedding is not None:
            await asyncio.to_thread(
                self._corrections_col.add,
                ids=[doc_id],
                documents=[text],
                metadatas=[meta],
                embeddings=[embedding],
            )
        else:
            await asyncio.to_thread(
                self._corrections_col.add,
                ids=[doc_id],
                documents=[text],
                metadatas=[meta],
            )

    async def search_corrections(self, query: str, n: int = 3) -> list[dict]:
        """Search corrections by semantic similarity."""
        embedding = await self._embed(query)
        if embedding is not None:
            results = await asyncio.to_thread(
                self._corrections_col.query, query_embeddings=[embedding], n_results=n
            )
        else:
            results = await asyncio.to_thread(
                self._corrections_col.query, query_texts=[query], n_results=n
            )
        return self._parse_results(results)

    async def search_recent(self, hours: int = 168) -> list[dict]:
        """Return events from the last N hours (time-filtered, no query embedding)."""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        try:
            results = await asyncio.to_thread(
                self._events_col.get,
                where={"timestamp": {"$gte": cutoff}},
            )
            # get() returns {"ids": [...], "documents": [...], "metadatas": [...]}
            docs = results.get("documents") or []
            metas = results.get("metadatas") or []
            ids = results.get("ids") or []
            return [{"text": d, "metadata": m or {}, "id": i} for d, m, i in zip(docs, metas, ids)]
        except Exception as e:
            logger.warning("search_recent failed: %s", e)
            return []

    async def get_recent_corrections(self, days: int = 7) -> list[dict]:
        """Return corrections from the last N days."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        try:
            results = await asyncio.to_thread(
                self._corrections_col.get,
                where={"timestamp": {"$gte": cutoff}},
            )
            docs = results.get("documents") or []
            metas = results.get("metadatas") or []
            ids = results.get("ids") or []
            output = []
            for d, m, i in zip(docs, metas, ids):
                meta = m or {}
                output.append(
                    {
                        "original": meta.get("original", ""),
                        "correction": meta.get("correction", ""),
                        "metadata": meta,
                        "timestamp": meta.get("timestamp", ""),
                    }
                )
            return output
        except Exception as e:
            logger.warning("get_recent_corrections failed: %s", e)
            return []

    async def format_similar(self, intent: str, user: str, n: int = 3) -> str:
        """Search events similar to intent and format as German context block."""
        user_filter = {"user": user} if user else None
        results = await self.search(intent, n=n, filter=user_filter)
        if not results:
            return ""
        lines = ["Ähnliche Situationen:"]
        for r in results:
            lines.append(f"- {r['text']}")
        return "\n".join(lines)

    async def format_recent_corrections(self, user: str, n: int = 3) -> str:
        """Format recent corrections as German context block."""
        results = await self.search_corrections(f"Korrekturen von {user}", n=n)
        if not results:
            return ""
        lines = ["Letzte Korrekturen:"]
        for r in results:
            meta = r.get("metadata", {})
            original = meta.get("original", "")
            correction = meta.get("correction", "")
            if original and correction:
                lines.append(f"- '{original}' → '{correction}'")
        return "\n".join(lines) if len(lines) > 1 else ""

    @staticmethod
    def _parse_results(results: dict) -> list[dict]:
        """Parse chromadb query results into list of dicts."""
        docs = (results.get("documents") or [[]])[0]
        metas = (results.get("metadatas") or [[]])[0]
        ids = (results.get("ids") or [[]])[0]
        return [{"text": d, "metadata": m or {}, "id": i} for d, m, i in zip(docs, metas, ids)]

    async def count_events(self) -> int:
        """Return total count of events in the events collection.

        Uses collection.count() which may load all IDs in some chromadb versions.
        Returns 0 on any failure rather than falling back to get() which is worse.
        """
        try:
            return self._events_col.count()
        except Exception:
            return 0

    async def count_recent_corrections(self, days: int = 7) -> int:
        """Return count of corrections in the last N days."""
        corrections = await self.get_recent_corrections(days=days)
        return len(corrections)

    async def get_repeated_corrections(self, days: int = 7, top_n: int = 3) -> list[dict]:
        """Return top N most-repeated corrections in the last N days.

        Groups by the 'original' field and returns sorted by count descending.
        Each entry is: {original: str, correction: str, count: int}.
        """
        corrections = await self.get_recent_corrections(days=days)
        grouped: dict[str, dict] = {}
        for c in corrections:
            original = c.get("original", "")
            if not original:
                continue
            if original not in grouped:
                grouped[original] = {
                    "original": original,
                    "correction": c.get("correction", ""),
                    "count": 0,
                }
            grouped[original]["count"] += 1
        sorted_groups = sorted(grouped.values(), key=lambda x: x["count"], reverse=True)
        return sorted_groups[:top_n]
