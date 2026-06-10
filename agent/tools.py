import json
import os
from pathlib import Path
from dotenv import load_dotenv
from kicad_rag.client import KicadRAG

dotenv_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path, override=True)

rag = KicadRAG()


def search_components(query: str, k: int = 8) -> list[dict]:
    results = rag.search(query, k=k)
    return [
        {"id_str": r.id_str, "text": r.text, "score": r.score, "pins": r.pins}
        for r in results
    ]


def fetch_sexpr(id_str: str) -> str:
    return rag.sexpr(id_str)


def fetch_pins(id_str: str) -> list[dict]:
    return rag.pins(id_str)


def llm_call(system: str, user: str, model: str = "llama-3.3-70b-versatile") -> str:
    from groq import Groq

    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        raise RuntimeError("GROQ_API_KEY not set")
    client = Groq(api_key=key)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.1,
    )
    return resp.choices[0].message.content.strip()
