"""Template library for common circuit patterns."""

import json
import os
from typing import List, Optional

_TEMPLATES_CACHE: Optional[List[dict]] = None


def _load_templates() -> List[dict]:
    """Load templates from the JSON file."""
    global _TEMPLATES_CACHE
    if _TEMPLATES_CACHE is not None:
        return _TEMPLATES_CACHE

    template_path = os.path.join(os.path.dirname(__file__), "circuit_templates.json")
    try:
        with open(template_path, "r") as f:
            data = json.load(f)
        _TEMPLATES_CACHE = data.get("templates", [])
    except Exception:
        _TEMPLATES_CACHE = []

    return _TEMPLATES_CACHE


def search_templates(query: str, k: int = 3) -> List[dict]:
    """Search templates by keyword matching against name, description, and keywords."""
    templates = _load_templates()
    query_lower = query.lower()

    scored = []
    for t in templates:
        score = 0
        # Match against name
        if query_lower in t.get("name", "").lower():
            score += 3
        # Match against description
        if query_lower in t.get("description", "").lower():
            score += 2
        # Match against keywords
        for kw in t.get("keywords", []):
            if kw.lower() in query_lower or query_lower in kw.lower():
                score += 2
        # Match against subsystem names
        for sub in t.get("subsystems", []):
            if query_lower in sub.get("subsystem", "").lower():
                score += 1
            if query_lower in sub.get("function", "").lower():
                score += 1

        if score > 0:
            scored.append((score, t))

    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored[:k]]


def get_template_by_id(template_id: str) -> Optional[dict]:
    """Get a specific template by its ID."""
    templates = _load_templates()
    for t in templates:
        if t.get("id") == template_id:
            return t
    return None


def list_templates() -> List[dict]:
    """List all available templates with summary info."""
    templates = _load_templates()
    return [
        {
            "id": t["id"],
            "name": t["name"],
            "description": t["description"],
            "subsystem_count": len(t.get("subsystems", [])),
        }
        for t in templates
    ]


def get_template_subsystems(template_id: str) -> List[dict]:
    """Get subsystem definitions from a template."""
    template = get_template_by_id(template_id)
    if not template:
        return []
    return template.get("subsystems", [])


def get_template_nets(template_id: str) -> List[dict]:
    """Get net definitions from a template."""
    template = get_template_by_id(template_id)
    if not template:
        return []
    return template.get("nets", [])
