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
    power_labels: List[dict]
    component_bboxes: dict
    component_placements: List[ComponentPlacement]
    wire_paths: List[WirePath]
    error: Optional[str]