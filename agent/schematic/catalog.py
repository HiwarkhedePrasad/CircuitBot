"""Motif catalog — all detectable functional patterns.

Each motif is defined by a MotifSignature that describes:
  - What component class to look for (primary)
  - What pin roles the primary must have
  - What net roles its pins connect to
  - What secondary components (if any) are connected
  - How to score the match

Detection priority is by position in this list — earlier signatures
are checked first and have first claim on components.

Priority order (intentional):
  1. Power motifs (regulators, power entry) — highest
  2. Interface motifs (USB, crystal, programming)
  3. Active motifs (op-amp, MOSFET driver)
  4. Multi-component passives (RC filter, voltage divider)
  5. Single-component passives (pull-up, decoupling cap)
"""

from agent.schematic.schematic_types import (
    MotifCategory,
    MotifSignature,
    MotifType,
    PinNetConstraint,
    SecondarySpec,
)


# ── 1. POWER MOTIFS ─────────────────────────────────────────────────────────

LDO_REGULATOR = MotifSignature(
    name="ldo_regulator",
    motif_type=MotifType.LDO_REGULATOR,
    category=MotifCategory.POWER,
    priority=100,
    primary_meta={"component_class": {"linear_regulator"}},
    primary_pin_roles={"vin", "vout", "ground"},
    pin_net_constraints=[
        PinNetConstraint(pin_role="vin", net_role="power"),
        PinNetConstraint(pin_role="ground", net_role="ground"),
    ],
    anchor="primary",
    base_score=80.0,
    template_name="ldo_regulator",
)

BUCK_CONVERTER = MotifSignature(
    name="buck_converter",
    motif_type=MotifType.BUCK_CONVERTER,
    category=MotifCategory.POWER,
    priority=95,
    primary_meta={"component_class": {"switching_regulator"}},
    primary_pin_roles={"vin", "vout", "ground"},
    secondaries=[
        SecondarySpec(
            meta={"passive_class": {"inductor"}},
            connected_pin_role="vout",
            required=False,
            label="output_inductor",
        ),
    ],
    anchor="primary",
    base_score=85.0,
    template_name="buck_converter",
)

POWER_ENTRY = MotifSignature(
    name="power_entry",
    motif_type=MotifType.POWER_ENTRY,
    category=MotifCategory.POWER,
    priority=90,
    primary_meta={"component_class": {"connector"}},
    primary_pin_roles=set(),
    pin_net_constraints=[
        PinNetConstraint(pin_role="", net_role="power", required=False),
    ],
    secondaries=[
        SecondarySpec(
            meta={"passive_class": {"fuse"}},
            required=False,
            label="fuse",
        ),
        SecondarySpec(
            meta={"passive_class": {"diode"}},
            required=False,
            label="tvs",
        ),
        SecondarySpec(
            meta={"passive_class": {"capacitor"}},
            required=False,
            label="bulk_cap",
        ),
    ],
    anchor="primary",
    base_score=60.0,
    template_name="power_entry",
)

BATTERY_CHARGER = MotifSignature(
    name="battery_charger",
    motif_type=MotifType.BATTERY_CHARGER,
    category=MotifCategory.POWER,
    priority=85,
    primary_meta={"component_class": {"interface_ic"}},
    primary_pin_roles={"vin", "vout"},
    pin_net_constraints=[
        PinNetConstraint(pin_role="vin", net_role="power"),
    ],
    secondaries=[
        SecondarySpec(
            meta={"passive_class": {"resistor"}},
            connected_pin_role="vout",
            required=False,
            label="sense_resistor",
        ),
    ],
    anchor="primary",
    base_score=70.0,
    template_name="battery_charger",
)


# ── 2. INTERFACE MOTIFS ─────────────────────────────────────────────────────

USB_INTERFACE = MotifSignature(
    name="usb_interface",
    motif_type=MotifType.USB_INTERFACE,
    category=MotifCategory.INTERFACE,
    priority=80,
    primary_meta={"component_class": {"connector"}},
    primary_pin_roles=set(),
    secondaries=[
        SecondarySpec(
            meta={"passive_class": {"diode"}},
            required=False,
            label="esd_protection",
        ),
    ],
    anchor="primary",
    base_score=65.0,
    template_name="usb_interface",
)

