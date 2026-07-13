import os
from pathlib import Path
from dotenv import load_dotenv

dotenv_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path, override=True)


def tinyfish_search(query: str, max_results: int = 5) -> str:
    """Search the web using TinyFish Search API.
    Args: query (str), max_results (int, default 5).
    Returns: formatted search results with titles, snippets, and URLs."""
    try:
        from tinyfish import TinyFish
        api_key = os.environ.get("TINYFISH_API_KEY")
        client = TinyFish(api_key=api_key)
        response = client.search.query(query=query)
        lines = []
        for r in response.results[:max_results]:
            lines.append(f"• {r.title}\n  {r.snippet}\n  URL: {r.url}")
        return "\n\n".join(lines) if lines else "(No results found)"
    except Exception as e:
        return f"(Search failed: {e})"


def tinyfish_fetch(url: str) -> str:
    """Fetch and extract clean content from a URL using TinyFish Fetch API.
    Args: url (str).
    Returns: clean page content as text."""
    try:
        from tinyfish import TinyFish
        api_key = os.environ.get("TINYFISH_API_KEY")
        client = TinyFish(api_key=api_key)
        response = client.fetch(url=url)
        return response.content or "(No content extracted)"
    except Exception as e:
        return f"(Fetch failed: {e})"
