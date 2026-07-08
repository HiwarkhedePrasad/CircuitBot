"""Core data types for the schematic layout pipeline.

These types complement (do not replace) the existing SynthesisGraph types
in agent/synthesis/graph.py.  The pipeline flow:

    SynthesisGraph
        ↓
    Semantic Analyzer → ComponentSemanticInfo
        ↓
    Motif Discovery → Motif
        ↓
    Motif Resolution → resolved Motifs
        ↓
    Functional Blocks → BlockGraph
        ↓
    Constraint Builder → LayoutConstraint
        ↓
    Block Placement → block positions
        ↓
    Expansion (TemplateExpander | ConstraintExpander) → TemplateInstance
        ↓
    Block Optimizer
        ↓
    Layout Scoring → LayoutScore
        ↓
    Wire Generation → WireSegment
        ↓
    Labels + Beautification → Export
"""

from __future__ import annotations

import enum
import uuid
from typing import Any, Optional


# ── Motif types ─────────────────────────────────────────────────────────────


class MotifType(enum.Enum):
    """All detectable functional motifs."""
    DECOUPLING_CAP = "decoupling_cap"
    PULL_UP = "pull_up"
    PULL_DOWN = "pull_down"
    VOLTAGE_DIVIDER = "voltage_divider"
    RC_FILTER = "rc_filter"
    PI_FILTER = "pi_filter"
    POWER_ENTRY = "power_entry"
    LDO_REGULATOR = "ldo_regulator"
    BUCK_CONVERTER = "buck_converter"
    BATTERY_CHARGER = "battery_charger"
    USB_INTERFACE = "usb_interface"
    PROGRAMMING_HEADER = "programming_header"
    I2C_BUS = "i2c_bus"
    CRYSTAL = "crystal"
    RESET_CIRCUIT = "reset_circuit"
    LED_INDICATOR = "led_indicator"
    MOSFET_DRIVER = "mosfet_driver"
    OPAMP = "opamp"
    UNKNOWN = "unknown"


class MotifCategory(enum.Enum):
    """Broad category used for grouping and placement."""
    PASSIVE = "passive"
    POWER = "power"
    INTERFACE = "interface"
    ACTIVE = "active"
    UNKNOWN = "unknown"


# ── Motif ──────────────────────────────────────────────────────────────────


class Motif:
    """A detected functional motif in the circuit.

    Components are claimed exclusively — no two motifs share a component.
    """
    __slots__ = (
        "id", "motif_type", "category", "components", "anchor",
        "pins", "net_signature", "bbox", "score", "template_name",
    )

    def __init__(
        self,
        id: str = "",
        motif_type: MotifType = MotifType.UNKNOWN,
        category: MotifCategory = MotifCategory.UNKNOWN,
        components: Optional[list[str]] = None,
        anchor: str = "",
        pins: Optional[dict[str, str]] = None,
        net_signature: Optional[frozenset[str]] = None,
        score: float = 0.0,
        template_name: str = "",
    ):
        self.id = id or _new_id("motif")
        self.motif_type = motif_type
        self.category = category
        self.components = components or []
        self.anchor = anchor
        self.pins = pins or {}
        self.net_signature = net_signature or frozenset()
        self.bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        self.score = score
        self.template_name = template_name

    @property
    def width(self) -> float:
        return self.bbox[2]

    @property
    def height(self) -> float:
        return self.bbox[3]


# ── Motif signatures (for detection) ────────────────────────────────────────


class PinNetConstraint:
    """A pin on a component should connect to a net of a specific role.

    Used in MotifSignature to define the expected electrical environment.
    """
    __slots__ = ("pin_role", "net_role", "required")

    def __init__(
        self,
        pin_role: str = "",
        net_role: str = "",
        required: bool = True,
    ):
        self.pin_role = pin_role
        self.net_role = net_role
        self.required = required


