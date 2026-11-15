"""Runtime preparation for read-only hosts such as Vercel.

A serverless bundle is read-only, but ChromaDB must write to its SQLite file and the
embedding model cache is easier to keep next to it. On such hosts we copy both to the
temporary directory once per instance and point the settings at the copies.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import MutableMapping


def is_read_only(project_root: Path) -> bool:
    if os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return True
    return not os.access(project_root, os.W_OK)


def _make_writable(path: Path) -> None:
    """A read-only bundle copies as read-only; the instance must be able to write to its copy."""
    for current, directories, files in os.walk(path):
        for name in [*directories, *files, ""]:
            target = Path(current, name) if name else Path(current)
            os.chmod(target, os.stat(target).st_mode | 0o200 | (0o100 if target.is_dir() else 0))


def _copy_once(source: Path, target: Path) -> bool:
    """Copy ``source`` to ``target`` unless it already exists. Safe if two cold starts race."""
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f"{target.name}.{uuid.uuid4().hex}.partial")
    shutil.copytree(source, staging)
    _make_writable(staging)
    try:
        os.rename(staging, target)
    except OSError:  # another instance finished first
        shutil.rmtree(staging, ignore_errors=True)
        return False
    return True


def prepare_runtime(
    project_root: Path,
    env: MutableMapping[str, str] | None = None,
    read_only: bool | None = None,
    scratch_dir: Path | None = None,
) -> dict:
    env = os.environ if env is None else env
    read_only = is_read_only(project_root) if read_only is None else read_only
    if not read_only:
        return {"read_only": False, "copied": False}

    scratch = (scratch_dir or Path(tempfile.gettempdir())) / "kbn"
    store = scratch / "local_vector_kb"
    copied = _copy_once(project_root / "local_vector_kb", store)
    env["KBN_CHROMA_DIR"] = str(store)

    model_cache = scratch / "fastembed_cache"
    bundled = project_root / ".fastembed_cache"
    if bundled.exists():
        _copy_once(bundled, model_cache)
    else:
        model_cache.mkdir(parents=True, exist_ok=True)  # model is downloaded on first use
    env["FASTEMBED_CACHE_PATH"] = str(model_cache)
    # vercel.json gives the function 60 s; answer or fail before the platform kills it.
    env.setdefault("KBN_REQUEST_TIMEOUT_SECONDS", "50")
    return {"read_only": True, "copied": copied, "store": str(store), "model_cache": str(model_cache)}
