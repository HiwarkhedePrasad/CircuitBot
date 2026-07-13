import os
import re
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


def _count_results(raw: str) -> int:
    return len(re.findall(r"^• ", raw, re.MULTILINE))


def _extract_urls(raw: str) -> list[str]:
    return re.findall(r"https?://\S+", raw)


def deep_search(query: str, config=None) -> str:
    """Run a research query: search the web, then synthesize results with the LLM."""

    try:
        from agent.emit_utils import emit_tool_call, emit_tool_end, emit_step, emit_thought
    except ImportError:
        emit_tool_call = emit_tool_end = emit_step = emit_thought = lambda *a, **kw: None

    tool_id = f"websearch_{os.urandom(4).hex()}"

    if config:
        emit_tool_call(config, tool_id, f"Searching the web for: {query[:80]}...")

    raw_results = tinyfish_search(query, max_results=5)
    count = _count_results(raw_results)
    urls = _extract_urls(raw_results)

    if config:
        emit_step(config, tool_id, f"Found {count} results from TinyFish search", "completed")

    fetched_content = ""
    for i, url in enumerate(urls[:2]):
        if config:
            emit_step(config, tool_id, f"Fetching: {url[:70]}...", "running")
        try:
            content = tinyfish_fetch(url)
            if content and content != "(No content extracted)":
                fetched_content += f"\n--- Content from {url} ---\n{content[:2000]}\n"
                if config:
                    emit_step(config, tool_id, f"Fetched content from {url[:50]}...", "completed")
        except Exception as e:
            if config:
                emit_step(config, tool_id, f"Failed to fetch {url[:50]}...", "completed")

    details = f"RAW SEARCH RESULTS ({count} results):\n\n{raw_results}"
    if fetched_content:
        details += f"\n\nFETCHED PAGE CONTENT:\n{fetched_content}"

    if config:
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