class SecondarySpec:
    """Specification for a secondary component in a motif.

    Defines what kind of component should be connected to the primary,
    and through which electrical interface.
    """
    __slots__ = (
        "meta", "pin_roles", "connected_pin_role",
        "via_net_role", "required", "label",
    )

    def __init__(
        self,
        meta: Optional[dict[str, set[str]]] = None,
        pin_roles: Optional[set[str]] = None,
        connected_pin_role: Optional[str] = None,
        via_net_role: Optional[str] = None,
        required: bool = True,
        label: str = "",
    ):
        self.meta = meta or {}
        self.pin_roles = pin_roles or set()
        self.connected_pin_role = connected_pin_role
        self.via_net_role = via_net_role
        self.required = required
        self.label = label


class MotifSignature:
    """Signature for detecting a motif topology in the circuit graph.

    Every motif in the catalog is defined by one of these signatures.
    The detector walks the graph, finds candidates matching each signature,
    scores them, and resolves overlaps.
    """
    __slots__ = (
        "name", "motif_type", "category", "priority",
        "primary_meta", "primary_pin_roles",
        "pin_net_constraints", "secondaries",
        "anchor", "base_score", "template_name",
    )

    def __init__(
        self,
        name: str = "",
        motif_type: MotifType = MotifType.UNKNOWN,
        category: MotifCategory = MotifCategory.UNKNOWN,
        priority: int = 5,
        primary_meta: Optional[dict[str, set[str]]] = None,
        primary_pin_roles: Optional[set[str]] = None,
        pin_net_constraints: Optional[list[PinNetConstraint]] = None,
        secondaries: Optional[list[SecondarySpec]] = None,
        anchor: str = "primary",
        base_score: float = 10.0,
        template_name: str = "",
    ):
        self.name = name
        self.motif_type = motif_type
        self.category = category
        self.priority = priority
        self.primary_meta = primary_meta or {}
        self.primary_pin_roles = primary_pin_roles or set()
        self.pin_net_constraints = pin_net_constraints or []
        self.secondaries = secondaries or []
        self.anchor = anchor
        self.base_score = base_score
        self.template_name = template_name or motif_type.value

    def __repr__(self) -> str:
        return f"MotifSignature({self.name}, priority={self.priority})"


# ── Semantic model ──────────────────────────────────────────────────────────


class ComponentSemanticInfo:
    """Reusable semantic metadata attached to each component.

    Produced once by the semantic analyzer, consumed by every downstream stage.
    """
    __slots__ = (
        "ref_des", "role", "domain", "owner", "importance",
        "signal_direction", "placement_priority", "intent", "subsystem",
    )

    def __init__(
        self,
        ref_des: str = "",
        role: str = "",
        domain: str = "",
        owner: str = "",
        importance: float = 0.0,
        signal_direction: str = "",
        placement_priority: int = 5,
        intent: str = "",
        subsystem: str = "",
    ):
        self.ref_des = ref_des
        self.role = role                # e.g. "controller", "regulator", "sensor"
        self.domain = domain            # e.g. "3V3", "5V", "VBAT" — power domain
        self.owner = owner              # ref_des of the owning controller
        self.importance = importance    # 0.0–1.0
        self.signal_direction = signal_direction  # "input", "output", "processing", "bidirectional"
        self.placement_priority = placement_priority  # 0 (highest) to 10 (lowest)
        self.intent = intent            # e.g. "power_usb", "data_usb", "debug_uart"
        self.subsystem = subsystem      # e.g. "power_supply", "main_mcu", "sensor_frontend"


class SemanticModel:
    """Container for all semantic information produced by the analyzer."""
    __slots__ = ("components", "power_domains", "controller", "signal_flow")

    def __init__(self):
        self.components: dict[str, ComponentSemanticInfo] = {}
        self.power_domains: dict[str, list[str]] = {}   # domain → list of ref_des
        self.controller: Optional[str] = None            # ref_des of main controller
        self.signal_flow: dict[str, str] = {}            # ref_des → "left_right" | "top_bottom"


# ── Blocks ──────────────────────────────────────────────────────────────────


