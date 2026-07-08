"""Schematic layout engine — deterministic, motif-driven, constraint-based.

Pipeline:
    SynthesisGraph → Semantic Analyzer → Motif Discovery → Motif Resolution
    → Functional Blocks → Constraint Builder → Block Placement → Expansion
    → Block Optimizer → Layout Scoring → Wire Generation → Beautification → Export
"""

from agent.schematic.schematic_types import (
    MotifType, MotifCategory, BlockRole, RuleSubjectType, RulePredicate,
    Predicate, Motif, PinNetConstraint, SecondarySpec, MotifSignature,
    ComponentSemanticInfo, SemanticModel, FunctionalBlock, BlockEdge,
    BlockGraph, LayoutRule, LayoutConstraint, TemplateComponent,
    TemplateWire, TemplateLayout, TemplateInstance, WireSegment,
    LayoutScore, SchematicOutput, LayoutContext, BUILTIN_RULES,
)
from agent.schematic.matcher import (
    CandidateMatch, discover_candidates, matches_meta, has_pin_roles,
)
from agent.schematic.detector import detect_motifs, find_orphan_components
from agent.schematic.blocks import build_block_graph
from agent.schematic.placement import place_blocks, generate_constraints
from agent.schematic.templates import get_template, list_variants, has_template
from agent.schematic.expander import Expander, TemplateExpander, ConstraintExpander
from agent.schematic.optimizer import optimize
from agent.schematic.scoring import score_layout, generate_candidates, pick_best
from agent.schematic.wires import generate_wires
from agent.schematic.labels import place_labels
from agent.schematic.beautify import beautify
from agent.schematic.export_adapter import export

__all__ = [
    "MotifType", "MotifCategory", "BlockRole", "RuleSubjectType",
    "RulePredicate", "Predicate", "Motif", "PinNetConstraint",
    "SecondarySpec", "MotifSignature", "ComponentSemanticInfo",
    "SemanticModel", "FunctionalBlock", "BlockEdge", "BlockGraph",
    "LayoutRule", "LayoutConstraint", "TemplateComponent",
    "TemplateWire", "TemplateLayout", "TemplateInstance", "WireSegment",
    "LayoutScore", "SchematicOutput", "LayoutContext", "BUILTIN_RULES",
    "CandidateMatch", "discover_candidates", "matches_meta", "has_pin_roles",
    "detect_motifs", "find_orphan_components",
    "build_block_graph", "place_blocks", "generate_constraints",
    "get_template", "list_variants", "has_template",
    "Expander", "TemplateExpander", "ConstraintExpander",
    "optimize", "score_layout", "generate_candidates", "pick_best",
    "generate_wires", "place_labels", "beautify", "export",
]
