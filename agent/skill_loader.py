"""Skill loader for CircuitBot pipeline.

Injects domain-specific SKILL.md content into LLM system prompts.
Each pipeline stage loads its own specialized skill + relevant sections
from the core electronics-domain skill.
"""

from pathlib import Path
from functools import lru_cache
import re

_SKILLS_BASE = Path(__file__).resolve().parent.parent / ".agents" / "skills"

_STAGE_TO_SKILL = {
    "clarify": "circuitbot-clarify",
    "analyze": "circuitbot-analyzer",
    "rerank": "circuitbot-reranker",
    "validate": "circuitbot-validator",
    "netlist": "circuitbot-netlist",
    "design_review": "circuitbot-design-review",
    "modify_classify": "circuitbot-modify",
    "prompt_router": "circuitbot-router",
    "modify": "circuitbot-modify",
    "datasheet_extend": "",
}

_CORE_SECTIONS_PER_STAGE = {
    "clarify": ["Component Selection Rules"],
    "analyze": ["Component Selection Rules", "Board Type Rules"],
    "rerank": ["Component Selection Rules", "Pipeline-Specific Behavior", "KiCad Library Conventions"],
    "validate": ["Component Selection Rules", "Pipeline-Specific Behavior", "KiCad Library Conventions", "Electrical Design Patterns"],
    "netlist": ["KiCad Library Conventions", "Electrical Design Patterns"],
    "design_review": ["Electrical Design Patterns", "KiCad Library Conventions"],
    "modify_classify": ["Component Selection Rules", "Pipeline-Specific Behavior"],
    "modify": ["Component Selection Rules", "Pipeline-Specific Behavior"],
    "prompt_router": [],
    "datasheet_extend": [],
}


def _strip_frontmatter(content: str) -> str:
    lines = content.split("\n")
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "\n".join(lines[i + 1:])
    return content


@lru_cache(maxsize=16)
def _read_skill(skill_name: str) -> str:
    path = _SKILLS_BASE / skill_name / "SKILL.md"
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8")
    return _strip_frontmatter(content).strip()


def _extract_sections(content: str, section_names: list[str]) -> str:
    if not section_names or not content:
        return ""

    lines = content.split("\n")
    parts: list[str] = []
    current: list[str] = []
    capturing = False

    for line in lines:
        if line.startswith("## "):
            if capturing and current:
                parts.append("\n".join(current))
            current = []
            name = line.lstrip("# ").strip()
            capturing = any(name.startswith(s) for s in section_names)
        if capturing:
            current.append(line)

    if capturing and current:
        parts.append("\n".join(current))

    return "\n\n".join(parts)


def load_skill_for_stage(stage: str) -> str:
    """Return skill content to inject for a given pipeline stage.

    Returns a formatted string to prepend to the LLM system prompt,
    or empty string if no skill is configured for this stage.
    """
    fragments = []

    # 1. Core domain knowledge (relevant sections)
    core = _read_skill("circuitbot-electronics-domain")
    sections = _CORE_SECTIONS_PER_STAGE.get(stage, [])
    core_content = _extract_sections(core, sections)
    if core_content:
        fragments.append("[CircuitBot Electronics Knowledge]\n" + core_content)

    # 2. Stage-specific skill
    skill_name = _STAGE_TO_SKILL.get(stage, "")
    if skill_name:
        stage_skill = _read_skill(skill_name)
        if stage_skill:
            fragments.append(f"[CircuitBot {stage} Knowledge]\n" + stage_skill)

    if not fragments:
        return ""

    return "\n\n---\n\n".join(fragments) + "\n\n---\n"


def reload_skills():
    """Clear caches so next call re-reads from disk."""
    _read_skill.cache_clear()
