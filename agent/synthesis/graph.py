"""Canonical graph model for circuit synthesis.

Every stage operates on this graph.  Nothing mutates raw netlists directly.

A SynthesisGraph has:
  - ComponentNode per physical component
  - PinNode per physical pin (resolved once to a PinRole)
  - NetNode per logical net
  - ConstraintEdge per topology relationship

Downstream code never matches pin names by string — use PinRole instead.
"""

from __future__ import annotations

import enum
from typing import Any, Optional


# ── Enums (no string matching downstream) ──────────────────────────────────


class PinRole(enum.Enum):
    """Semantic role of a pin, resolved once by the classifier.

    Every downstream system uses pin.role instead of regex/string matching.
    """
    # Power / ground
    POWER_IN = "power_in"
    POWER_OUT = "power_out"
    GND = "ground"
    VREF = "vref"

    # Passive / signal
    PASSIVE = "passive"
    INPUT = "input"
    OUTPUT = "output"
    BIDIRECTIONAL = "bidirectional"
    OPEN_COLLECTOR = "open_collector"
    OPEN_EMITTER = "open_emitter"
    TRI_STATE = "tri_state"

    # Specific functions (resolved from pin names + symbol metadata)
    ANODE = "anode"
    CATHODE = "cathode"
    GATE = "gate"
    DRAIN = "drain"
    SOURCE = "source"
    BASE = "base"
    EMITTER = "emitter"
    COLLECTOR = "collector"

    VIN = "vin"
    VOUT = "vout"
    EN = "enable"
    FB = "feedback"
    COMP = "compensation"
    BOOT = "bootstrap"
    PG = "power_good"

    SDA = "sda"
    SCL = "scl"
    TX = "tx"
    RX = "rx"
    RTS = "rts"
    CTS = "cts"

    RESET = "reset"
    OSC_IN = "osc_in"
    OSC_OUT = "osc_out"
    CLOCK = "clock"

    NC = "no_connect"
    UNUSED = "unused"

    @classmethod
    def from_pin_name(cls, name: str, etype: str | None = None,
                      pin_count: int = 0, other_names: set[str] | None = None) -> PinRole:
        """Heuristic classification from pin metadata.

        This is the **last resort** — the preferred path is library metadata.
        """
        cleaned = name.strip().upper() if name else ""
        if not cleaned or cleaned in ("NC", "NO_CONNECT", "N/C", "NOCONNECT", "~"):
            return cls.NC
        if cleaned in ("GND", "VSS", "VEE", "AGND", "DGND", "PGND", "EPAD", "SHIELD", "0V"):
            return cls.GND
        if cleaned == "VIN":
            return cls.VIN
        if cleaned == "VOUT":
            return cls.VOUT
        if cleaned in ("VCC", "VDD", "VBAT", "VSYS", "VUSB", "VBUS", "5V", "3V3", "3.3V", "1V8", "1.2V"):
            return cls.POWER_IN
        if cleaned in ("VOUT", "AVDD", "AVCC", "DVDD", "VREFP", "VREFN"):
            return cls.POWER_OUT if etype == "power_out" else cls.POWER_IN
        if cleaned in ("A", "ANODE"):
            return cls.ANODE
        if cleaned in ("K", "CATHODE"):
            return cls.CATHODE
        if cleaned in ("G", "GATE"):
            return cls.GATE
        if cleaned in ("D", "DRAIN"):
            return cls.DRAIN
        if cleaned in ("S", "SOURCE"):
            return cls.SOURCE
        if cleaned in ("B", "BASE"):
            return cls.BASE
        if cleaned in ("E", "EMITTER"):
            return cls.EMITTER
        if cleaned in ("C", "COLLECTOR") and pin_count <= 3:
            return cls.COLLECTOR
        if cleaned in ("SDA",):
            return cls.SDA
        if cleaned in ("SCL",):
            return cls.SCL
        if cleaned in ("TXD", "TX", "TXD0", "TX0"):
            return cls.TX
        if cleaned in ("RXD", "RX", "RXD0", "RX0"):
            return cls.RX
        if cleaned in ("RTS",):
            return cls.RTS
        if cleaned in ("CTS",):
            return cls.CTS
        if cleaned in ("RST", "RESET", "~RESET", "RST_N", "RESET_N"):
            return cls.RESET
        if cleaned in ("EN", "ENABLE", "EN_N", "CHIP_EN", "CE"):
            return cls.EN
        if cleaned in ("BOOT", "GPIO0", "IO0"):
            return cls.BOOT
        if cleaned in ("XI", "OSC_IN", "XTAL_IN"):
            return cls.OSC_IN
        if cleaned in ("XO", "OSC_OUT", "XTAL_OUT"):
            return cls.OSC_OUT
        # Fallback to electrical type
        if etype:
            etype_lower = etype.lower()
            if etype_lower in ("power_in",):
                return cls.POWER_IN
            if etype_lower in ("power_out",):
                return cls.POWER_OUT
            if etype_lower in ("input",):
                return cls.INPUT
            if etype_lower in ("output",):
                return cls.OUTPUT
            if etype_lower in ("bidirectional",):
                return cls.BIDIRECTIONAL
            if etype_lower in ("passive",):
                return cls.PASSIVE
            if etype_lower in ("open_collector", "open_drain"):
                return cls.OPEN_COLLECTOR
            if etype_lower in ("open_emitter", "open_source"):
                return cls.OPEN_EMITTER
            if etype_lower in ("tri_state",):
                return cls.TRI_STATE
        return cls.UNUSED


