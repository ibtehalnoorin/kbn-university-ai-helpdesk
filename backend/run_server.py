"""Start the KBN University AI Helpdesk API on Windows/macOS/Linux."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
for entry in (str(PROJECT_ROOT), str(BACKEND_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

os.chdir(PROJECT_ROOT)

# Windows' Proactor event loop can permanently stop accepting connections when
# a client aborts during accept (OSError WinError 64 / "Accept failed on a
# socket"). The Selector loop uses select() and is stable for this single-user
# localhost service. Set it before uvicorn creates its event loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn  # noqa: E402

from app.config import get_settings  # noqa: E402


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    main()
