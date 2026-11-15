"""Rebuild the ChromaDB collection from ``knowledge_docs`` with the active embedder.

Run from anywhere:  python backend/scripts/reindex.py
Uses the original chunking from ``ingest.py`` so ids and metadata are unchanged
(``<file stem>_chunk_<i>`` with ``source`` and ``chunk_index``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ingest  # noqa: E402
from kb_manager import KnowledgeBase  # noqa: E402

COLLECTION = "local_research_kb"


def reindex(docs_dir: Path, collection: str = COLLECTION) -> int:
    """Delete and rebuild the collection in ./local_vector_kb; return the chunk count."""
    kb = KnowledgeBase(collection_name=collection)
    kb.client.delete_collection(collection)
    kb = KnowledgeBase(collection_name=collection)
    total = 0
    files = sorted([*docs_dir.glob("**/*.md"), *docs_dir.glob("**/*.txt")])
    for path in files:
        if path.name.startswith("."):
            continue
        chunks = ingest.chunk_text(path.read_text(encoding="utf-8"), chunk_size=600, overlap=100)
        for index, chunk in enumerate(chunks):
            kb.add_document(
                doc_id=f"{path.stem}_chunk_{index}",
                text=chunk,
                metadata={"source": path.name, "chunk_index": index},
            )
        total += len(chunks)
    return total


if __name__ == "__main__":
    os.chdir(ROOT)  # the original modules use the relative path ./local_vector_kb
    count = reindex(ROOT / "knowledge_docs")
    print(f"Indexed {count} chunks into {COLLECTION}.")