CRYSTAL = MotifSignature(
    name="crystal",
    motif_type=MotifType.CRYSTAL,
    category=MotifCategory.INTERFACE,
    priority=75,
    primary_meta={"component_class": {"crystal"}},
    primary_pin_roles=set(),
    secondaries=[
        SecondarySpec(
            meta={"passive_class": {"capacitor"}},
            required=False,
            label="load_cap_1",
        ),
        SecondarySpec(
            meta={"passive_class": {"capacitor"}},
            required=False,
            label="load_cap_2",
        ),
    ],
    anchor="primary",
    base_score=70.0,
    template_name="crystal",
)

PROGRAMMING_HEADER = MotifSignature(
    name="programming_header",
    motif_type=MotifType.PROGRAMMING_HEADER,
    category=MotifCategory.INTERFACE,
    priority=70,
    primary_meta={"component_class": {"connector"}},
    primary_pin_roles=set(),
    anchor="primary",
    base_score=55.0,
    template_name="programming_header",
)

RESET_CIRCUIT = MotifSignature(
    name="reset_circuit",
    motif_type=MotifType.RESET_CIRCUIT,
    category=MotifCategory.INTERFACE,
    priority=65,
    primary_meta={"passive_class": {"resistor"}},
    primary_pin_roles=set(),
    pin_net_constraints=[
        PinNetConstraint(pin_role="", net_role="power"),
    ],
    secondaries=[
        SecondarySpec(
            meta={"component_class": {"connector"}},
            required=False,
            label="reset_switch",
        ),
    ],
    anchor="primary",
    base_score=40.0,
    template_name="reset_circuit",
)

I2C_BUS = MotifSignature(
    name="i2c_bus",
    motif_type=MotifType.I2C_BUS,
    category=MotifCategory.INTERFACE,
    priority=60,
    primary_meta={"passive_class": {"resistor"}},
    primary_pin_roles=set(),
    pin_net_constraints=[
        PinNetConstraint(pin_role="", net_role="communication"),
    ],
    secondaries=[
        SecondarySpec(
            meta={"passive_class": {"resistor"}},
            required=False,
            label="second_pull_up",
        ),
    ],
    anchor="primary",
    base_score=35.0,
    template_name="i2c_bus",
)


# ── 3. ACTIVE MOTIFS ────────────────────────────────────────────────────────

OPAMP = MotifSignature(
    name="opamp",
    motif_type=MotifType.OPAMP,
    category=MotifCategory.ACTIVE,
    priority=55,
    primary_meta={"component_class": {"amplifier", "comparator"}},
    primary_pin_roles={"input", "output"},
    secondaries=[
        SecondarySpec(
            meta={"passive_class": {"resistor"}},
            required=False,
            label="feedback_resistor",
        ),
    ],
    anchor="primary",
    base_score=60.0,
    template_name="opamp",
)

MOSFET_DRIVER = MotifSignature(
    name="mosfet_driver",
    motif_type=MotifType.MOSFET_DRIVER,
    category=MotifCategory.ACTIVE,
    priority=50,
    primary_meta={"component_class": {"transistor"}},
    primary_pin_roles={"gate", "drain", "source"},
    secondaries=[
        SecondarySpec(
            meta={"passive_class": {"resistor"}},
            connected_pin_role="gate",
            required=False,
            label="gate_resistor",
        ),
        SecondarySpec(
            meta={"passive_class": {"diode"}},
            connected_pin_role="drain",
            required=False,
            label="flyback_diode",
        ),
    ],
    anchor="primary",
    base_score=55.0,
    template_name="mosfet_driver",
)


# ── 4. MULTI-COMPONENT PASSIVE MOTIFS ───────────────────────────────────────

RC_FILTER = MotifSignature(
    name="rc_filter",
    motif_type=MotifType.RC_FILTER,
    category=MotifCategory.PASSIVE,
    priority=45,
    primary_meta={"passive_class": {"resistor"}},
    primary_pin_roles=set(),
    secondaries=[
        SecondarySpec(
            meta={"passive_class": {"capacitor"}},
            connected_pin_role="",
            required=True,
            label="filter_capacitor",
        ),
    ],
    anchor="primary",
    base_score=45.0,
    template_name="rc_filter",
)

