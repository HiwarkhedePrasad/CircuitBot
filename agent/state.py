from typing import TypedDict, List, Optional


class ComponentSelection(TypedDict):
    id_str: str
    ref_des: str
    category: str
    description: str
    footprint: str
    pads: list[dict]
    justification: str
    datasheet_text: str


class PinInfo(TypedDict):
    key: str
    name: str
    x: float
    y: float
    ref_des: str
    pin_num: str


class NetlistConnection(TypedDict):
    source: str
    target: str


class WirePath(TypedDict):
    source: str
    target: str
    path: List[dict]


class ComponentPlacement(TypedDict):
    ref_des: str
    x: float
    y: float


class AgentState(TypedDict, total=False):
    prompt: str
    analysis: list
    research_results: List[dict]
    selected_components: List[ComponentSelection]
    component_ops: dict
    pin_matrix: dict
    netlist: List[NetlistConnection]
    nets: List[dict]
    power_pins: List[dict]
    power_labels: Optional[List[dict]]
    component_bboxes: dict
    component_placements: List[ComponentPlacement]
    wire_paths: List[WirePath]
    error: Optional[str]
    retry_count: int
    validation_errors: List[str]
    rejected_ids: Optional[List[str]]
    rejected_families: Optional[List[str]]
    repair_failures: Optional[List[str]]
    trace_constraints: Optional[dict]  # {"net_name": {"width_mm": 0.5, "impedance": 90}}
    pcb_approved: bool
    _stage: str
    _placement_locked: bool             # True after placement node runs once
    _erc_results: Optional[dict]        # ERC output from kicad-cli
    _erc_retries: int                   # how many ERC→repair loops so far
    _erc_pending_connections: Optional[List[dict]]  # [{pin, net}] — attach requests for routing
    _erc_affected_nets: Optional[List[str]]  # net names affected by last ERC repair (targeted re-route)
    _validation_issues: Optional[List[dict]]  # all validation issues collected across stages
    _power_net_repaired: bool           # True after power_net_repair runs
    synthesis_graph: Optional["dict"]   # serialised SynthesisGraph (set by netlist_node)
