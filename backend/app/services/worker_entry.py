"""Child-process entry point for the existing ``processor.process_worker``.

The protected ``processor.py`` hardcodes the model name and generation options.
This entry point applies runtime overrides without editing that file: it sets the
module-level model name and wraps ``ollama.chat`` so every call gets the tuned
options, ``think=False`` for Qwen3 and a long ``keep_alive``. It then starts the
original worker loop unchanged.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def run_worker(task_queue, result_queue, ollama_host: str) -> None:
    backend_dir = Path(__file__).resolve().parents[2]
    project_root = backend_dir.parent
    for path in (str(project_root), str(backend_dir)):
        if path not in sys.path:
            sys.path.insert(0, path)

    if ollama_host:
        os.environ["OLLAMA_HOST"] = ollama_host

    from app.config import get_settings

    settings = get_settings()

    # Imported in the child so the parent never loads the Ollama client.
    import ollama

    import processor
    from processor import process_worker

    logger = logging.getLogger("kbn.worker_entry")
    base_options = {
        "temperature": settings.llm_temperature,
        "num_ctx": settings.llm_num_ctx,
        "num_predict": settings.llm_num_predict,
        "num_thread": settings.llm_num_thread,
    }

    processor.LOCAL_MODEL = settings.llm_model
    original_chat = ollama.chat

    def tuned_chat(*args, **kwargs):
        options = dict(kwargs.get("options") or {})
        options.update(base_options)
        kwargs["options"] = options
        kwargs["keep_alive"] = settings.llm_keep_alive
        if settings.llm_think is not None:
            kwargs["think"] = settings.llm_think
        return original_chat(*args, **kwargs)

    # processor.py calls ``ollama.chat`` through the module attribute, so this
    # patch applies to the protected code path as well.
    ollama.chat = tuned_chat  # type: ignore[assignment]

    if settings.llm_warmup:
        try:
            original_chat(
                model=settings.llm_model,
                messages=[{"role": "user", "content": "ping"}],
                think=settings.llm_think,
                keep_alive=settings.llm_keep_alive,
                options={**base_options, "num_predict": 1},
            )
            logger.info("LLM warmup complete for model=%s", settings.llm_model)
        except Exception as exc:  # pragma: no cover - depends on local Ollama
            logger.warning("LLM warmup failed: %s", exc)

    process_worker(task_queue, result_queue)
