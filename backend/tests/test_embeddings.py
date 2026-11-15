"""Embeddings no longer require Ollama; the knowledge base can be rebuilt offline."""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

import embeddings


@pytest.fixture(autouse=True)
def _fresh_chroma_system():
    """Chroma caches clients by the relative path ./local_vector_kb; isolate each test's tmp cwd."""
    from chromadb.api.shared_system_client import SharedSystemClient

    SharedSystemClient.clear_system_cache()
    yield
    SharedSystemClient.clear_system_cache()


def test_embed_dispatches_to_ollama_provider(monkeypatch):
    calls = {}
    fake = types.SimpleNamespace(
        embeddings=lambda model, prompt: calls.update(model=model, prompt=prompt) or {"embedding": [1.0, 2.0]}
    )
    monkeypatch.setitem(sys.modules, "ollama", fake)
    monkeypatch.setenv("KBN_EMBEDDING_PROVIDER", "ollama")
    monkeypatch.delenv("KBN_EMBEDDING_MODEL", raising=False)
    assert embeddings.embed("hi") == [1.0, 2.0]
    assert calls == {"model": "nomic-embed-text", "prompt": "hi"}


def test_default_provider_is_onnx(monkeypatch):
    monkeypatch.delenv("KBN_EMBEDDING_PROVIDER", raising=False)
    assert embeddings.provider() == "onnx"


@pytest.mark.skipif(os.environ.get("RUN_SLOW") != "1", reason="downloads the ONNX model")
def test_embed_returns_384_floats_for_onnx_provider(monkeypatch):
    monkeypatch.delenv("KBN_EMBEDDING_PROVIDER", raising=False)
    vector = embeddings.embed("Who is the Vice Chancellor?")
    assert len(vector) == 384 and all(isinstance(x, float) for x in vector)


def test_kb_manager_uses_the_embeddings_module(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(embeddings, "embed", lambda text: [0.5, 0.5, 0.5])
    from kb_manager import KnowledgeBase

    assert KnowledgeBase()._get_embedding("anything") == [0.5, 0.5, 0.5]


def test_reindex_rebuilds_collection_idempotently(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(embeddings, "embed", lambda text: [float(len(text) % 7), 1.0, 0.0, 0.5])
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# Source: https://x.test/a\n" + "alpha " * 300, encoding="utf-8")
    (docs / "b.md").write_text("short page", encoding="utf-8")
    (docs / ".ingested_hashes.json").write_text("{}", encoding="utf-8")

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    try:
        import reindex
    finally:
        sys.path.pop(0)

    expected = len(__import__("ingest").chunk_text((docs / "a.md").read_text(encoding="utf-8"))) + 1
    assert reindex.reindex(docs) == expected
    assert reindex.reindex(docs) == expected  # running twice must not duplicate chunks

    from kb_manager import KnowledgeBase

    kb = KnowledgeBase()
    assert kb.collection.count() == expected
    got = kb.collection.get(ids=["b_chunk_0"])
    assert got["metadatas"][0] == {"source": "b.md", "chunk_index": 0}


def test_embed_loads_a_local_model_folder_when_configured(monkeypatch, tmp_path):
    import fastembed

    captured = {}

    class FakeTextEmbedding:
        def __init__(self, name, **kwargs):
            captured.update(name=name, **kwargs)

        def embed(self, texts):
            return iter([[0.25, 0.5]])

    monkeypatch.setattr(fastembed, "TextEmbedding", FakeTextEmbedding)
    monkeypatch.setattr(embeddings, "_model", None)
    monkeypatch.setenv("KBN_EMBEDDING_PROVIDER", "onnx")
    monkeypatch.setenv("KBN_EMBEDDING_MODEL_DIR", str(tmp_path))
    assert embeddings.embed("hi") == [0.25, 0.5]
    assert captured["specific_model_path"] == str(tmp_path)
    monkeypatch.setattr(embeddings, "_model", None)  # do not leak the fake into other tests