class NetRole(enum.Enum):
    """Abstract net category — never hardcode net names."""
    POWER = "power"
    GROUND = "ground"
    SIGNAL = "signal"
    ANALOG = "analog"
    CLOCK = "clock"
    BUS = "bus"
    DIFFERENTIAL = "differential"
    HIGH_CURRENT = "high_current"
    COMMUNICATION = "communication"
    PASSIVE = "passive"

    @classmethod
    def from_net_name(cls, name: str) -> NetRole:
        cleaned = name.strip().upper() if name else ""
        if cleaned in ("GND", "VSS", "VEE", "AGND", "DGND", "PGND", "EPAD", "SHIELD", "0V"):
            return cls.GROUND
        if cleaned in ("VIN", "VCC", "VDD", "VBAT", "VSYS", "VUSB", "VBUS", "5V", "3V3",
                        "3.3V", "1V8", "1V2", "VOUT", "AVDD", "AVCC", "DVDD", "VREFP", "VREFN",
                        "+3.3V", "+5V", "5V0", "3V0"):
            return cls.POWER
        if cleaned.startswith("ADC") or cleaned.startswith("DAC") or "ANALOG" in cleaned:
            return cls.ANALOG
        if cleaned.startswith("CLK") or cleaned.endswith("CLK") or "CLOCK" in cleaned:
            return cls.CLOCK
        if any(bus in cleaned for bus in ("I2C", "SPI", "UART", "CAN", "USB", "SDA", "SCL")):
            return cls.COMMUNICATION
        if cleaned.endswith("_N") or cleaned.endswith("_P"):
            return cls.DIFFERENTIAL
        return cls.SIGNAL


class ConstraintType(enum.Enum):
    """Relationship between components — not a hardcoded connection."""
    SERIES = "series"
    PARALLEL = "parallel"
    POWERED_BY = "powered_by"
    GROUNDED_BY = "grounded_by"
    PULLED_UP = "pulled_up"
    PULLED_DOWN = "pulled_down"
    LOAD = "load"
    DRIVES = "drives"
    DECOUPLES = "decouples"


# ── Node types ─────────────────────────────────────────────────────────────


class PinNode:
    """A single physical pin with a resolved role."""
    __slots__ = ("key", "role", "name", "etype", "voltage_domain", "position")

    def __init__(
        self,
        key: str,
        role: PinRole,
        name: str = "",
        etype: str = "",
        voltage_domain: str | None = None,
        position: tuple[float, float] | None = None,
    ):
        self.key = key
        self.role = role
        self.name = name
        self.etype = etype
        self.voltage_domain = voltage_domain
        self.position = position


class ComponentNode:
    """A physical component with resolved pin roles."""
    __slots__ = ("ref_des", "id_str", "library", "category", "description",
                 "footprint", "pins", "metadata", "subsystem", "user_locked")

    def __init__(
        self,
        ref_des: str,
        id_str: str,
        library: str = "",
        category: str = "",
        description: str = "",
        footprint: str = "",
        subsystem: str = "",
        user_locked: bool = False,
    ):
        self.ref_des = ref_des
        self.id_str = id_str
        self.library = library
        self.category = category
        self.description = description
        self.footprint = footprint
        self.pins: dict[str, PinNode] = {}
        self.metadata: dict[str, Any] = {}
        self.subsystem = subsystem
        self.user_locked = user_locked


class NetNode:
    """A logical net with role classification."""
    __slots__ = ("name", "role", "pins")

    def __init__(self, name: str, role: NetRole):
        self.name = name
        self.role = role
        self.pins: set[str] = set()


