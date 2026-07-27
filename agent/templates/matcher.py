"""Deterministic template matcher — matches user prompts to board templates.

Uses keyword scoring + component name matching. Zero LLM calls.
If the best template match score exceeds a threshold, its subsystems
are used directly — no LLM analysis needed.
"""

import re
from typing import Optional

from agent.templates.loader import _load_templates
from agent.utils import _extract_part_numbers


# ── Keyword extraction ───────────────────────────────────────────────────

_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "be", "are", "were",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can", "need",
    "want", "like", "make", "build", "design", "create", "generate",
    "that", "this", "these", "those", "it", "its", "them", "they",
    "very", "just", "also", "too", "more", "much", "many", "some",
    "any", "each", "every", "all", "both", "few", "most", "other",
    "into", "over", "such", "than", "then", "there", "here", "where",
    "when", "why", "how", "what", "which", "who", "whose",
    "using", "based", "around", "about", "between", "under",
    "please", "thanks", "thank", "hello", "hi",
})


def _tokenize(text: str) -> set[str]:
    """Extract meaningful tokens from a prompt."""
    lower = text.lower()
    tokens = set(re.findall(r'[a-z0-9]+(?:[-_][a-z0-9]+)*', lower))
    return {t for t in tokens if len(t) >= 2 and t not in _STOP_WORDS}


def _component_names(text: str) -> set[str]:
    """Extract component/part names (e.g. ESP32, BME280, DS18B20)."""
    parts = _extract_part_numbers(text)
    return {p.upper() for p in parts}


# ── Match result ─────────────────────────────────────────────────────────

class TemplateMatch:
    """Result of matching a prompt against the template library."""

    def __init__(
        self,
        template_id: str,
        name: str,
        confidence: float,
        subsystems: list[dict],
        nets: list[dict],
    ):
        self.template_id = template_id
        self.name = name
        self.confidence = confidence
        self.subsystems = subsystems
        self.nets = nets

    def is_confident(self, threshold: float = 0.6) -> bool:
        return self.confidence >= threshold


# ── Token font-size multiplier table ────────────────────────────────────
# Longer tokens carry more information (part numbers, specific models).
# Short tokens (< 4 chars) are common words with low discriminatory power.

_TOKEN_WEIGHT = {
    (2, 3): 0.5,
    (4, 5): 1.0,
    (6, 8): 1.5,
    (9, 99): 2.0,
}

def _weight(tok: str) -> float:
    for (lo, hi), w in _TOKEN_WEIGHT.items():
        if lo <= len(tok) <= hi:
            return w
    return 0.5


# ── Scoring ──────────────────────────────────────────────────────────────

def _score_template(template: dict, prompt_tokens: set[str], comp_names: set[str]) -> float:
    """Compute a match score between a template and the user prompt."""
    score = 0.0

    name_kw = set(template.get("keywords", []))
    desc = template.get("description", "")
    name = template.get("name", "")

    # Keyword matches (weighted by token length)
    for kw in name_kw:
        kw_tokens = _tokenize(kw)
        overlap = prompt_tokens & kw_tokens
        if overlap:
            score += sum(_weight(t) for t in overlap) * 1.5

    # Subsystem name matches
    for sub in template.get("subsystems", []):
        sub_tokens = _tokenize(sub.get("subsystem", ""))
        overlap = prompt_tokens & sub_tokens
        if overlap:
            score += sum(_weight(t) for t in overlap) * 1.2

    # Subsystem example component matches (HIGH weight — exact part numbers)
    for sub in template.get("subsystems", []):
        for ex in sub.get("example_components", []):
            ex_upper = ex.upper()
            if ex_upper in comp_names:
                score += 3.0
            # Partial match: "ESP32" in "ESP32-C3" etc.
            for comp in comp_names:
                if ex_upper and len(ex_upper) >= 3 and ex_upper in comp:
                    score += 2.0
                    break

    # Description word overlap
    desc_tokens = _tokenize(desc)
    overlap = prompt_tokens & desc_tokens
    if overlap:
        score += sum(_weight(t) for t in overlap) * 0.8

    # Name word overlap
    name_tokens = _tokenize(name)
    overlap = prompt_tokens & name_tokens
    if overlap:
        score += sum(_weight(t) for t in overlap)

    return score


def _max_possible_score(template: dict, prompt_tokens: set[str]) -> float:
    """Compute the maximum possible score if all keywords matched perfectly.
    Used as a denominator for normalization."""
    name_kw = set(template.get("keywords", []))
    desc = template.get("description", "")
    name = template.get("name", "")

    max_score = 0.0

    for kw in name_kw:
        kw_tokens = _tokenize(kw)
        if kw_tokens:
            max_score += sum(_weight(t) for t in kw_tokens) * 1.5

    for sub in template.get("subsystems", []):
        sub_tokens = _tokenize(sub.get("subsystem", ""))
        if sub_tokens:
            max_score += sum(_weight(t) for t in sub_tokens) * 1.2

    for sub in template.get("subsystems", []):
        if sub.get("example_components"):
            max_score += 6.0

    desc_tokens = _tokenize(desc)
    if desc_tokens:
        max_score += sum(_weight(t) for t in desc_tokens) * 0.8

    name_tokens = _tokenize(name)
    if name_tokens:
        max_score += sum(_weight(t) for t in name_tokens)

    return max(max_score, 1.0)


# ── Public API ───────────────────────────────────────────────────────────

def find_best_template(prompt: str) -> Optional[TemplateMatch]:
    """Find the best-matching template for a user prompt.

    Returns a TemplateMatch with confidence 0.0–1.0, or None if no template
    matches at all (unlikely since the general-purpose fallback exists).
    """
    templates = _load_templates()
    if not templates:
        return None

    prompt_tokens = _tokenize(prompt)
    comp_names = _component_names(prompt)

    best_score = 0.0
    best_template = None
    best_subsystems = []
    best_nets = []

    for t in templates:
        score = _score_template(t, prompt_tokens, comp_names)
        max_score = _max_possible_score(t, prompt_tokens)
        confidence = score / max_score if max_score > 0 else 0.0

        if confidence > best_score and confidence >= 0.2:
            best_score = confidence
            best_template = t
            best_subsystems = t.get("subsystems", [])
            best_nets = t.get("nets", [])

    if best_template:
        return TemplateMatch(
            template_id=best_template["id"],
            name=best_template["name"],
            confidence=best_score,
            subsystems=best_subsystems,
            nets=best_nets,
        )

    return None


def get_library_filter(sub: dict) -> str:
    """Get the library filter string for a subsystem (from template or default)."""
    lib_filter = sub.get("library_filter", "")
    if lib_filter:
        return lib_filter

    sub_name = sub.get("subsystem", "")
    from agent.library_registry import get_library_filter as _registry_filter
    return _registry_filter(sub_name)
