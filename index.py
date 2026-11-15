"""Vercel entrypoint: exposes the FastAPI ``app`` (see ``tool.vercel.entrypoint`` in pyproject.toml).

The bundle is read-only, so the vector store and model cache are copied to the temp
directory before the application (and its settings) are created.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for entry in (str(ROOT / "backend"), str(ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from app.runtime import prepare_runtime  # noqa: E402

prepare_runtime(ROOT)

from app.main import app  # noqa: E402,F401  (Vercel looks for this name)
