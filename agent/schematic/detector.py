"""Motif detection engine — scoring + conflict resolution.

The detector:
  1. Discovers all candidate matches across all signatures
  2. Scores each candidate
  3. Resolves conflicts (overlapping component claims) by highest score
  4. Produces resolved Motif objects

This is NOT a greedy "first match wins" approach.
All candidates are discovered before any claims are made.
"""

from __future__ import annotations

from typing import Any, Optional

from agent.schematic.schematic_types import Motif, MotifSignature
from agent.schematic.matcher import CandidateMatch, discover_candidates


# ── Scoring pass ──────────────────────────────────────────────────────────────


def score_candidates(
    candidates: list[CandidateMatch], graph: Any,
) -> list[CandidateMatch]:
    """Sort candidates by descending score.

    Returns a new list sorted high-to-low.
    """
    return sorted(candidates, key=lambda c: -c.score)


# ── Conflict resolution ──────────────────────────────────────────────────────


def resolve_conflicts(candidates: list[CandidateMatch]) -> list[CandidateMatch]:
    """Resolve overlapping component claims using highest-score wins.

    Algorithm:
      1. Sort candidates by score descending
      2. Take the highest-scoring candidate
      3. Mark its components as claimed
      4. Discard any other candidate that claims any claimed component
      5. Repeat until no candidates remain

    This is deterministic: given the same input, the same candidates
    produce the same resolved set every time.
    """
    sorted_candidates = sorted(candidates, key=lambda c: (-c.score, c.signature.name))
    claimed: set[str] = set()
    resolved: list[CandidateMatch] = []

    for cand in sorted_candidates:
        if cand.all_components & claimed:
            continue
        resolved.append(cand)
        claimed.update(cand.all_components)

    return resolved


# ── Motif construction ───────────────────────────────────────────────────────


def _build_motif(candidate: CandidateMatch) -> Motif:
    """Convert a resolved CandidateMatch into a Motif object."""
    sig = candidate.signature

    pins: dict[str, str] = candidate.secondaries.copy()

    return Motif(
        motif_type=sig.motif_type,
        category=sig.category,
        components=sorted(candidate.all_components),
        anchor=candidate.primary,
        pins=pins,
        score=candidate.score,
        template_name=sig.template_name,
    )


# ── Orchestrator ──────────────────────────────────────────────────────────────


def detect_motifs(
    graph: Any,
    catalog: Optional[list[MotifSignature]] = None,
) -> list[Motif]:
    """Run the full motif detection pipeline.

    Args:
        graph: A SynthesisGraph instance (classification + nets must be set).
        catalog: List of MotifSignature to detect. Defaults to MOTIF_CATALOG
                 from agent.schematic.catalog.

    Returns:
        List of resolved Motif objects with no overlapping component claims.
    """
    if catalog is None:
        from agent.schematic.catalog import MOTIF_CATALOG
        catalog = MOTIF_CATALOG

    # Phase 1: Discover all candidates
    all_candidates: list[CandidateMatch] = []
    for signature in catalog:
        try:
            candidates = discover_candidates(graph, signature)
            all_candidates.extend(candidates)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Error discovering candidates for %s: %s", signature.name, exc,
            )

    # Phase 2: Score / sort
    scored = score_candidates(all_candidates, graph)

    # Phase 3: Resolve conflicts
    resolved = resolve_conflicts(scored)

    # Phase 4: Build Motif objects
    motifs = [_build_motif(c) for c in resolved]

    return motifs


# ── Orphan detection ─────────────────────────────────────────────────────────


def find_orphan_components(graph: Any, motifs: list[Motif]) -> list[str]:
    """Return component ref_des that are NOT claimed by any motif."""
    claimed: set[str] = set()
    for motif in motifs:
        claimed.update(motif.components)

    orphans = [ref for ref in graph.components if ref not in claimed]
    return sorted(orphans)
