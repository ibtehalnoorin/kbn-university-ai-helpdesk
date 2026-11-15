import os
import chromadb
from chromadb.config import Settings
import embeddings

DB_PATH = "./local_vector_kb"


class KnowledgeBase:
    def __init__(self, collection_name="local_research_kb"):
        # Initialize persistent ChromaDB client stored locally on disk
        self.client = chromadb.PersistentClient(path=DB_PATH)
        self.collection = self.client.get_or_create_collection(name=collection_name)

    def _get_embedding(self, text: str) -> list[float]:
        """Generates a vector embedding (in-process ONNX by default; see embeddings.py)."""
        return embeddings.embed(text)

    def add_document(self, doc_id: str, text: str, metadata: dict = None):
        """Vectorizes a text document/snippet and stores it in the local vector DB."""
        print(f"[*] Vectorizing and adding document '{doc_id}' to Knowledge Base...")
        embedding = self._get_embedding(text)

        self.collection.upsert(
            ids=[doc_id],
            documents=[text],
            embeddings=[embedding],
            metadatas=[metadata or {"source": "local_kb"}]
        )
        print(f"[+] Document '{doc_id}' successfully indexed!")

    def query_kb(self, query: str, n_results: int = 2) -> str:
        """Searches the vector database for knowledge relevant to the query."""
        try:
            if self.collection.count() == 0:
                return ""  # KB is empty

            query_embedding = self._get_embedding(query)

            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=min(n_results, self.collection.count())
            )

            # Extract retrieved documents and format them into a context block
            retrieved_docs = results.get("documents", [[]])[0]
            if not retrieved_docs:
                return ""

            kb_context = "\n\n--- LOCAL KNOWLEDGE BASE (RAG) ---\n"
            for i, doc in enumerate(retrieved_docs):
                kb_context += f"[KB Snippet {i + 1}]: {doc}\n"

            return kb_context
        except Exception as e:
            print(f"[!] Error querying Knowledge Base: {e}")
            return ""