"""Reasoning Engine — engineering principle knowledge graph.

Maps user goals to engineering principles, then to capabilities and constraint checks.
This is a DATA STRUCTURE, not an LLM call. Makes planning deterministic and explainable.

Example:
    "Reduce EMI" → ["minimize_loop_area", "continuous_ground_return"]
                 → [routing_capability, placement_capability]
                 → [trace_length_check, ground_plane_check]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EngineeringPrinciple:
    """A fundamental engineering principle."""
    id: str = ""
    name: str = ""
    description: str = ""
    sub_principles: list[str] = field(default_factory=list)  # IDs of child principles
    capabilities: list[str] = field(default_factory=list)    # capability names this maps to
    constraints: list[str] = field(default_factory=list)     # constraint checks to run
    weight: float = 1.0  # importance weight (0-1)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "sub_principles": self.sub_principles, "capabilities": self.capabilities,
            "constraints": self.constraints, "weight": self.weight,
        }


@dataclass
class CapabilityRef:
    """Reference to a capability that can be invoked."""
    name: str = ""
    description: str = ""
    input_types: list[str] = field(default_factory=list)
    output_types: list[str] = field(default_factory=list)
    estimated_cost: float = 1.0  # relative cost (1.0 = normal)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "description": self.description,
            "input_types": self.input_types, "output_types": self.output_types,
            "estimated_cost": self.estimated_cost,
        }


@dataclass
class CheckRef:
    """Reference to a constraint check to run."""
    name: str = ""
    check_type: str = ""  # "deterministic", "simulation", "llm"
    description: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "check_type": self.check_type, "description": self.description}


# ── Engineering Principle Knowledge Graph ───────────────────────────────

PRINCIPLES: dict[str, EngineeringPrinciple] = {
    # Cost optimization
    "reduce_bom_cost": EngineeringPrinciple(
        id="reduce_bom_cost",
        name="Reduce BOM Cost",
        description="Minimize total bill of materials cost",
        sub_principles=["reduce_component_count", "use_cheaper_parts", "reduce_layer_count"],
        capabilities=["bom_analysis", "component_substitution", "stackup_optimization"],
        constraints=["cost_per_component", "total_bom_cost"],
    ),
    "reduce_component_count": EngineeringPrinciple(
        id="reduce_component_count",
        name="Reduce Component Count",
        description="Merge or eliminate unnecessary components",
        capabilities=["bom_analysis", "functional_duplicate_detection"],
        constraints=["component_usage"],
    ),
    "use_cheaper_parts": EngineeringPrinciple(
        id="use_cheaper_parts",
        name="Use Cheaper Parts",
        description="Find lower-cost alternatives with same functionality",
        capabilities=["component_substitution", "alternative_search"],
        constraints=["pin_compatibility", "functional_equivalence"],
    ),
    "reduce_layer_count": EngineeringPrinciple(
        id="reduce_layer_count",
        name="Reduce Layer Count",
        description="Use fewer PCB layers to reduce manufacturing cost",
        capabilities=["stackup_optimization", "routing_analysis"],
        constraints=["signal_integrity", "power_integrity"],
    ),

    # EMI reduction
    "reduce_emi": EngineeringPrinciple(
        id="reduce_emi",
        name="Reduce EMI",
        description="Minimize electromagnetic interference",
        sub_principles=["minimize_loop_area", "continuous_ground_return", "shield_clock_lines", "split_planes"],
        capabilities=["routing_optimization", "placement_grouping", "stackup_optimization"],
        constraints=["trace_length", "ground_continuity", "clock_routing"],
    ),
    "minimize_loop_area": EngineeringPrinciple(
        id="minimize_loop_area",
        name="Minimize Loop Area",
        description="Keep signal and return paths close together",
        capabilities=["routing_optimization"],
        constraints=["return_path_proximity"],
    ),
    "continuous_ground_return": EngineeringPrinciple(
        id="continuous_ground_return",
        name="Continuous Ground Return",
        description="Ensure unbroken ground plane under all signals",
        capabilities=["routing_optimization", "stackup_optimization"],
        constraints=["ground_plane_continuity"],
    ),
    "shield_clock_lines": EngineeringPrinciple(
        id="shield_clock_lines",
        name="Shield Clock Lines",
        description="Route ground guard traces alongside clock signals",
        capabilities=["routing_optimization"],
        constraints=["clock_shielding"],
    ),
    "split_planes": EngineeringPrinciple(
        id="split_planes",
        name="Split Planes",
        description="Separate analog and digital ground planes",
        capabilities=["placement_grouping", "stackup_optimization"],
        constraints=["plane_separation"],
    ),

    # Thermal management
    "improve_thermal": EngineeringPrinciple(
        id="improve_thermal",
        name="Improve Thermal Performance",
        description="Manage heat dissipation and temperature",
        sub_principles=["increase_copper_area", "add_thermal_vias", "spacing_heat_sources"],
        capabilities=["thermal_analysis", "placement_optimization", "copper_pour"],
        constraints=["junction_temperature", "thermal_resistance"],
    ),
    "increase_copper_area": EngineeringPrinciple(
        id="increase_copper_area",
        name="Increase Copper Area",
        description="Wider traces and larger pads for high-current paths",
        capabilities=["trace_widening", "copper_pour"],
        constraints=["current_capacity", "trace_width"],
    ),
    "add_thermal_vias": EngineeringPrinciple(
        id="add_thermal_vias",
        name="Add Thermal Vias",
        description="Via arrays under power components for heat transfer",
        capabilities=["via_placement"],
        constraints=["thermal_via_density"],
    ),
    "spacing_heat_sources": EngineeringPrinciple(
        id="spacing_heat_sources",
        name="Space Heat Sources",
        description="Keep heat-generating components apart",
        capabilities=["placement_optimization"],
        constraints=["component_spacing", "thermal_coupling"],
    ),

    # Signal integrity
    "improve_signal_integrity": EngineeringPrinciple(
        id="improve_signal_integrity",
        name="Improve Signal Integrity",
        description="Ensure clean signal transmission",
        sub_principles=["impedance_matching", "length_matching", "proper_termination"],
        capabilities=["routing_optimization", "trace_sizing"],
        constraints=["impedance_target", "skew_budget", "termination"],
    ),
    "impedance_matching": EngineeringPrinciple(
        id="impedance_matching",
        name="Impedance Matching",
        description="Match trace impedance to target (e.g., 90Ω for USB)",
        capabilities=["trace_sizing", "stackup_optimization"],
        constraints=["impedance_target"],
    ),
    "length_matching": EngineeringPrinciple(
        id="length_matching",
        name="Length Matching",
        description="Match trace lengths for differential pairs and buses",
        capabilities=["routing_optimization"],
        constraints=["differential_skew", "bus_length_matching"],
    ),
    "proper_termination": EngineeringPrinciple(
        id="proper_termination",
        name="Proper Termination",
        description="Add series/parallel termination for high-speed signals",
        capabilities=["component_addition"],
        constraints=["termination_resistance"],
    ),

    # Power integrity
    "improve_power_integrity": EngineeringPrinciple(
        id="improve_power_integrity",
        name="Improve Power Integrity",
        description="Ensure stable power delivery",
        sub_principles=["decoupling_strategy", "power_trace_sizing", "voltage_regulation"],
        capabilities=["component_addition", "trace_widening", "placement_optimization"],
        constraints=["decoupling_adequacy", "voltage_drop", "current_capacity"],
    ),
    "decoupling_strategy": EngineeringPrinciple(
        id="decoupling_strategy",
        name="Decoupling Strategy",
        description="Place decoupling caps close to IC power pins",
        capabilities=["component_addition", "placement_optimization"],
        constraints=["decoupling_proximity", "decoupling_count"],
    ),
    "power_trace_sizing": EngineeringPrinciple(
        id="power_trace_sizing",
        name="Power Trace Sizing",
        description="Width power traces for required current capacity",
        capabilities=["trace_widening"],
        constraints=["current_capacity", "voltage_drop"],
    ),

    # Reliability
    "improve_reliability": EngineeringPrinciple(
        id="improve_reliability",
        name="Improve Reliability",
        description="Design for long-term operation",
        sub_principles=["derating", "esd_protection", "conformal_coating"],
        capabilities=["component_selection", "component_addition"],
        constraints=["derating_margin", "esd_protection", "operating_temperature"],
    ),
    "derating": EngineeringPrinciple(
        id="derating",
        name="Derating",
        description="Operate components below rated maximums",
        capabilities=["component_selection"],
        constraints=["derating_margin"],
    ),
    "esd_protection": EngineeringPrinciple(
        id="esd_protection",
        name="ESD Protection",
        description="Add TVS diodes on external interfaces",
        capabilities=["component_addition"],
        constraints=["esd_protection"],
    ),
}

# ── Goal → Principle mapping ───────────────────────────────────────────

GOAL_TO_PRINCIPLES: dict[str, list[str]] = {
    "reduce_cost": ["reduce_bom_cost"],
    "reduce_bom_cost": ["reduce_bom_cost"],
    "cheaper": ["reduce_bom_cost"],
    "cost_down": ["reduce_bom_cost"],
    "reduce_emi": ["reduce_emi"],
    "emi": ["reduce_emi"],
    "electromagnetic": ["reduce_emi"],
    "noise": ["reduce_emi"],
    "improve_thermal": ["improve_thermal"],
    "thermal": ["improve_thermal"],
    "temperature": ["improve_thermal"],
    "overheating": ["improve_thermal"],
    "heat": ["improve_thermal"],
    "signal_integrity": ["improve_signal_integrity"],
    "si": ["improve_signal_integrity"],
    "impedance": ["improve_signal_integrity"],
    "usb": ["improve_signal_integrity", "improve_power_integrity"],
    "power_integrity": ["improve_power_integrity"],
    "pi": ["improve_power_integrity"],
    "decoupling": ["improve_power_integrity"],
    "reliability": ["improve_reliability"],
    "robust": ["improve_reliability"],
    "esd": ["improve_reliability"],
}

# ── Capability definitions ──────────────────────────────────────────────

CAPABILITIES: dict[str, CapabilityRef] = {
    "bom_analysis": CapabilityRef(
        name="bom_analysis", description="Analyze bill of materials for cost",
        input_types=["selected_components"], output_types=["cost_report"],
    ),
    "component_substitution": CapabilityRef(
        name="component_substitution", description="Find cheaper alternatives",
        input_types=["selected_components", "constraints"], output_types=["substitution_proposals"],
    ),
    "component_selection": CapabilityRef(
        name="component_selection", description="Select or re-select components",
        input_types=["requirements"], output_types=["selected_components"],
    ),
    "component_addition": CapabilityRef(
        name="component_addition", description="Add supporting components (caps, resistors, TVS)",
        input_types=["selected_components", "requirements"], output_types=["component_additions"],
    ),
    "placement_optimization": CapabilityRef(
        name="placement_optimization", description="Optimize component placement",
        input_types=["board_model", "constraints"], output_types=["placement_updates"],
    ),
    "placement_grouping": CapabilityRef(
        name="placement_grouping", description="Group related components together",
        input_types=["board_model", "grouping_rules"], output_types=["placement_updates"],
    ),
    "routing_optimization": CapabilityRef(
        name="routing_optimization", description="Optimize trace routing",
        input_types=["board_model", "constraints"], output_types=["routing_updates"],
    ),
    "routing_analysis": CapabilityRef(
        name="routing_analysis", description="Analyze routing quality",
        input_types=["board_model"], output_types=["routing_report"],
    ),
    "trace_widening": CapabilityRef(
        name="trace_widening", description="Widen traces for current capacity",
        input_types=["board_model", "current_requirements"], output_types=["trace_updates"],
    ),
    "trace_sizing": CapabilityRef(
        name="trace_sizing", description="Size traces for impedance targets",
        input_types=["board_model", "impedance_targets"], output_types=["trace_updates"],
    ),
    "stackup_optimization": CapabilityRef(
        name="stackup_optimization", description="Optimize layer stackup",
        input_types=["layer_count", "requirements"], output_types=["stackup_updates"],
    ),
    "copper_pour": CapabilityRef(
        name="copper_pour", description="Add copper fill zones",
        input_types=["board_model", "pour_rules"], output_types=["zone_updates"],
    ),
    "thermal_analysis": CapabilityRef(
        name="thermal_analysis", description="Analyze thermal performance",
        input_types=["board_model"], output_types=["thermal_report"],
    ),
    "via_placement": CapabilityRef(
        name="via_placement", description="Place vias (thermal, stitching)",
        input_types=["board_model", "via_rules"], output_types=["via_updates"],
    ),
    "alternative_search": CapabilityRef(
        name="alternative_search", description="Search for alternative components",
        input_types=["component_id", "constraints"], output_types=["alternatives"],
    ),
    "functional_duplicate_detection": CapabilityRef(
        name="functional_duplicate_detection", description="Find redundant components",
        input_types=["selected_components"], output_types=["duplicate_report"],
    ),
}


class ReasoningEngine:
    """Maps goals to engineering principles, capabilities, and constraint checks.

    This is a DATA STRUCTURE, not an LLM call.
    """

    def __init__(self):
        self.principles = dict(PRINCIPLES)
        self.goal_map = dict(GOAL_TO_PRINCIPLES)
        self.capabilities = dict(CAPABILITIES)

    def decompose(self, goal: str) -> list[EngineeringPrinciple]:
        """Map a goal string to engineering principles.

        Args:
            goal: user goal like "reduce board cost" or "improve thermal"

        Returns:
            List of relevant principles with sub-principles expanded.
        """
        goal_lower = goal.lower().strip()
        principle_ids = set()

        # Direct keyword matching
        for keyword, p_ids in self.goal_map.items():
            if keyword in goal_lower:
                principle_ids.update(p_ids)

        # If no match, try partial matching
        if not principle_ids:
            for keyword, p_ids in self.goal_map.items():
                if any(w in goal_lower for w in keyword.split("_")):
                    principle_ids.update(p_ids)

        # Expand sub-principles
        expanded = set()
        for pid in principle_ids:
            p = self.principles.get(pid)
            if p:
                expanded.add(pid)
                for sub_id in p.sub_principles:
                    expanded.add(sub_id)

        # Build result list
        result = []
        for pid in expanded:
            p = self.principles.get(pid)
            if p:
                result.append(p)

        # Sort by weight (most important first)
        result.sort(key=lambda p: p.weight, reverse=True)
        return result

    def map_to_capabilities(self, principles: list[EngineeringPrinciple]) -> list[CapabilityRef]:
        """Map principles to capabilities.

        Returns deduplicated list of capabilities needed.
        """
        cap_names = set()
        for p in principles:
            cap_names.update(p.capabilities)

        caps = []
        for name in cap_names:
            cap = self.capabilities.get(name)
            if cap:
                caps.append(cap)
        return caps

    def suggest_checks(self, goal: str) -> list[CheckRef]:
        """What constraint checks to run for this goal."""
        principles = self.decompose(goal)
        check_names = set()
        for p in principles:
            check_names.update(p.constraints)

        checks = []
        for name in check_names:
            checks.append(CheckRef(
                name=name,
                check_type="deterministic",
                description=f"Check: {name.replace('_', ' ')}",
            ))
        return checks

    def get_all_principles(self) -> list[EngineeringPrinciple]:
        """Get all available principles."""
        return list(self.principles.values())

    def get_all_capabilities(self) -> list[CapabilityRef]:
        """Get all available capabilities."""
        return list(self.capabilities.values())
