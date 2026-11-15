import asyncio
import threading
import multiprocessing
from researcher import get_search_and_scrape_content
from processor import process_worker
from helpers import is_direct_query, classify_intent
from kb_manager import KnowledgeBase

MAX_RESULTS_TO_SCRAPE = 3
kb = KnowledgeBase()


def render_progress_bar(percentage: float, length: int = 20) -> str:
    """Generates a text-based progress bar for context tracking."""
    filled = int(length * percentage / 100)
    bar = "█" * filled + "-" * (length - filled)
    return f"[{bar}] {percentage:.1f}%"


def result_listener(result_queue):
    """Listens for background completions and prints persona badges & metrics."""
    while True:
        result = result_queue.get()
        if result is None:
            break

        if result["status"] == "success":
            print("\n" + "=" * 30 + f" SESSION: {result['session_id']} " + "=" * 30)
            print(f"Query: {result['query']} (Processed in {result['time']:.2f}s)")
            print(f"🎭 Active Persona: [{result.get('persona', 'AI Research Assistant')}]")
            print("-" * 86)
            print(result['answer'])
            print("-" * 86)

            progress_bar = render_progress_bar(result['utilization'])
            print(f"📊 Context Window Usage: {progress_bar} (~{result['tokens_used']} / {result['max_tokens']} tokens)")
            print("=" * 86 + "\n")
            print("Enter search query: ", end="", flush=True)

        elif result["status"] == "error":
            print(f"\n[!] Error in session '{result['session_id']}': {result['error']}\n")
            print("Enter search query: ", end="", flush=True)


async def main_async_loop(task_queue):
    print("[+] Interactive Multi-Session Research Assistant Initialized.")
    session_id = input("Enter session name (default 'default'): ").strip() or "default"
    print(f"[+] Active Session: '{session_id}'. Type 'exit' to quit.\n")

    while True:
        query = input("Enter search query: ").strip()

        if query.lower() in ["exit", "quit"]:
            print("Shutting down...")
            task_queue.put(None)
            break

        if not query:
            continue

        # 1. Pre-check Intent & Persona
        persona_name, system_prompt = classify_intent(query)
        print(f"[*] Pre-check Intent Detected -> Persona: [{persona_name}]")

        kb_knowledge = ""
        web_context = ""

        # 2. Check if query is direct (math, logic, poetry)
        if is_direct_query(query):
            print(f"[*] Direct query recognized. Skipping KB lookup and web search.")
        else:
            # 3. Query Local Vector Knowledge Base first
            print(f"[*] Querying Local Knowledge Base (RAG)...")
            kb_knowledge = kb.query_kb(query, n_results=2)

            # 4. Fallback to Web Search if KB is empty/missing
            if not kb_knowledge.strip():
                print(f"[-] Answer not found in Knowledge Base. Falling back to live web search & scrape...")
                web_context = await get_search_and_scrape_content(query, max_results_per_backend=MAX_RESULTS_TO_SCRAPE)
            else:
                print(f"[+] Relevant knowledge found in Local Knowledge Base! Skipping web search.")

        # 5. Hand off payload to background worker process
        task_payload = (session_id, query, kb_knowledge, web_context, persona_name, system_prompt)
        task_queue.put(task_payload)
        print("[+] Task dispatched to background worker! You can type your next query:\n")


def main():
    multiprocessing.freeze_support()

    task_queue = multiprocessing.Queue()
    result_queue = multiprocessing.Queue()

    background_process = multiprocessing.Process(
        target=process_worker,
        args=(task_queue, result_queue)
    )
    background_process.daemon = True
    background_process.start()

    listener_thread = threading.Thread(
        target=result_listener,
        args=(result_queue,),
        daemon=True
    )
    listener_thread.start()

    try:
        asyncio.run(main_async_loop(task_queue))
    except KeyboardInterrupt:
        print("\nExiting application.")
    finally:
        task_queue.put(None)
        result_queue.put(None)
        background_process.terminate()


if __name__ == "__main__":
    main()