class BlockRole(enum.Enum):
    """Semantic role assigned to a functional block."""
    POWER_SOURCE = "power_source"
    POWER_CONDITIONING = "power_conditioning"
    CONTROLLER = "controller"
    INTERFACE = "interface"
    SENSOR = "sensor"
    ACTUATOR = "actuator"
    SIGNAL_CONDITIONING = "signal_conditioning"
    PASSIVE_NETWORK = "passive_network"
    UNKNOWN = "unknown"


class FunctionalBlock:
    """A group of motifs and orphan components forming a functional unit.

    Blocks are the unit of placement — every component belongs to exactly one block.
    """
    __slots__ = (
        "id", "name", "role", "motifs", "orphan_components",
        "component_refs", "anchor", "signal_flow", "position",
        "bbox", "orientation",
    )

    def __init__(
        self,
        id: str = "",
        name: str = "",
        role: BlockRole = BlockRole.UNKNOWN,
        motifs: Optional[list[str]] = None,
        orphan_components: Optional[list[str]] = None,
        anchor: str = "",
        signal_flow: str = "left_right",
    ):
        self.id = id or _new_id("block")
        self.name = name
        self.role = role
        self.motifs = motifs or []
        self.orphan_components = orphan_components or []
        self.component_refs: set[str] = set()
        self.anchor = anchor
        self.signal_flow = signal_flow
        self.position: tuple[float, float] = (0.0, 0.0)
        self.bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        self.orientation: str = "horizontal"

    def all_components(self) -> list[str]:
        return sorted(self.component_refs)

    def __repr__(self) -> str:
        return f"FunctionalBlock(id={self.id}, role={self.role.value}, name={self.name})"


class BlockEdge:
    """A directed connection between two blocks."""
    __slots__ = ("source_id", "target_id", "net", "signal_type")

    def __init__(self, source_id: str, target_id: str, net: str = "",
                 signal_type: str = "signal"):
        self.source_id = source_id
        self.target_id = target_id
        self.net = net
        self.signal_type = signal_type  # "power", "signal", "ground"


class BlockGraph:
    """A directed graph of functional blocks."""
    __slots__ = ("blocks", "edges", "hierarchy")

    def __init__(self):
        self.blocks: dict[str, FunctionalBlock] = {}
        self.edges: list[BlockEdge] = []
        self.hierarchy: dict[str, str] = {}  # block_id → parent_block_id

    def add_block(self, block: FunctionalBlock):
        self.blocks[block.id] = block

    def add_edge(self, edge: BlockEdge):
        self.edges.append(edge)

    def topological_order(self) -> list[str]:
        """Return block IDs in topological order (power → controller → output)."""
        in_degree: dict[str, int] = {bid: 0 for bid in self.blocks}
        adj: dict[str, list[str]] = {bid: [] for bid in self.blocks}
        for e in self.edges:
            if e.source_id in adj and e.target_id in in_degree:
                adj[e.source_id].append(e.target_id)
                in_degree[e.target_id] += 1

        queue = [bid for bid, deg in in_degree.items() if deg == 0]
        ordered: list[str] = []
        while queue:
            node = queue.pop(0)
            ordered.append(node)
            for nb in adj.get(node, []):
                in_degree[nb] -= 1
                if in_degree[nb] == 0:
                    queue.append(nb)
        remaining = [bid for bid in self.blocks if bid not in ordered]
        ordered.extend(remaining)
        return ordered


# ── Layout rules ────────────────────────────────────────────────────────────


class RuleSubjectType(enum.Enum):
    """What kind of element a rule applies to."""
    COMPONENT_CLASS = "component_class"
    MOTIF_TYPE = "motif_type"
    BLOCK_ROLE = "block_role"
    NET_ROLE = "net_role"
    PIN_ROLE = "pin_role"


