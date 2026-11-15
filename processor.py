import time
import logging
import ollama

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | [%(levelname)s] | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("ProcessorWorker")

LOCAL_MODEL = "qwen3:8b"
MAX_CONTEXT_TOKENS = 8192


def estimate_tokens(messages: list) -> int:
    total_chars = sum(len(m.get("content", "")) for m in messages)
    return int(total_chars / 4)


def truncate_history(history: list, max_tokens: int) -> list:
    safe_limit = int(max_tokens * 0.75)
    while estimate_tokens(history) > safe_limit and len(history) > 3:
        history.pop(1)
        if len(history) > 1 and history[1]["role"] == "assistant":
            history.pop(1)
    return history


def process_worker(task_queue, result_queue):
    logger.info("Background session worker started successfully.")
    sessions = {}

    while True:
        session_id = "default"
        query = "Unknown"

        try:
            task = task_queue.get()
            if task is None:
                break

            session_id, query, kb_knowledge, web_context, persona_name, system_prompt = task
            start_time = time.time()

            logger.info(f"Session [{session_id}] | Persona: '{persona_name}' | Query: '{query}'")

            if session_id not in sessions:
                sessions[session_id] = []

            history = sessions[session_id]

            # Set or update system prompt based on pre-checked persona
            if not history or history[0]["role"] != "system":
                history.insert(0, {"role": "system", "content": system_prompt})
            else:
                history[0]["content"] = system_prompt

            history = truncate_history(history, MAX_CONTEXT_TOKENS)

            # Combine retrieved contexts if available
            combined_parts = []
            if kb_knowledge:
                combined_parts.append(kb_knowledge)
            if web_context:
                combined_parts.append(web_context)

            context_string = "\n\n".join(combined_parts)

            if context_string.strip():
                user_prompt = f"User Query: {query}\n\nRetrieved Context & Knowledge:\n{context_string}"
            else:
                user_prompt = f"User Query: {query}"

            history.append({"role": "user", "content": user_prompt})

            response = ollama.chat(
                model=LOCAL_MODEL,
                messages=history,
                options={"temperature": 0.3, "num_ctx": MAX_CONTEXT_TOKENS}
            )

            answer = response['message']['content']
            elapsed = time.time() - start_time

            history.append({"role": "assistant", "content": answer})

            final_tokens = estimate_tokens(history)
            final_pct = min(100.0, (final_tokens / MAX_CONTEXT_TOKENS) * 100)

            result_queue.put({
                "status": "success",
                "session_id": session_id,
                "query": query,
                "answer": answer,
                "time": elapsed,
                "persona": persona_name,
                "tokens_used": final_tokens,
                "max_tokens": MAX_CONTEXT_TOKENS,
                "utilization": final_pct
            })

        except Exception as e:
            logger.error(f"Error processing session {session_id}: {e}")
            result_queue.put({
                "status": "error",
                "session_id": session_id,
                "query": query,
                "error": str(e)
            })