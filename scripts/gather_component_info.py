"""Gather all component info (id_str, datasheet URL, pins, footprint, etc.)
from the CircuitBot RAG dataset and export as structured JSON."""

import json
import os
import re
import sys
from pathlib import Path


DATASET_PATH = Path(__file__).resolve().parent.parent / "kicad_rag" / "data" / "turbovec_dataset.json"
OUTPUT_PATH = Path(__file__).resolve().parent / "component_catalog.json"


def _parse_description(text: str) -> str:
    """Extract the Description field from text_for_embedding."""
    m = re.search(r'Description:\s*([^.]*\.)', text)
    return m.group(1).strip() if m else text[:200].strip()


def _clean(val):
    """Return None for empty/missing strings, otherwise the value."""
    if val is None:
        return None
    if isinstance(val, str) and not val.strip():
        return None
    return val


def process_record(record: dict) -> dict:
    """Flatten a single dataset record into the output schema."""
    return {
        "id_str": record.get("id", ""),
        "description": _parse_description(record.get("text_for_embedding", "")),
        "datasheet_url": _clean(record.get("datasheet")),
        "footprint": _clean(record.get("footprint")),
        "extends": _clean(record.get("extends")),
        "fp_filters": record.get("fp_filters", []),
        "pin_count": len(record.get("pins_ground_truth", [])),
        "pins": [
            {
                "num": p.get("num"),
                "name": p.get("name"),
                "type": p.get("type"),
            }
            for p in record.get("pins_ground_truth", [])
        ],
    }


def main():
    if not DATASET_PATH.exists():
        print(f"ERROR: Dataset not found at {DATASET_PATH}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {DATASET_PATH} ...")
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    total = len(data)
    print(f"Processing {total} records ...")

    catalog = []
    with_ds = 0
    for i, record in enumerate(data):
        entry = process_record(record)
        if entry["datasheet_url"]:
            with_ds += 1
        catalog.append(entry)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)

    print(f"\nDone. {total} components processed.")
    print(f"  With datasheet URL : {with_ds}")
    print(f"  Without            : {total - with_ds}")
    print(f"Output written to    : {OUTPUT_PATH}")
    print(f"File size            : {os.path.getsize(OUTPUT_PATH) / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