class RulePredicate(enum.Enum):
    """Relationship enforced by a design rule."""
    WITHIN_DISTANCE = "within_distance"
    LEFT_OF = "left_of"
    RIGHT_OF = "right_of"
    ABOVE = "above"
    BELOW = "below"
    ADJACENT_TO = "adjacent_to"
    TOUCHING = "touching"
    INLINE_WITH = "inline_with"
    FACING_EDGE = "facing_edge"
    FLOW_DIRECTION = "flow_direction"
    ORIENTATION = "orientation"
    ALIGNED_WITH = "aligned_with"
    SAME_ROW = "same_row"
    SAME_COLUMN = "same_column"


class LayoutRule:
    """A design rule that guides placement and wiring.

    All pipeline stages query these rules instead of hardcoding behavior.
    """
    __slots__ = (
        "name", "subject_type", "subject_value", "predicate",
        "object_type", "object_value", "params", "priority", "enabled",
    )

    def __init__(
        self,
        name: str = "",
        subject_type: RuleSubjectType = RuleSubjectType.COMPONENT_CLASS,
        subject_value: str = "",
        predicate: RulePredicate = RulePredicate.ADJACENT_TO,
        object_type: RuleSubjectType = RuleSubjectType.COMPONENT_CLASS,
        object_value: str = "",
        params: Optional[dict[str, Any]] = None,
        priority: int = 5,
        enabled: bool = True,
    ):
        self.name = name
        self.subject_type = subject_type
        self.subject_value = subject_value
        self.predicate = predicate
        self.object_type = object_type
        self.object_value = object_value
        self.params = params or {}
        self.priority = priority
        self.enabled = enabled

    def __repr__(self) -> str:
        return (f"LayoutRule({self.name}: {self.subject_value} "
                f"{self.predicate.value} {self.object_value})")


# Default rules shipped with the engine
BUILTIN_RULES: list[LayoutRule] = [
    LayoutRule(
        name="crystal_near_mcu",
        subject_type=RuleSubjectType.COMPONENT_CLASS,
        subject_value="crystal",
        predicate=RulePredicate.WITHIN_DISTANCE,
        object_type=RuleSubjectType.COMPONENT_CLASS,
        object_value="microcontroller",
        params={"max_distance_mm": 15.0},
        priority=9,
    ),
    LayoutRule(
        name="decoupling_at_power_pin",
        subject_type=RuleSubjectType.MOTIF_TYPE,
        subject_value="decoupling_cap",
        predicate=RulePredicate.TOUCHING,
        object_type=RuleSubjectType.PIN_ROLE,
        object_value="power_in",
        priority=8,
    ),
    LayoutRule(
        name="signal_flow_left_right",
        subject_type=RuleSubjectType.BLOCK_ROLE,
        subject_value="controller",
        predicate=RulePredicate.FLOW_DIRECTION,
        object_type=RuleSubjectType.BLOCK_ROLE,
        object_value="interface",
        params={"direction": "left_right"},
        priority=5,
    ),
    LayoutRule(
        name="power_flow_top_bottom",
        subject_type=RuleSubjectType.BLOCK_ROLE,
        subject_value="power_source",
        predicate=RulePredicate.FLOW_DIRECTION,
        object_type=RuleSubjectType.BLOCK_ROLE,
        object_value="power_conditioning",
        params={"direction": "top_bottom"},
        priority=5,
    ),
    LayoutRule(
        name="connector_facing_edge",
        subject_type=RuleSubjectType.COMPONENT_CLASS,
        subject_value="connector",
        predicate=RulePredicate.FACING_EDGE,
        object_type=RuleSubjectType.COMPONENT_CLASS,
        object_value="connector",
        params={"edge": "left"},
        priority=4,
    ),
    LayoutRule(
        name="led_resistor_inline",
        subject_type=RuleSubjectType.MOTIF_TYPE,
        subject_value="led_indicator",
        predicate=RulePredicate.INLINE_WITH,
        object_type=RuleSubjectType.MOTIF_TYPE,
        object_value="led_indicator",
        priority=6,
    ),
    LayoutRule(
        name="pull_up_vertical",
        subject_type=RuleSubjectType.MOTIF_TYPE,
        subject_value="pull_up",
        predicate=RulePredicate.ORIENTATION,
        object_type=RuleSubjectType.MOTIF_TYPE,
        object_value="pull_up",
        params={"orientation": "vertical"},
        priority=3,
    ),
]


