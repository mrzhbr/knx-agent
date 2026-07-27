"""TrainingDataWriter — non-blocking JSONL append for LLM interaction logging.

Writes three JSONL files:
  - routing.jsonl      — router classification records (input, raw response, parsed result)
  - interactions.jsonl — per-invocation tool call chains + final response
  - background.jsonl   — PatternDetector and ReflectionAgent LLM calls

Design:
  - asyncio.Lock per file serializes concurrent writers (AD-14)
  - asyncio.to_thread() for file I/O so writes never block the event loop
  - Timestamp added automatically to every record
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_KNOWN_FILES = ("routing.jsonl", "interactions.jsonl", "background.jsonl")


class TrainingDataWriter:
    """Appends structured records to JSONL files for LLM fine-tuning data collection.

    Thread-safe: uses one asyncio.Lock per output file, pre-created in __init__
    to avoid lazy-creation race conditions under concurrent invocations.

    Usage::

        writer = TrainingDataWriter("data/training")
        await writer.log_routing({"source": "agent", "input_text": "...", ...})
        await writer.log_interaction({"agent": "comfort", "tool_calls": [...], ...})
        await writer.log_background({"task": "pattern_detector", "response": "...", ...})
    """

    def __init__(self, output_dir: str) -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        # Pre-create one lock per known file to avoid lazy-creation race conditions (AD-14)
        self._locks: dict[str, asyncio.Lock] = {name: asyncio.Lock() for name in _KNOWN_FILES}
        logger.info("TrainingDataWriter initialized at %s", self._output_dir)

    async def log_routing(self, data: dict) -> None:
        """Append a routing classification record to routing.jsonl."""
        await self._append("routing.jsonl", data)

    async def log_interaction(self, data: dict) -> None:
        """Append an agent interaction record to interactions.jsonl."""
        await self._append("interactions.jsonl", data)

    async def log_background(self, data: dict) -> None:
        """Append a background task LLM record to background.jsonl."""
        await self._append("background.jsonl", data)

    async def _append(self, filename: str, data: dict) -> None:
        """Append a single JSON line to the named file.

        1. Adds ISO timestamp to the record.
        2. Serializes to a single JSON line.
        3. Acquires the per-file asyncio.Lock (pre-created in __init__).
        4. Delegates blocking file I/O to asyncio.to_thread().
        """
        record = dict(data)
        record.setdefault("timestamp", datetime.now().isoformat())

        try:
            line = json.dumps(record, ensure_ascii=False) + "\n"
        except (TypeError, ValueError) as e:
            logger.warning("TrainingDataWriter: JSON serialization failed for %s: %s", filename, e)
            return

        lock = self._locks.get(filename)
        if lock is None:
            # Fallback for unexpected filenames — create lock lazily (should not happen)
            logger.warning("TrainingDataWriter: no pre-created lock for %s, creating", filename)
            lock = asyncio.Lock()
            self._locks[filename] = lock

        filepath = self._output_dir / filename
        async with lock:
            try:
                await asyncio.to_thread(self._write_sync, filepath, line)
            except Exception as e:
                logger.warning("TrainingDataWriter: write failed for %s: %s", filename, e)

    def _write_sync(self, filepath: Path, line: str) -> None:
        """Synchronous file append — called via asyncio.to_thread()."""
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(line)
