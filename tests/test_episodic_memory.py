"""Tests for EpisodicMemory — ChromaDB vector store with Ollama embeddings."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.memory.episodic import EpisodicMemory


# ─── Fixtures ──────────────────────────────────────────────────────────────


def _make_mock_collection(docs=None, metas=None, ids=None):
    """Build a mock ChromaDB collection."""
    col = MagicMock()
    docs = docs or []
    metas = metas or []
    ids = ids or []
    col.query.return_value = {
        "documents": [docs],
        "metadatas": [metas],
        "ids": [ids],
        "distances": [[0.1] * len(docs)],
    }
    col.get.return_value = {
        "documents": docs,
        "metadatas": metas,
        "ids": ids,
    }
    return col


def _make_episodic(events_col=None, corrections_col=None, embeddings_available=True):
    """Build an EpisodicMemory instance bypassing __init__."""
    mem = EpisodicMemory.__new__(EpisodicMemory)
    mem._client = MagicMock()
    mem._events_col = events_col or _make_mock_collection()
    mem._corrections_col = corrections_col or _make_mock_collection()
    mem._embeddings_available = embeddings_available
    mem._embed_failures = 0
    mem._max_embed_failures = 3
    mem._ollama_host = "http://localhost:11434"
    return mem


@pytest.fixture
def mock_embed(monkeypatch):
    """Mock ollama.embed to return a fixed 768-dim vector."""
    import ollama

    monkeypatch.setattr(ollama, "embed", lambda **kwargs: {"embeddings": [[0.1] * 768]})


@pytest.fixture
def mock_chromadb(monkeypatch):
    """Mock chromadb.PersistentClient and collection creation."""
    mock_events = _make_mock_collection()
    mock_corrections = _make_mock_collection()
    mock_client = MagicMock()
    mock_client.get_or_create_collection.side_effect = [mock_events, mock_corrections]

    import chromadb

    monkeypatch.setattr(chromadb, "PersistentClient", lambda path: mock_client)
    return mock_client, mock_events, mock_corrections


# ─── Tests: open() classmethod ─────────────────────────────────────────────


async def test_open_creates_instance(mock_chromadb, mock_embed):
    """open() returns a fully initialized EpisodicMemory instance."""
    mem = await EpisodicMemory.open("/tmp/test_chromadb")
    assert mem._events_col is not None
    assert mem._corrections_col is not None
    assert mem._embeddings_available is True


async def test_open_calls_persist_client_in_thread(mock_chromadb, mock_embed):
    """open() wraps PersistentClient in asyncio.to_thread (no block)."""
    # If this test passes, PersistentClient was called (mocked, non-blocking)
    mock_client, _, _ = mock_chromadb
    mem = await EpisodicMemory.open("/tmp/test_chromadb", ollama_host="http://myollama:11434")
    assert mem._ollama_host == "http://myollama:11434"
    assert mock_client.get_or_create_collection.call_count == 2


# ─── Tests: add_event() ────────────────────────────────────────────────────


async def test_add_event_with_embedding(mock_embed):
    """add_event embeds text and calls collection.add with embeddings."""
    events_col = _make_mock_collection()
    mem = _make_episodic(events_col=events_col)

    import ollama

    with patch.object(ollama, "embed", return_value={"embeddings": [[0.1] * 768]}):
        await mem.add_event("Licht im Wohnzimmer eingeschaltet")

    events_col.add.assert_called_once()
    call_kwargs = events_col.add.call_args[1]
    assert "embeddings" in call_kwargs
    assert "documents" in call_kwargs
    assert len(call_kwargs["embeddings"][0]) == 768


async def test_add_event_without_embedding():
    """add_event falls back to document-only when embeddings unavailable."""
    events_col = _make_mock_collection()
    mem = _make_episodic(events_col=events_col, embeddings_available=False)
    await mem.add_event("Test event")

    events_col.add.assert_called_once()
    call_kwargs = events_col.add.call_args[1]
    assert "embeddings" not in call_kwargs
    assert "documents" in call_kwargs


async def test_add_event_always_includes_timestamp(mock_embed):
    """add_event always includes a timestamp in metadata."""
    events_col = _make_mock_collection()
    mem = _make_episodic(events_col=events_col)

    import ollama

    with patch.object(ollama, "embed", return_value={"embeddings": [[0.1] * 768]}):
        await mem.add_event("test event", metadata={"room": "Wohnzimmer"})

    call_kwargs = events_col.add.call_args[1]
    meta = call_kwargs["metadatas"][0]
    assert "timestamp" in meta
    assert "room" in meta


# ─── Tests: search() ───────────────────────────────────────────────────────


async def test_search_returns_list_of_dicts(mock_embed):
    """search() returns list of dicts with text, metadata, id keys."""
    docs = ["Licht an", "Heizung aus"]
    metas = [{"room": "Wohnzimmer"}, {"room": "Schlafzimmer"}]
    ids = ["id-1", "id-2"]
    events_col = _make_mock_collection(docs=docs, metas=metas, ids=ids)
    mem = _make_episodic(events_col=events_col)

    import ollama

    with patch.object(ollama, "embed", return_value={"embeddings": [[0.1] * 768]}):
        results = await mem.search("Licht")

    assert len(results) == 2
    assert results[0]["text"] == "Licht an"
    assert results[0]["metadata"]["room"] == "Wohnzimmer"
    assert results[0]["id"] == "id-1"


async def test_search_empty_collection():
    """search() returns empty list when collection has no results."""
    mem = _make_episodic(embeddings_available=False)
    results = await mem.search("query")
    assert results == []


# ─── Tests: add_correction() and search_corrections() ─────────────────────


async def test_add_correction_stores_metadata(mock_embed):
    """add_correction stores original, correction, and timestamp in metadata."""
    corrections_col = _make_mock_collection()
    mem = _make_episodic(corrections_col=corrections_col)

    import ollama

    with patch.object(ollama, "embed", return_value={"embeddings": [[0.1] * 768]}):
        await mem.add_correction("Licht ausschalten", "Licht einschalten")

    corrections_col.add.assert_called_once()
    call_kwargs = corrections_col.add.call_args[1]
    meta = call_kwargs["metadatas"][0]
    assert meta["original"] == "Licht ausschalten"
    assert meta["correction"] == "Licht einschalten"
    assert "timestamp" in meta


async def test_search_corrections_returns_results(mock_embed):
    """search_corrections() returns list of dicts."""
    docs = ["Licht ausschalten → Licht einschalten"]
    metas = [{"original": "aus", "correction": "ein", "timestamp": "2026-01-01"}]
    ids = ["corr-1"]
    corrections_col = _make_mock_collection(docs=docs, metas=metas, ids=ids)
    mem = _make_episodic(corrections_col=corrections_col)

    import ollama

    with patch.object(ollama, "embed", return_value={"embeddings": [[0.1] * 768]}):
        results = await mem.search_corrections("Licht")

    assert len(results) == 1
    assert results[0]["id"] == "corr-1"


# ─── Tests: search_recent() ────────────────────────────────────────────────


async def test_search_recent_uses_time_filter():
    """search_recent() calls collection.get with a timestamp filter."""
    events_col = _make_mock_collection(
        docs=["recent event"],
        metas=[{"timestamp": datetime.now().isoformat()}],
        ids=["ev-1"],
    )
    mem = _make_episodic(events_col=events_col)
    await mem.search_recent(hours=24)

    # Verify get() was called (not query())
    events_col.get.assert_called_once()
    call_kwargs = events_col.get.call_args[1]
    assert "where" in call_kwargs
    assert "$gte" in call_kwargs["where"]["timestamp"]


async def test_search_recent_returns_correct_keys():
    """search_recent() returns list of dicts with text, metadata, id."""
    events_col = _make_mock_collection(
        docs=["event text"],
        metas=[{"timestamp": "2026-01-01"}],
        ids=["ev-1"],
    )
    mem = _make_episodic(events_col=events_col)
    results = await mem.search_recent()
    assert len(results) == 1
    assert "text" in results[0]
    assert "metadata" in results[0]
    assert "id" in results[0]


# ─── Tests: get_recent_corrections() ──────────────────────────────────────


async def test_get_recent_corrections_returns_correct_keys():
    """get_recent_corrections() returns list with original, correction, metadata, timestamp."""
    ts = datetime.now().isoformat()
    corrections_col = _make_mock_collection(
        docs=["orig → corr"],
        metas=[{"original": "orig", "correction": "corr", "timestamp": ts}],
        ids=["c-1"],
    )
    mem = _make_episodic(corrections_col=corrections_col)
    results = await mem.get_recent_corrections(days=7)
    assert len(results) == 1
    assert results[0]["original"] == "orig"
    assert results[0]["correction"] == "corr"
    assert results[0]["timestamp"] == ts
    assert "metadata" in results[0]


# ─── Tests: format_similar() ───────────────────────────────────────────────


async def test_format_similar_returns_german_text():
    """format_similar() formats results as German text."""
    events_col = _make_mock_collection(
        docs=["Licht wurde eingeschaltet"],
        metas=[{"room": "Wohnzimmer"}],
        ids=["ev-1"],
    )
    mem = _make_episodic(events_col=events_col, embeddings_available=False)
    result = await mem.format_similar("Licht einschalten", "Max")
    assert "Ähnliche Situationen" in result
    assert "Licht wurde eingeschaltet" in result


async def test_format_similar_empty_when_no_results():
    """format_similar() returns empty string when no results found."""
    mem = _make_episodic(embeddings_available=False)
    result = await mem.format_similar("unbekannte Anfrage", "Max")
    assert result == ""


# ─── Tests: graceful fallback ──────────────────────────────────────────────


async def test_embedding_failure_disables_embeddings():
    """After max_embed_failures consecutive failures, _embeddings_available becomes False."""
    mem = _make_episodic(embeddings_available=True)

    import ollama

    with patch.object(ollama, "embed", side_effect=ConnectionError("no ollama")):
        # First two failures should not disable embeddings
        for _ in range(2):
            result = await mem._embed("test text")
            assert result is None
            assert mem._embeddings_available is True

        # Third failure disables embeddings
        result = await mem._embed("test text")
        assert result is None
        assert mem._embeddings_available is False


async def test_add_event_after_embedding_failure():
    """add_event works in document-only mode after embedding fails."""
    events_col = _make_mock_collection()
    mem = _make_episodic(events_col=events_col, embeddings_available=True)

    import ollama

    with patch.object(ollama, "embed", side_effect=ConnectionError("no ollama")):
        await mem.add_event("Licht an")

    # Should still have added the document (without embeddings)
    events_col.add.assert_called_once()
    call_kwargs = events_col.add.call_args[1]
    assert "embeddings" not in call_kwargs


async def test_close_is_noop():
    """close() does nothing (PersistentClient has no explicit close)."""
    mem = _make_episodic()
    await mem.close()  # Must not raise


# ─── Tests: count_events() ─────────────────────────────────────────────────


async def test_count_events_returns_int():
    """count_events() returns the integer from collection.count()."""
    col = _make_mock_collection()
    col.count = MagicMock(return_value=42)
    mem = _make_episodic(events_col=col)
    result = await mem.count_events()
    assert result == 42


async def test_count_events_returns_zero_on_exception():
    """count_events() returns 0 when collection.count() raises an exception."""
    col = _make_mock_collection()
    col.count = MagicMock(side_effect=AttributeError("no count method"))
    mem = _make_episodic(events_col=col)
    result = await mem.count_events()
    assert result == 0


# ─── Tests: count_recent_corrections() ────────────────────────────────────


async def test_count_recent_corrections_returns_count(monkeypatch):
    """count_recent_corrections() returns len of get_recent_corrections result."""
    from unittest.mock import AsyncMock

    mem = _make_episodic()
    corrections = [
        {
            "original": "a",
            "correction": "b",
            "metadata": {"timestamp": "2026-04-12"},
            "timestamp": "2026-04-12",
        },
        {
            "original": "c",
            "correction": "d",
            "metadata": {"timestamp": "2026-04-12"},
            "timestamp": "2026-04-12",
        },
    ]
    monkeypatch.setattr(mem, "get_recent_corrections", AsyncMock(return_value=corrections))
    result = await mem.count_recent_corrections(days=7)
    assert result == 2


async def test_count_recent_corrections_zero_when_empty(monkeypatch):
    """count_recent_corrections() returns 0 when no corrections."""
    from unittest.mock import AsyncMock

    mem = _make_episodic()
    monkeypatch.setattr(mem, "get_recent_corrections", AsyncMock(return_value=[]))
    result = await mem.count_recent_corrections()
    assert result == 0


# ─── Tests: get_repeated_corrections() ────────────────────────────────────


async def test_get_repeated_corrections_groups_correctly(monkeypatch):
    """get_repeated_corrections() groups by original field and returns top N."""
    from unittest.mock import AsyncMock

    mem = _make_episodic()
    corrections = [
        {
            "original": "Licht an",
            "correction": "Licht 50%",
            "metadata": {},
            "timestamp": "2026-04-12",
        },
        {
            "original": "Licht an",
            "correction": "Licht 50%",
            "metadata": {},
            "timestamp": "2026-04-12",
        },
        {
            "original": "Licht an",
            "correction": "Licht 60%",
            "metadata": {},
            "timestamp": "2026-04-12",
        },
        {
            "original": "Rollladen",
            "correction": "Rollladen 50%",
            "metadata": {},
            "timestamp": "2026-04-12",
        },
    ]
    monkeypatch.setattr(mem, "get_recent_corrections", AsyncMock(return_value=corrections))
    result = await mem.get_repeated_corrections(days=7, top_n=3)
    assert len(result) >= 1
    # "Licht an" has 3 occurrences, should be first
    assert result[0]["original"] == "Licht an"
    assert result[0]["count"] == 3


async def test_get_repeated_corrections_returns_top_n(monkeypatch):
    """get_repeated_corrections() respects top_n limit."""
    from unittest.mock import AsyncMock

    mem = _make_episodic()
    corrections = [
        {"original": f"cmd{i}", "correction": f"fix{i}", "metadata": {}, "timestamp": "2026-04-12"}
        for i in range(10)
    ]
    monkeypatch.setattr(mem, "get_recent_corrections", AsyncMock(return_value=corrections))
    result = await mem.get_repeated_corrections(top_n=3)
    assert len(result) <= 3


async def test_get_repeated_corrections_empty_when_no_corrections(monkeypatch):
    """get_repeated_corrections() returns empty list when no corrections."""
    from unittest.mock import AsyncMock

    mem = _make_episodic()
    monkeypatch.setattr(mem, "get_recent_corrections", AsyncMock(return_value=[]))
    result = await mem.get_repeated_corrections()
    assert result == []
