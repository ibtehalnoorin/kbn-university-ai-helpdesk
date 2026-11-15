"""Text embeddings for the knowledge base.

Default provider ``onnx`` runs a small model in-process (no Ollama, no API key),
which is what free hosting needs. ``KBN_EMBEDDING_PROVIDER=ollama`` keeps the
original local path. Whatever provider built ``local_vector_kb`` must also be
used at query time; run ``python backend/scripts/reindex.py`` after switching.
"""

from __future__ import annotations

import os
import threading

DEFAULT_ONNX_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_OLLAMA_MODEL = "nomic-embed-text"

_model = None
_lock = threading.Lock()


def provider() -> str:
    return os.environ.get("KBN_EMBEDDING_PROVIDER", "onnx").strip().lower()


def model_name() -> str:
    default = DEFAULT_OLLAMA_MODEL if provider() == "ollama" else DEFAULT_ONNX_MODEL
    return os.environ.get("KBN_EMBEDDING_MODEL") or default


def _onnx_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from fastembed import TextEmbedding

                # A bundled model folder (plain files) avoids any download or cache writes.
                model_dir = os.environ.get("KBN_EMBEDDING_MODEL_DIR")
                kwargs = {"specific_model_path": model_dir} if model_dir else {}
                _model = TextEmbedding(model_name(), **kwargs)
    return _model


def embed(text: str) -> list[float]:
    if provider() == "ollama":
        import ollama

        return ollama.embeddings(model=model_name(), prompt=text)["embedding"]
    vector = next(iter(_onnx_model().embed([text])))
    return [float(x) for x in vector]