class ConstraintEdge:
    """A topology relationship — not a hardcoded connection."""
    __slots__ = ("type", "source_pin", "target_pin", "via_role", "metadata")

    def __init__(
        self,
        type: ConstraintType,
        source_pin: str,
        target_pin: str | None = None,
        via_role: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        self.type = type
        self.source_pin = source_pin
        self.target_pin = target_pin
        self.via_role = via_role
        self.metadata = metadata or {}


# ── The Graph ──────────────────────────────────────────────────────────────


class SynthesisGraph:
    """Canonical circuit graph.

    Every synthesis stage reads from and writes to this graph.
    Raw LLM output is converted once at entry; downstream never mutates
    the original netlist directly.
    """

    def __init__(self):
        self.components: dict[str, ComponentNode] = {}
        self.nets: dict[str, NetNode] = {}
        self.constraints: list[ConstraintEdge] = []
        self.power_pins: list[dict] = []
        self.llm_nets: list[dict] | None = None

    # ── Construction helpers ──────────────────────────────────────────

    def add_component(self, comp_dict: dict) -> ComponentNode:
        ref = comp_dict.get("ref_des", "")
        existing = self.components.get(ref)
        if existing:
            return existing
        id_str = comp_dict.get("id_str", "")
        library = id_str.split(":")[0] if ":" in id_str else ""
        node = ComponentNode(
            ref_des=ref,
            id_str=id_str,
            library=library,
            category=comp_dict.get("category", ""),
            description=comp_dict.get("description", ""),
            footprint=comp_dict.get("footprint", ""),
            subsystem=comp_dict.get("subsystem", ""),
            user_locked=bool(comp_dict.get("user_locked")),
        )
        node.metadata = dict(comp_dict)
        self.components[ref] = node
        return node

    def add_pin(self, comp_ref: str, pin_key: str, pin_data: dict) -> PinNode:
        comp = self.components.get(comp_ref)
        if not comp:
            return None
        name = pin_data.get("name", "")
        etype = pin_data.get("etype", "")
        role = PinRole.from_pin_name(name, etype, len(comp.pins) + 1)
        pin = PinNode(
            key=pin_key,
            role=role,
            name=name,
            etype=etype,
            position=(pin_data.get("x", 0), pin_data.get("y", 0)),
        )
        comp.pins[pin_key] = pin
        return pin

    def get_or_create_net(self, name: str) -> NetNode:
        existing = self.nets.get(name)
        if existing:
            return existing
        role = NetRole.from_net_name(name) if name else NetRole.SIGNAL
        net = NetNode(name=name, role=role)
        self.nets[name] = net
        return net

    def add_constraint(self, constraint: ConstraintEdge):
        self.constraints.append(constraint)

    def import_llm_nets(self, netlist: list[dict]):
        """Convert raw LLM netlist entries into NetNodes."""
        self.llm_nets = netlist
        for conn in netlist:
            net_name = conn.get("net", "")
            if not net_name:
                continue
            net = self.get_or_create_net(net_name)
            src = conn.get("source", "")
            tgt = conn.get("target", "")
            if src:
                net.pins.add(src)
            if tgt:
                net.pins.add(tgt)

    def import_power_pins(self, power_pins: list[dict]):
        """Ingest power pin assignments as NetNodes."""
        self.power_pins = power_pins
        for pp in power_pins:
            net_name = pp.get("net", "")
            pin_key = pp.get("pin", "")
            if not net_name:
                continue
            net = self.get_or_create_net(net_name)
            if pin_key:
                net.pins.add(pin_key)

    # ── Query helpers ─────────────────────────────────────────────────

    def pin_role(self, pin_key: str) -> PinRole | None:
        for comp in self.components.values():
            pin = comp.pins.get(pin_key)
            if pin:
                return pin.role
        return None

    def component_by_pin(self, pin_key: str) -> ComponentNode | None:
        ref = pin_key.split(":")[0] if ":" in pin_key else ""
        return self.components.get(ref)

    def nets_by_role(self, role: NetRole) -> list[NetNode]:
        return [n for n in self.nets.values() if n.role == role]

    def constraints_for_pin(self, pin_key: str) -> list[ConstraintEdge]:
        return [c for c in self.constraints
                if c.source_pin == pin_key or c.target_pin == pin_key]

    @property
    def signal_nets(self) -> list[NetNode]:
        return self.nets_by_role(NetRole.SIGNAL)

    @property
    def power_nets(self) -> list[NetNode]:
        return self.nets_by_role(NetRole.POWER)

    @property
    def ground_nets(self) -> list[NetNode]:
        return self.nets_by_role(NetRole.GROUND)
