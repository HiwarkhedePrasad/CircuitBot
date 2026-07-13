import os
from pathlib import Path
from dotenv import load_dotenv

dotenv_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path, override=True)

# Check if tinyfish is available at module load time
_TINYFISH_AVAILABLE = False
try:
    from tinyfish import TinyFish
    _TINYFISH_AVAILABLE = True
except ImportError:
    pass


def tinyfish_search(query: str, max_results: int = 5) -> str:
    """Search the web using TinyFish Search API.
    Args: query (str), max_results (int, default 5).
    Returns: formatted search results with titles, snippets, and URLs."""
    if not _TINYFISH_AVAILABLE:
        return "(Search unavailable: tinyfish module not installed. Run: pip install tinyfish)"
    try:
        api_key = os.environ.get("TINYFISH_API_KEY")
        if not api_key:
            return "(Search unavailable: TINYFISH_API_KEY not set in .env)"
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
    if not _TINYFISH_AVAILABLE:
        return "(Fetch unavailable: tinyfish module not installed. Run: pip install tinyfish)"
    try:
        api_key = os.environ.get("TINYFISH_API_KEY")
        if not api_key:
            return "(Fetch unavailable: TINYFISH_API_KEY not set in .env)"
        client = TinyFish(api_key=api_key)
        response = client.fetch(url=url)
        return response.content or "(No content extracted)"
    except Exception as e:
        return f"(Fetch failed: {e})"