# ── Layout constraints ──────────────────────────────────────────────────────


class Predicate(enum.Enum):
    """Placement relationship between two elements."""
    LEFT_OF = "left_of"
    RIGHT_OF = "right_of"
    ABOVE = "above"
    BELOW = "below"
    ADJACENT_TO = "adjacent_to"
    NEAR = "near"
    FAR_FROM = "far_from"
    SAME_ROW = "same_row"
    SAME_COLUMN = "same_column"
    ALIGNED_LEFT = "aligned_left"
    ALIGNED_RIGHT = "aligned_right"
    ALIGNED_TOP = "aligned_top"
    ALIGNED_BOTTOM = "aligned_bottom"


class LayoutConstraint:
    """A specific placement constraint between two layout elements.

    Constraints are generated by the Constraint Builder from LayoutRules
    and are consumed by the Block Placement stage.
    """
    __slots__ = ("id", "subject_id", "predicate", "object_id", "weight", "metadata")

    def __init__(
        self,
        subject_id: str,
        predicate: Predicate,
        object_id: str,
        weight: float = 1.0,
        metadata: Optional[dict[str, Any]] = None,
    ):
        self.id = _new_id("constraint")
        self.subject_id = subject_id
        self.predicate = predicate
        self.object_id = object_id
        self.weight = weight
        self.metadata = metadata or {}

    def __repr__(self) -> str:
        return (f"LayoutConstraint({self.subject_id} "
                f"{self.predicate.value} {self.object_id})")


# ── Templates ──────────────────────────────────────────────────────────────


class TemplateComponent:
    """A component placement within a template, relative to the anchor."""
    __slots__ = ("ref", "offset_x", "offset_y", "rotation", "pin_connections")

    def __init__(
        self,
        ref: str = "",
        offset_x: float = 0.0,
        offset_y: float = 0.0,
        rotation: float = 0.0,
        pin_connections: Optional[list[tuple[str, str, str]]] = None,
    ):
        self.ref = ref
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.rotation = rotation
        self.pin_connections = pin_connections or []


class TemplateWire:
    """A wire within a template, defined by offsets relative to the anchor."""
    __slots__ = ("from_pin", "to_pin", "path_offsets")

    def __init__(
        self,
        from_pin: str = "",
        to_pin: str = "",
        path_offsets: Optional[list[tuple[float, float]]] = None,
    ):
        self.from_pin = from_pin
        self.to_pin = to_pin
        self.path_offsets = path_offsets or []


class TemplateLayout:
    """A deterministic layout definition for a motif type.

    Templates define component positions and internal wires as offsets
    relative to the anchor component.  The same logical template can
    produce multiple TemplateInstances (horizontal, vertical, mirrored).
    """
    __slots__ = ("motif_type", "variant", "components", "wires")

    def __init__(
        self,
        motif_type: MotifType = MotifType.UNKNOWN,
        variant: str = "default",
        components: Optional[list[TemplateComponent]] = None,
        wires: Optional[list[TemplateWire]] = None,
    ):
        self.motif_type = motif_type
        self.variant = variant
        self.components = components or []
        self.wires = wires or []


class TemplateInstance:
    """An instantiated template at a specific sheet position.

    Produced by TemplateExpander.expand().
    """
    __slots__ = ("template", "position", "rotation", "placements", "wires")

    def __init__(
        self,
        template: Optional[TemplateLayout] = None,
        position: tuple[float, float] = (0.0, 0.0),
        rotation: float = 0.0,
        placements: Optional[dict[str, tuple[float, float, float]]] = None,
        wires: Optional[list[WireSegment]] = None,
    ):
        self.template = template
        self.position = position
        self.rotation = rotation
        self.placements: dict[str, tuple[float, float, float]] = placements or {}
        self.wires: list[WireSegment] = wires or []


# ── Wires ──────────────────────────────────────────────────────────────────


