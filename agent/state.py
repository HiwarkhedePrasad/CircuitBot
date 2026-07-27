from typing import Any, TypedDict, List, Optional, Literal


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


class ValidationError(TypedDict):
    code: str                  # e.g. "DUP_MCU", "MISSING_DECOUPLING"
    category: str              # "fatal", "repairable", "warning"
    component_id: Optional[str]
    message: str
    suggested_fix: Optional[str]


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
    connection_records: Optional[List[dict]]
    net_labels: Optional[List[dict]]
    error: Optional[str]
    retry_count: int
    validation_errors: List[str]
    validation_warnings: List[str]
    _validation_help_result: Optional[dict]
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
    _validation_error_detail: Optional[str]  # Detailed error for ask_validation_help
    _builtin_components: Optional[List[dict]]  # Built-in components from capability_resolver
    _prev_erc_error_count: int          # Previous ERC error count for loop detection
    synthesis_graph: Optional[Any]      # live SynthesisGraph object (set by dispatch_node or netlist_node)
    knowledge_db: Optional[dict]        # live knowledge extraction per component (set by dispatch_node)
    layer_count: int                    # PCB layer count (2, 4, 6, or 8) — set by ask_board_config
    review_suggestions: Optional[List[dict]]  # Design review suggestions
    # Modification fields (for modify_design intent)
    modification_type: Optional[str]    # value_change, part_swap, add_component, remove_component, net_modify, reroute
    modification_target: Optional[dict]  # {ref: "R1"} or {net: "VCC"}
    modification_value: Optional[dict]  # {"value": "10k"} or {"part_id": "C1234"}
    original_design: Optional[dict]     # Snapshot of LAST_DESIGN before modification
    web_research_results: Optional[List[dict]]  # Phase 1: web research per subsystem
    deep_research_results: Optional[List[dict]]  # Deep research: structured per-subsystem recommendations
    datasheet_search_results: Optional[List[dict]]  # Phase 2: datasheet research per component
    connection_search_results: Optional[List[dict]]  # Phase 3: connection/wiring research
    review_suggestions: Optional[List[dict]]  # Design review suggestions
    # Clarification fields (pre-generation question flow)
    clarification_needed: bool           # True if clarify_node found gaps in prompt
    clarification_questions: list        # List of question dicts [{id, question, options}]
    clarification_answers: dict          # User's answers {q1: "ESP32", q2: "I2C", ...}
    # ── Circuit type detection (set by analyze_node) ──
    circuit_type: Literal["mcu_based", "ic_based", "analog_only", "mixed"]  # what kind of circuit
    requires_mcu: bool                                 # True if circuit needs a microcontroller
    primary_ic: Optional[str]                          # e.g. "NE555" — for ic_based circuits
    # ── Architecture lock (set by architecture_planner_node) ──
    architecture_frozen: bool                          # True after arch planner runs
    board_type: Literal["devkit", "module", "bare_ic", "custom_pcb"]  # Board class
    primary_mcu: str                                   # e.g. "ESP32-C3" — frozen selection (empty for non-MCU)
    mcu_platform: str                                  # e.g. "espressif" — derived from primary_mcu
    provides: dict                                     # e.g. {"usb_to_serial": True, "regulator_3v3": True}
    # ── Component ownership (set by dependency_expander_node) ──
    ownership_graph: dict[str, list[str]]              # component_id → [owned_capability_ids]
    capability_sources: dict[str, str]                 # capability_id → component_id that provides it
    # ── Error classification (set by constraint_checker_node) ──
    fatal_errors: List[ValidationError]                # Cannot be repaired, must re-select
    repairable_errors: List[ValidationError]           # Can be repaired, max 2 passes
    # ── Freeze gate (set by freeze_component_list_node) ──
    component_list_frozen: bool                        # True after freeze
    # ── Template matching (set by analyze_node) ──
    template_id: Optional[str]                         # Matched template ID, if any
    template_nets: Optional[list[dict]]                # Pre-defined nets from template
    # ── Repair tracking ──
    repair_passes_used: int                            # 0, 1, or 2
    repair_history: list                               # [{pass: 1, changed: [...], reason: "..."}]
    repair_source: Optional[str]                       # node that triggered repair: "constraint_checker" or "post_validate"
    bandit_state: Optional[dict]                       # ThompsonBandit state dict {alpha: {}, beta: {}}
    # ── Dispatch tracking ──
    _skipped_components: Optional[List[dict]]          # components that failed symbol dispatch
    _last_validated_component_count: int               # count at last validation pass
    synthesis_graph_error: Optional[str]               # error message if SynthesisGraph build failed
    knowledge_db_error: Optional[str]                  # error message if knowledge extraction failed
    # ── LLM Judge evaluation (post-design_review quality gate) ──
    judge_evaluation: Optional[dict]                   # {completeness: {score, justification}, ...}
    # ── Schematic sync tracking ──
    import_source_path: Optional[str]                  # path to last imported .kicad_sch
    import_checksum: Optional[str]                     # SHA-256 of last imported file
    sync_history: Optional[List[dict]]                 # log of sync operations
