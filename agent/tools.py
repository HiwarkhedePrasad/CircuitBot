import json
import os
from pathlib import Path
from dotenv import load_dotenv
from kicad_rag.client import KicadRAG

dotenv_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path, override=True)

rag = KicadRAG(mode="bm25")


def search_components(query: str, k: int = 8,
                     library_filter: str | None = None) -> list[dict]:
    results = rag.search(query, k=k)
    if library_filter:
        results = [r for r in results
                   if r.id_str.startswith(library_filter + ":")]
    return [
        {
            "id_str": r.id_str,
            "text": r.text,
            "score": r.score,
            "pins": r.pins,
            "datasheet": r.datasheet,
            "footprint": r.footprint,
            "fp_filters": r.fp_filters,
            "pads": r.pads,
        }
        for r in results
    ]


def fetch_sexpr(id_str: str) -> str:
    return rag.sexpr(id_str)


def fetch_pins(id_str: str) -> list[dict]:
    return rag.pins(id_str)


def fetch_footprint(id_str: str) -> dict | None:
    return rag.footprint(id_str)


def llm_call(system: str, user: str, model: str = "meta/llama-3.3-70b-instruct") -> str:
    from langchain_nvidia_ai_endpoints import ChatNVIDIA

    key = os.environ.get("NVIDIA_API_KEY", "")
    if not key:
        raise RuntimeError("NVIDIA_API_KEY not set")
    client = ChatNVIDIA(
        model=model,
        api_key=key,
        temperature=0.1,
        max_tokens=4096,
    )
    full_response = ""
    for chunk in client.stream([{"role": "system", "content": system}, {"role": "user", "content": user}]):
        full_response += chunk.content
    return full_response.strip()