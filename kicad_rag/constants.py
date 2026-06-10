"""Paths, model, and scoring defaults for the KiCad-RAG pipeline."""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent

DATA_DIR = HERE / "data"

# --- generated artifacts (inside data/) ---
DATASET_PATH = DATA_DIR / "turbovec_dataset.json"
INDEX_PATH = DATA_DIR / "circuitbot.tvim"
SQLITE_PATH = DATA_DIR / "circuitbot.sqlite"

# --- external repos (stays at project root) ---
SYMBOLS_ROOT = HERE / "kicad-symbols"
UTILS_ROOT = HERE.parent / "kicad-library-utils"

# --- embedding ---
EMBED_MODEL = "BAAI/bge-small-en-v1.5"   # 384-d ONNX, ~80 MB, fastembed
EMBED_BATCH = 128

# --- retrieval ---
BIT_WIDTH = 4           # TurboQuant bit depth
FANOUT = 100            # docs pulled from each retriever before RRF fusion
K_RRF = 60              # RRF constant; smaller = more weight on top ranks
W_DENSE = 1.0           # weight for dense retriever
W_BM25 = 2.0            # weight for BM25 retriever  (2× for part-number dominance)
