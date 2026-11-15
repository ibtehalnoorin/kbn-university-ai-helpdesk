from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # optional scraping dependency; only needed for type hints
    from crawl4ai import AsyncWebCrawler
import re


def is_direct_query(query: str) -> bool:
    """
    Returns True if the query can be handled directly by the LLM
    (math, logic, poetry, general chat) without needing KB or web search.
    """
    query_clean = query.strip().lower()

    # Check if it contains numbers and math operators (e.g. 2+3*5, 10 / 2)
    has_math_symbols = bool(re.search(r'[\d].*[\+\-\*\/\^]', query_clean)) or bool(
        re.search(r'[\+\-\*\/\^].*[\d]', query_clean))
    math_starters = ["what is", "calculate", "solve", "eval", "evaluate", "compute"]

    if has_math_symbols or any(query_clean.startswith(kw) for kw in math_starters):
        research_triggers = ["search", "latest", "news", "price", "weather", "who is", "documentation"]
        if not any(trigger in query_clean for trigger in research_triggers):
            return True

    # Conversational or specialized creative/logical intent prefixes
    direct_keywords = ["hello", "hi", "hey", "who are you", "poem", "verse", "rhyme", "stanza", "paradox", "logic",
                       "puzzle"]
    if any(query_clean.startswith(kw) for kw in direct_keywords) or len(query_clean.split()) <= 3:
        research_triggers = ["search", "latest", "news", "price", "weather", "who is", "documentation"]
        if not any(trigger in query_clean for trigger in research_triggers):
            return True

    return False


def classify_intent(query: str) -> tuple[str, str]:
    """Pre-checks query intent and assigns the appropriate persona and system prompt."""
    query_lower = query.lower()

    # 1. Pure Math / Calculation (Robust detection for numbers + operators and math keywords)
    has_math_symbols = bool(re.search(r'[\d].*[\+\-\*\/\^]', query_lower)) or bool(
        re.search(r'[\+\-\*\/\^].*[\d]', query_lower))
    math_starters = ["what is", "calculate", "solve", "eval", "evaluate", "compute"]

    if has_math_symbols or any(query_lower.startswith(kw) for kw in math_starters) or any(
            kw in query_lower for kw in ["math", "equation"]):
        return "General Generalist / Math Solver", (
            "You are a versatile, highly intelligent AI assistant. "
            "Answer mathematical questions directly, accurately, and concisely without external context. "
            "CRITICAL INSTRUCTION: DO NOT use LaTeX formatting or dollar signs ($) in your response. "
            "Write all numbers, steps, and expressions in clean plain text using standard symbols (+, -, *, /)."
        )

    # 2. Romantic Poetry
    if any(kw in query_lower for kw in ["poem", "verse", "rhyme", "stanza", "sonnet"]):
        return "Romantic Poet", (
            "You are a master romantic poet and literary artist. Seamlessly continue or complete "
            "the poetry with rich emotional resonance, vivid sensory imagery, and careful cadence."
        )

    # 3. Paradox & Philosophical
    if any(kw in query_lower for kw in ["paradox", "dilemma", "thought experiment", "infinite loop"]):
        return "Philosophical Explorer (Paradoxes)", (
            "You are a philosophical explorer specializing in paradoxes. "
            "Illuminate their depth and underlying mechanics rather than forcing a simple solution."
        )

    # 4. Logic / Deductions
    if any(kw in query_lower for kw in ["logic", "deduction", "puzzle", "fallacy", "premise"]):
        return "Expert Logician", (
            "You are an expert logician. Break down the problem step-by-step, "
            "identify hidden premises, and provide rigorous deductions."
        )

    # 5. Default Research / RAG Persona
    return "AI Research Assistant (RAG/Web)", (
        "You are an advanced AI research assistant. Answer the query using "
        "the provided Local Knowledge Base snippets and Web Scraped Context. Cite your sources."
    )

def deduplicate_urls(urls: list[str]) -> list[str]:
    """Removes duplicate URLs while preserving order."""
    seen = set()
    unique = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            unique.append(url)
    return unique

async def scrape_single_url(crawler: AsyncWebCrawler, url: str) -> str:
    """Helper function to scrape a single URL asynchronously and format to markdown."""
    try:
        print(f"[*] Scraping URL concurrently: {url}")
        result = await crawler.arun(url=url)
        if result.success and result.markdown:
            content = f"\n\n--- SOURCE: {url} ---\n"
            content += result.markdown[:4000]  # Trim page length for token management
            return content
    except Exception as e:
        print(f"[!] Failed to scrape {url}: {e}")
    return ""