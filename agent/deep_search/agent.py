import os
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from agent.deep_search.tools import tinyfish_search, tinyfish_fetch

dotenv_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path, override=True)

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:4010/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "opencode/deepseek-v4-flash-free")

_model = ChatOpenAI(
    model=LLM_MODEL,
    base_url=LLM_BASE_URL,
    api_key="not-needed",
    temperature=0.1,
    max_tokens=8192,
)

# Rate limiter: max 25 searches/min (5 buffer under TinyFish free tier 30/min)
_search_semaphore = threading.Semaphore(2)  # max 2 concurrent
_rate_lock = threading.Lock()
_rate_timestamps: list[float] = []
_MAX_SEARCHES_PER_MIN = 25


def _rate_limit_check():
    """Block if we'd exceed 25 searches/min. Adds 0.5s stagger between batches."""
    with _rate_lock:
        now = time.time()
        # Prune timestamps older than 60s
        _rate_timestamps[:] = [t for t in _rate_timestamps if now - t < 60]
        if len(_rate_timestamps) >= _MAX_SEARCHES_PER_MIN:
            sleep_time = 60 - (now - _rate_timestamps[0]) + 0.1
            if sleep_time > 0:
                time.sleep(sleep_time)
        _rate_timestamps.append(time.time())
    time.sleep(0.5)  # stagger between batch starts


def _count_results(raw: str) -> int:
    return len(re.findall(r"^• ", raw, re.MULTILINE))


def _extract_urls(raw: str) -> list[str]:
    return re.findall(r"https?://\S+", raw)


def deep_search(query: str, config=None, quiet: bool = False) -> str:
    """Run a research query: search the web, then synthesize results with the LLM.

    Args:
        query: The search query.
        config: LangGraph config with emit function.
        quiet: If True, suppress all UI emit calls (for parallel thread use).
    """
    if not quiet:
        try:
            from agent.emit_utils import emit_tool_call, emit_tool_end, emit_step, emit_thought
        except ImportError:
            emit_tool_call = emit_tool_end = emit_step = emit_thought = lambda *a, **kw: None
    else:
        emit_tool_call = emit_tool_end = emit_step = emit_thought = lambda *a, **kw: None

    tool_id = f"websearch_{os.urandom(4).hex()}"

    if config and not quiet:
        emit_tool_call(config, tool_id, f"Searching the web for: {query[:80]}...")

    raw_results = tinyfish_search(query, max_results=5)
    count = _count_results(raw_results)
    urls = _extract_urls(raw_results)

    if config and not quiet:
        emit_step(config, tool_id, f"Found {count} results from TinyFish search", "completed")

    fetched_content = ""
    for i, url in enumerate(urls[:3]):  # try 3 URLs instead of 2
        if config and not quiet:
            emit_step(config, tool_id, f"Fetching: {url[:70]}...", "running")
        try:
            content = tinyfish_fetch(url)
            if content and content != "(No content extracted)":
                fetched_content += f"\n--- Content from {url} ---\n{content[:8000]}\n"
                if config and not quiet:
                    emit_step(config, tool_id, f"Fetched content from {url[:50]}...", "completed")
        except Exception as e:
            if config and not quiet:
                emit_step(config, tool_id, f"Failed to fetch {url[:50]}...", "completed")

    details = f"RAW SEARCH RESULTS ({count} results):\n\n{raw_results}"
    if fetched_content:
        details += f"\n\nFETCHED PAGE CONTENT:\n{fetched_content}"

    if config and not quiet:
        emit_tool_end(config, tool_id, f"Web search complete — {count} results found", details=details)
        emit_thought(config, "Synthesizing search results into a structured summary...")

    prompt = (
        "You are an expert electronics research synthesizer. Below are web search results "
        "for a user's query. Summarize the findings in a clear, structured format. "
        "Include specific part numbers, datasheet URLs, and key specifications where available. "
        "If the search returned no useful results, say so.\n\n"
        f"Query: {query}\n\n"
        f"Search Results:\n{raw_results}"
    )
    if fetched_content:
        prompt += f"\n\nFetched page content:\n{fetched_content}"

    response = _model.invoke([
        {"role": "system", "content": "You are a precise electronics research assistant."},
        {"role": "user", "content": prompt},
    ])

    return response.content


def _deep_search_single(query: str, config=None) -> dict:
    """Run a single deep_search with rate limiting and return structured result."""
    _rate_limit_check()
    _search_semaphore.acquire()
    try:
        # quiet=True: suppress individual tool cards in parallel mode
        summary = deep_search(query, config=config, quiet=True)
        return {"query": query, "summary": summary, "success": True}
    except Exception as e:
        return {"query": query, "summary": f"(Search failed: {e})", "success": False}
    finally:
        _search_semaphore.release()


def deep_search_parallel(queries: list[str], config=None) -> list[dict]:
    """Run multiple deep_search queries with up to 2 concurrent workers.

    Returns list of {query, summary, success} dicts in the same order as input.
    """
    if not queries:
        return []
    if len(queries) == 1:
        return [_deep_search_single(queries[0], config)]

    results: list[dict] = [None] * len(queries)  # type: ignore
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_to_idx = {
            executor.submit(_deep_search_single, q, config): i
            for i, q in enumerate(queries)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                results[idx] = {
                    "query": queries[idx],
                    "summary": f"(Parallel search failed: {e})",
                    "success": False,
                }
    return results
