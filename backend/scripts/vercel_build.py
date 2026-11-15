"""Vercel build step: build the React app and bundle the embedding model.

Run by ``buildCommand`` in vercel.json after Python dependencies are installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
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


def flatten_model(cache_dir: Path, dest: Path) -> Path:
    """Copy a downloaded Hugging Face-style model into ``dest`` as plain files.

    That cache stores each file once and links to it with symlinks, and Vercel counts every
    link as a full copy (the 65 MB model became 192 MB and pushed the bundle past 500 MB).
    """
    snapshots = sorted(cache_dir.glob("models--*/snapshots/*"))
    if not snapshots:
        sys.exit(f"No model snapshot found under {cache_dir}")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for item in snapshots[-1].iterdir():
        if item.is_file():  # follows symlinks, so real bytes are written
            shutil.copyfile(item, dest / item.name)
    return dest


def bundle_model() -> None:
    """Download the embedding model once and keep it in the project as plain files."""
    from fastembed import TextEmbedding

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["FASTEMBED_CACHE_PATH"] = tmp
        TextEmbedding(MODEL)
        dest = flatten_model(Path(tmp), ROOT / ".embedding_model")
    size = sum(p.stat().st_size for p in dest.iterdir()) / 1e6
    print(f"Embedding model {MODEL} bundled in {dest} ({size:.0f} MB, plain files)")


def remove_node_modules(root: Path) -> None:
    """Build-only files must not end up in the function bundle."""
    shutil.rmtree(root / "frontend" / "node_modules", ignore_errors=True)


def main(argv: list[str] | None = None) -> None:
    """No flag: do both. ``--model-only`` / ``--frontend-only`` for per-service builds."""
    args = sys.argv[1:] if argv is None else argv
    if "--model-only" not in args:
        build_frontend()
        if os.environ.get("VERCEL"):
            remove_node_modules(ROOT)
    if "--frontend-only" not in args:
        bundle_model()


if __name__ == "__main__":
    main()
