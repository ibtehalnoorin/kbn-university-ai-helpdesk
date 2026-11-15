"""Vercel build step: build the React app and bundle the embedding model.

Run by ``buildCommand`` in vercel.json after Python dependencies are installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL = os.environ.get("KBN_EMBEDDING_MODEL") or "BAAI/bge-small-en-v1.5"


def build_frontend() -> None:
    dist = ROOT / "frontend" / "dist" / "index.html"
    npm = shutil.which("npm")
    if npm is None:
        if dist.exists():
            print("npm not found; using the existing frontend/dist")
            return
        sys.exit("npm is not available and frontend/dist does not exist. Build the frontend "
                 "locally (cd frontend && npm ci && npm run build) and commit frontend/dist, "
                 "or use a build image with Node.")
    for command in ([npm, "ci"], [npm, "run", "build"]):
        subprocess.run(command, cwd=ROOT / "frontend", check=True)


def bundle_model() -> None:
    """Download the embedding model into the project so cold starts do not fetch it."""
    cache = ROOT / ".fastembed_cache"
    os.environ["FASTEMBED_CACHE_PATH"] = str(cache)
    from fastembed import TextEmbedding

    TextEmbedding(MODEL)
    print(f"Embedding model {MODEL} bundled in {cache}")


if __name__ == "__main__":
    build_frontend()
    bundle_model()
