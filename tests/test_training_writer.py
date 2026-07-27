"""Tests for TrainingDataWriter — JSONL append, locking, and concurrent safety."""

from __future__ import annotations

import asyncio
import json

import pytest

from src.training.writer import TrainingDataWriter


def test_writer_creates_output_dir(tmp_path):
    """TrainingDataWriter creates the output directory on init."""
    output_dir = tmp_path / "training_out"
    TrainingDataWriter(str(output_dir))
    assert output_dir.exists()
    assert output_dir.is_dir()


def test_writer_pre_creates_locks(tmp_path):
    """TrainingDataWriter pre-creates locks for all known filenames."""
    writer = TrainingDataWriter(str(tmp_path))
    assert "routing.jsonl" in writer._locks
    assert "interactions.jsonl" in writer._locks
    assert "background.jsonl" in writer._locks
    assert all(isinstance(lock, asyncio.Lock) for lock in writer._locks.values())


@pytest.mark.asyncio
async def test_log_routing_creates_file(tmp_path):
    """log_routing creates routing.jsonl with a valid JSON line."""
    writer = TrainingDataWriter(str(tmp_path))
    await writer.log_routing({"source": "agent", "input_text": "Licht an"})

    routing_file = tmp_path / "routing.jsonl"
    assert routing_file.exists()

    lines = routing_file.read_text().strip().split("\n")
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["source"] == "agent"
    assert record["input_text"] == "Licht an"
    assert "timestamp" in record


@pytest.mark.asyncio
async def test_log_interaction_creates_file(tmp_path):
    """log_interaction creates interactions.jsonl with a valid JSON line."""
    writer = TrainingDataWriter(str(tmp_path))
    await writer.log_interaction({"agent": "comfort", "tool_calls": []})

    interactions_file = tmp_path / "interactions.jsonl"
    assert interactions_file.exists()
    record = json.loads(interactions_file.read_text().strip())
    assert record["agent"] == "comfort"
    assert "timestamp" in record


@pytest.mark.asyncio
async def test_log_background_creates_file(tmp_path):
    """log_background creates background.jsonl with a valid JSON line."""
    writer = TrainingDataWriter(str(tmp_path))
    await writer.log_background({"task": "pattern_detector", "response": "[]"})

    bg_file = tmp_path / "background.jsonl"
    assert bg_file.exists()
    record = json.loads(bg_file.read_text().strip())
    assert record["task"] == "pattern_detector"


@pytest.mark.asyncio
async def test_concurrent_writes_no_interleaving(tmp_path):
    """Concurrent writes to the same file are serialized by asyncio.Lock.

    Launches 3 concurrent log_routing tasks and verifies all 3 records are
    written as separate valid JSON lines without interleaving.
    """
    writer = TrainingDataWriter(str(tmp_path))

    tasks = [
        asyncio.create_task(
            writer.log_routing({"source": "agent", "seq": i, "input_text": "x" * 100})
        )
        for i in range(3)
    ]
    await asyncio.gather(*tasks)

    routing_file = tmp_path / "routing.jsonl"
    assert routing_file.exists()

    raw = routing_file.read_text().strip()
    lines = raw.split("\n")
    assert len(lines) == 3, f"Expected 3 lines, got {len(lines)}: {raw!r}"

    seqs = set()
    for line in lines:
        record = json.loads(line)  # Raises if malformed/interleaved
        seqs.add(record["seq"])

    assert seqs == {0, 1, 2}, f"Expected seq 0,1,2 got {seqs}"


@pytest.mark.asyncio
async def test_timestamp_added_automatically(tmp_path):
    """Each record gets a timestamp field added automatically."""
    writer = TrainingDataWriter(str(tmp_path))
    await writer.log_routing({"source": "offline"})

    routing_file = tmp_path / "routing.jsonl"
    record = json.loads(routing_file.read_text().strip())
    assert "timestamp" in record
    # ISO format: starts with a 4-digit year
    assert record["timestamp"][:4].isdigit()


@pytest.mark.asyncio
async def test_existing_timestamp_not_overwritten(tmp_path):
    """If caller provides a timestamp, it is not overwritten."""
    writer = TrainingDataWriter(str(tmp_path))
    await writer.log_routing({"source": "offline", "timestamp": "2026-01-01T00:00:00"})

    routing_file = tmp_path / "routing.jsonl"
    record = json.loads(routing_file.read_text().strip())
    assert record["timestamp"] == "2026-01-01T00:00:00"


@pytest.mark.asyncio
async def test_multiple_records_appended(tmp_path):
    """Multiple sequential writes produce multiple lines in the same file."""
    writer = TrainingDataWriter(str(tmp_path))
    for i in range(5):
        await writer.log_routing({"seq": i})

    routing_file = tmp_path / "routing.jsonl"
    lines = routing_file.read_text().strip().split("\n")
    assert len(lines) == 5
    seqs = [json.loads(line)["seq"] for line in lines]
    assert seqs == [0, 1, 2, 3, 4]