PI_FILTER = MotifSignature(
    name="pi_filter",
    motif_type=MotifType.PI_FILTER,
    category=MotifCategory.PASSIVE,
    priority=40,
    primary_meta={"passive_class": {"inductor", "resistor"}},
    primary_pin_roles=set(),
    secondaries=[
        SecondarySpec(
            meta={"passive_class": {"capacitor"}},
            required=True,
            label="input_cap",
        ),
        SecondarySpec(
            meta={"passive_class": {"capacitor"}},
            required=True,
            label="output_cap",
        ),
    ],
    anchor="primary",
    base_score=50.0,
    template_name="pi_filter",
)

VOLTAGE_DIVIDER = MotifSignature(
    name="voltage_divider",
    motif_type=MotifType.VOLTAGE_DIVIDER,
    category=MotifCategory.PASSIVE,
    priority=35,
    primary_meta={"passive_class": {"resistor"}},
    primary_pin_roles=set(),
    pin_net_constraints=[
        PinNetConstraint(pin_role="", net_role="power"),
    ],
    secondaries=[
        SecondarySpec(
            meta={"passive_class": {"resistor"}},
            required=True,
            label="second_resistor",
        ),
    ],
    anchor="primary",
    base_score=40.0,
    template_name="voltage_divider",
)

LED_INDICATOR = MotifSignature(
    name="led_indicator",
    motif_type=MotifType.LED_INDICATOR,
    category=MotifCategory.PASSIVE,
    priority=30,
    primary_meta={"passive_class": {"led"}},
    primary_pin_roles={"anode", "cathode"},
    secondaries=[
        SecondarySpec(
            meta={"passive_class": {"resistor"}},
            connected_pin_role="anode",
            required=True,
            label="current_limit_resistor",
        ),
    ],
    anchor="secondary:current_limit_resistor",
    base_score=45.0,
    template_name="led_indicator",
)


# ── 5. SINGLE-COMPONENT PASSIVE MOTIFS ──────────────────────────────────────

PULL_UP = MotifSignature(
    name="pull_up",
    motif_type=MotifType.PULL_UP,
    category=MotifCategory.PASSIVE,
    priority=25,
    primary_meta={"passive_class": {"resistor"}},
    primary_pin_roles=set(),
    pin_net_constraints=[
        PinNetConstraint(pin_role="", net_role="signal"),
        PinNetConstraint(pin_role="", net_role="power"),
    ],
    anchor="primary",
    base_score=25.0,
    template_name="pull_up",
)

PULL_DOWN = MotifSignature(
    name="pull_down",
    motif_type=MotifType.PULL_DOWN,
    category=MotifCategory.PASSIVE,
    priority=24,
    primary_meta={"passive_class": {"resistor"}},
    primary_pin_roles=set(),
    pin_net_constraints=[
        PinNetConstraint(pin_role="", net_role="signal"),
        PinNetConstraint(pin_role="", net_role="ground"),
    ],
    anchor="primary",
    base_score=25.0,
    template_name="pull_down",
)

DECOUPLING_CAP = MotifSignature(
    name="decoupling_cap",
    motif_type=MotifType.DECOUPLING_CAP,
    category=MotifCategory.PASSIVE,
    priority=20,
    primary_meta={"passive_class": {"capacitor"}},
    primary_pin_roles=set(),
    pin_net_constraints=[
        PinNetConstraint(pin_role="", net_role="power"),
        PinNetConstraint(pin_role="", net_role="ground"),
    ],
    anchor="primary",
    base_score=20.0,
    template_name="decoupling_cap",
)


# ── Master catalog ──────────────────────────────────────────────────────────

# Detection order: earlier = higher priority (first claim on components).
# Power and interface motifs are listed first because they have the most
# specific signatures and should claim components before generic passives.
MOTIF_CATALOG: list[MotifSignature] = [
    # Power (priority 100–85)
    LDO_REGULATOR,
    BUCK_CONVERTER,
    POWER_ENTRY,
    BATTERY_CHARGER,
    # Interface (priority 80–60)
    USB_INTERFACE,
    CRYSTAL,
    PROGRAMMING_HEADER,
    RESET_CIRCUIT,
    I2C_BUS,
    # Active (priority 55–50)
    OPAMP,
    MOSFET_DRIVER,
    # Multi-component passives (priority 45–30)
    RC_FILTER,
    PI_FILTER,
    VOLTAGE_DIVIDER,
    LED_INDICATOR,
    # Single-component passives (priority 25–20)
    PULL_UP,
    PULL_DOWN,
    DECOUPLING_CAP,
]