class WireSegment:
    """A geometric wire segment between two pins.

    Points are (x, y) tuples on the schematic sheet.
    The path includes the start and end pin positions.
    """
    __slots__ = ("source", "target", "net", "points", "width_mm", "metadata")

    def __init__(
        self,
        source: str = "",
        target: str = "",
        net: str = "",
        points: Optional[list[tuple[float, float]]] = None,
        width_mm: float = 0.254,
        metadata: Optional[dict[str, Any]] = None,
    ):
        self.source = source
        self.target = target
        self.net = net
        self.points = points or []
        self.width_mm = width_mm
        self.metadata = metadata or {}


# ── Scoring ─────────────────────────────────────────────────────────────────


class LayoutScore:
    """Score for a candidate layout.

    Lower is better (represents penalties).
    """
    __slots__ = (
        "total", "crossings", "bends", "wire_length",
        "symmetry", "alignment", "signal_flow",
        "rule_violations", "details",
    )

    def __init__(
        self,
        total: float = 0.0,
        crossings: int = 0,
        bends: int = 0,
        wire_length: float = 0.0,
        symmetry: float = 0.0,
        alignment: float = 0.0,
        signal_flow: float = 0.0,
        rule_violations: int = 0,
        details: Optional[dict[str, Any]] = None,
    ):
        self.total = total
        self.crossings = crossings
        self.bends = bends
        self.wire_length = wire_length
        self.symmetry = symmetry
        self.alignment = alignment
        self.signal_flow = signal_flow
        self.rule_violations = rule_violations
        self.details = details or {}

    def __repr__(self) -> str:
        return f"LayoutScore(total={self.total:.1f})"


# ── Export data ─────────────────────────────────────────────────────────────


class SchematicOutput:
    """Final output of the schematic layout pipeline.

    Matches the AgentState contract expected by kicad_export.py.
    """
    __slots__ = ("component_placements", "wire_paths", "power_labels")

    def __init__(
        self,
        component_placements: Optional[list[dict]] = None,
        wire_paths: Optional[list[dict]] = None,
        power_labels: Optional[list[dict]] = None,
    ):
        self.component_placements: list[dict] = component_placements or []
        self.wire_paths: list[dict] = wire_paths or []
        self.power_labels: list[dict] = power_labels or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_placements": self.component_placements,
            "wire_paths": self.wire_paths,
            "power_labels": self.power_labels,
            "_placement_locked": True,
        }


# ── Layout context ──────────────────────────────────────────────────────────


class LayoutContext:
    """Central context threaded through the entire layout pipeline.

    Every stage reads from and writes to this context instead of
    receiving scattered arguments.  This keeps the pipeline decoupled
    and makes it easy to add new stages.
    """
    __slots__ = (
        "synthesis_graph", "semantic_model", "motifs", "resolved_motifs",
        "block_graph", "placements", "templates", "constraints", "rules",
        "scores", "wires", "output", "sheet_size", "grid_spacing",
        "metadata",
    )

    def __init__(
        self,
        sheet_size: tuple[float, float] = (841.0, 594.0),  # A1 default
        grid_spacing: float = 1.27,  # 50 mil
    ):
        self.synthesis_graph: Any = None          # SynthesisGraph instance
        self.semantic_model: SemanticModel = SemanticModel()
        self.motifs: list[Motif] = []
        self.resolved_motifs: list[Motif] = []
        self.block_graph: BlockGraph = BlockGraph()
        self.placements: dict[str, tuple[float, float]] = {}
        self.templates: list[TemplateLayout] = []
        self.constraints: list[LayoutConstraint] = []
        self.rules: list[LayoutRule] = list(BUILTIN_RULES)
        self.scores: list[LayoutScore] = []
        self.wires: list[WireSegment] = []
        self.output: Optional[SchematicOutput] = None
        self.sheet_size = sheet_size
        self.grid_spacing = grid_spacing
        self.metadata: dict[str, Any] = {}


# ── Helpers ─────────────────────────────────────────────────────────────────


def _new_id(prefix: str = "") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
