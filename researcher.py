import asyncio
from concurrent.futures import ThreadPoolExecutor
from ddgs import DDGS
from crawl4ai import AsyncWebCrawler
from helpers import deduplicate_urls, scrape_single_url

# Shared thread pool for concurrent search queries across multiple engines/backends
search_executor = ThreadPoolExecutor(max_workers=5)


def _search_with_backend(query: str, backend: str, max_results: int) -> list[str]:
    """Executes a search query using a specific backend ('api', 'html', 'lite')."""
    urls = []
    try:
        print(f"[*] Searching via engine backend '{backend}' for: '{query}'...")
        with DDGS() as ddgs:
            results = ddgs.text(query, max_results=max_results, backend=backend)
            if results:
                for r in results:
                    if 'href' in r:
                        urls.append(r['href'])
    except Exception as e:
        print(f"[!] Search failed with backend '{backend}': {e}")
    return urls


async def perform_multi_engine_search(query: str, max_results_per_backend: int = 3) -> list[str]:
    """Performs concurrent searches across multiple search backends using multithreading."""
    loop = asyncio.get_running_loop()

    # Utilizing multiple DuckDuckGo search backends as independent engines
    backends = ['api', 'html', 'lite']

    # Dispatch searches to background threads simultaneously
    futures = [
        loop.run_in_executor(search_executor, _search_with_backend, query, backend, max_results_per_backend)
        for backend in backends
    ]

    results_nested = await asyncio.gather(*futures)

    # Flatten all returned URL lists
    raw_urls = [url for url_list in results_nested for url in url_list]

    # Use helper function to eliminate duplicates
    unique_urls = deduplicate_urls(raw_urls)
    print(f"[*] Found {len(unique_urls)} unique URLs across all search backends.")
    return unique_urls


async def get_search_and_scrape_content(query: str, max_results_per_backend: int = 3) -> str:
    """Orchestrates multi-backend search and concurrent scraping."""
    urls = await perform_multi_engine_search(query, max_results_per_backend)
    if not urls:
        return ""

    print("[*] Starting concurrent scraping across all unique URLs...")
    async with AsyncWebCrawler(verbose=False) as crawler:
        tasks = [scrape_single_url(crawler, url) for url in urls]
        results = await asyncio.gather(*tasks)

    return "".join(results)