"""Simulation Broker — routes simulation requests to the right simulator.

Simulators are registered by type. The broker dispatches to:
1. Analytical models (instant, no external tool): impedance, current capacity, voltage drop, power dissipation
2. SPICE (requires ngspice or PySpice): transient analysis, AC analysis
3. Thermal (simplified model): junction temperature from power dissipation

Each simulator produces a SimulationResult with the same interface.
Thread-safe: all operations are stateless.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable
from uuid import uuid4


class SimulationType(Enum):
    IMPEDANCE = "impedance"
    CURRENT_CAPACITY = "current_capacity"
    VOLTAGE_DROP = "voltage_drop"
    POWER_DISSIPATION = "power_dissipation"
    JUNCTION_TEMPERATURE = "junction_temperature"
    DC_OPERATING_POINT = "dc_operating_point"
    TRACE_LENGTH = "trace_length"


class SimulationStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


@dataclass
class SimulationSpec:
    """Specification for a simulation request."""
    simulation_type: SimulationType = SimulationType.IMPEDANCE
    target_id: str = ""  # component ref, net name, trace ID
    parameters: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "simulation_type": self.simulation_type.value,
            "target_id": self.target_id,
            "parameters": self.parameters,
        }


@dataclass
class SimulationResult:
    """Result from a simulation."""
    id: str = field(default_factory=lambda: uuid4().hex[:8])
    spec: SimulationSpec = field(default_factory=SimulationSpec)
    status: SimulationStatus = SimulationStatus.PENDING
    value: float = 0.0
    unit: str = ""
    confidence: float = 0.0
    model: str = ""  # which simulator produced this
    details: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "simulation_type": self.spec.simulation_type.value,
            "target_id": self.spec.target_id,
            "status": self.status.value,
            "value": round(self.value, 4) if self.value else 0,
            "unit": self.unit,
            "confidence": round(self.confidence, 3),
            "model": self.model,
            "details": self.details,
            "warnings": self.warnings,
        }


# ── Analytical Simulators ───────────────────────────────────────────────

def simulate_impedance(spec: SimulationSpec, context: dict) -> SimulationResult:
    """Calculate trace impedance using IPC-2221 microstrip formula."""
    result = SimulationResult(spec=spec, model="ipc2221_microstrip")

    width = spec.parameters.get("width", 0.254)  # mm
    thickness = spec.parameters.get("thickness", 0.035)  # mm (1oz copper)
    dielectric_height = spec.parameters.get("dielectric_height", 1.5)  # mm
    dielectric_constant = spec.parameters.get("dielectric_constant", 4.5)

    try:
        from agent.tools import calculate_microstrip_impedance
        z0_raw = calculate_microstrip_impedance(width, thickness, dielectric_height, dielectric_constant)
        # Handle dict return (existing tools return dicts)
        if isinstance(z0_raw, dict):
            z0 = z0_raw.get("z0_ohm", 0)
            result.details = dict(z0_raw)
        else:
            z0 = float(z0_raw)
        result.value = z0
        result.unit = "ohms"
        result.confidence = 0.85
        result.status = SimulationStatus.COMPLETED
        result.details.setdefault("width_mm", width)
        result.details.setdefault("thickness_mm", thickness)
        result.details.setdefault("dielectric_height_mm", dielectric_height)
        result.details.setdefault("er", dielectric_constant)

        # Check against target
        target = spec.parameters.get("target_impedance")
        if target and z0 > 0:
            diff_pct = abs(z0 - target) / target * 100
            if diff_pct > 15:
                result.warnings.append(f"Impedance {z0:.1f}ohm differs from target {target}ohm by {diff_pct:.0f}%")
    except Exception as e:
        result.status = SimulationStatus.FAILED
        result.warnings.append(str(e))

    return result


def simulate_current_capacity(spec: SimulationSpec, context: dict) -> SimulationResult:
    """Calculate max current capacity using IPC-2221 trace width formula."""
    result = SimulationResult(spec=spec, model="ipc2221_current")

    width = spec.parameters.get("width", 0.254)  # mm
    thickness = spec.parameters.get("thickness", 0.035)  # mm
    temp_rise = spec.parameters.get("temp_rise", 10)  # °C

    try:
        from agent.tools import calculate_max_current
        i_max = calculate_max_current(width, thickness, temp_rise)
        result.value = i_max
        result.unit = "amps"
        result.confidence = 0.8
        result.status = SimulationStatus.COMPLETED
        result.details = {
            "width_mm": width, "thickness_mm": thickness, "temp_rise_c": temp_rise,
        }

        # Check against actual current
        actual_current = spec.parameters.get("actual_current")
        if actual_current and actual_current > i_max:
            result.warnings.append(f"Current {actual_current*1000:.1f}mA exceeds capacity {i_max*1000:.1f}mA")
    except Exception as e:
        result.status = SimulationStatus.FAILED
        result.warnings.append(str(e))

    return result


def simulate_voltage_drop(spec: SimulationSpec, context: dict) -> SimulationResult:
    """Calculate DC voltage drop across a trace."""
    result = SimulationResult(spec=spec, model="dc_voltage_drop")

    current = spec.parameters.get("current", 0.1)  # amps
    length = spec.parameters.get("length", 0.05)  # meters
    width = spec.parameters.get("width", 0.254)  # mm
    thickness = spec.parameters.get("thickness", 0.035)  # mm

    try:
        from agent.tools import calculate_voltage_drop
        vdrop = calculate_voltage_drop(current, length, width, thickness)
        result.value = vdrop
        result.unit = "volts"
        result.confidence = 0.85
        result.status = SimulationStatus.COMPLETED
        result.details = {
            "current_a": current, "length_m": length,
            "width_mm": width, "thickness_mm": thickness,
        }

        # Warn if voltage drop is significant
        if vdrop > 0.1:
            result.warnings.append(f"Voltage drop {vdrop*1000:.1f}mV may affect circuit performance")
    except Exception as e:
        result.status = SimulationStatus.FAILED
        result.warnings.append(str(e))

    return result


def simulate_power_dissipation(spec: SimulationSpec, context: dict) -> SimulationResult:
    """Calculate power dissipation for a component."""
    result = SimulationResult(spec=spec, model="power_calculation")

    voltage = spec.parameters.get("voltage", 0)
    current = spec.parameters.get("current", 0)
    resistance = spec.parameters.get("resistance")

    if resistance and current:
        power = current * current * resistance
    elif voltage and current:
        power = voltage * current
    else:
        power = 0

    result.value = power
    result.unit = "watts"
    result.confidence = 0.9
    result.status = SimulationStatus.COMPLETED
    result.details = {"voltage": voltage, "current": current, "resistance": resistance}

    # Warn if power is high
    if power > 0.5:
        result.warnings.append(f"Power dissipation {power*1000:.1f}mW may require thermal management")

    return result


def simulate_junction_temperature(spec: SimulationSpec, context: dict) -> SimulationResult:
    """Calculate junction temperature from power dissipation and thermal resistance."""
    result = SimulationResult(spec=spec, model="thermal_model")

    power = spec.parameters.get("power", 0)  # watts
    rth_ja = spec.parameters.get("rth_ja", 50)  # °C/W (junction to ambient)
    ambient_temp = spec.parameters.get("ambient_temp", 25)  # °C

    tj = ambient_temp + power * rth_ja

    result.value = tj
    result.unit = "celsius"
    result.confidence = 0.7  # simplified model
    result.status = SimulationStatus.COMPLETED
    result.details = {
        "power_w": power, "rth_ja": rth_ja, "ambient_c": ambient_temp,
    }

    if tj > 85:
        result.warnings.append(f"Junction temperature {tj:.0f}°C exceeds 85°C recommended limit")
    if tj > 125:
        result.warnings.append(f"Junction temperature {tj:.0f}°C exceeds absolute maximum 125°C")

    return result


def simulate_trace_length(spec: SimulationSpec, context: dict) -> SimulationResult:
    """Calculate trace length from path points."""
    result = SimulationResult(spec=spec, model="geometry")

    path = spec.parameters.get("path", [])
    if len(path) < 2:
        result.value = 0
        result.unit = "mm"
        result.status = SimulationStatus.COMPLETED
        return result

    length = 0.0
    for i in range(1, len(path)):
        dx = path[i][0] - path[i-1][0]
        dy = path[i][1] - path[i-1][1]
        length += (dx*dx + dy*dy) ** 0.5

    result.value = length
    result.unit = "mm"
    result.confidence = 1.0
    result.status = SimulationStatus.COMPLETED
    result.details = {"segments": len(path) - 1}

    return result


# ── Simulator Registry ──────────────────────────────────────────────────

SIMULATORS: dict[SimulationType, Callable] = {
    SimulationType.IMPEDANCE: simulate_impedance,
    SimulationType.CURRENT_CAPACITY: simulate_current_capacity,
    SimulationType.VOLTAGE_DROP: simulate_voltage_drop,
    SimulationType.POWER_DISSIPATION: simulate_power_dissipation,
    SimulationType.JUNCTION_TEMPERATURE: simulate_junction_temperature,
    SimulationType.TRACE_LENGTH: simulate_trace_length,
}


class SimulationBroker:
    """Routes simulation requests to the appropriate simulator.

    Thread-safe: simulators are stateless functions.
    """

    def __init__(self, projections: dict | None = None):
        self._projections = projections or {}
        self._custom_simulators: dict[SimulationType, Callable] = {}
        self._lock = threading.Lock()

    def set_projections(self, projections: dict) -> None:
        """Update projection data."""
        self._projections = projections

    def register_simulator(self, sim_type: SimulationType, simulator: Callable) -> None:
        """Register a custom simulator."""
        with self._lock:
            self._custom_simulators[sim_type] = simulator

    def simulate(self, spec: SimulationSpec) -> SimulationResult:
        """Run a simulation and return the result.

        Checks custom simulators first, then built-in ones.
        """
        # Check custom simulators
        with self._lock:
            if spec.simulation_type in self._custom_simulators:
                return self._custom_simulators[spec.simulation_type](spec, self._projections)

        # Check built-in simulators
        simulator = SIMULATORS.get(spec.simulation_type)
        if simulator:
            return simulator(spec, self._projections)

        return SimulationResult(
            spec=spec, status=SimulationStatus.UNSUPPORTED,
            warnings=[f"Simulation type {spec.simulation_type.value} not supported"],
        )

    def simulate_trace(self, net_name: str, context: dict | None = None) -> dict:
        """Run all relevant simulations for a trace."""
        ctx = context or self._projections
        results = {}

        # Impedance
        spec = SimulationSpec(
            simulation_type=SimulationType.IMPEDANCE,
            target_id=net_name,
            parameters={"width": ctx.get("width", 0.254)},
        )
        results["impedance"] = self.simulate(spec)

        # Current capacity
        spec = SimulationSpec(
            simulation_type=SimulationType.CURRENT_CAPACITY,
            target_id=net_name,
            parameters={"width": ctx.get("width", 0.254)},
        )
        results["current_capacity"] = self.simulate(spec)

        # Voltage drop
        spec = SimulationSpec(
            simulation_type=SimulationType.VOLTAGE_DROP,
            target_id=net_name,
            parameters={
                "current": ctx.get("current", 0.1),
                "length": ctx.get("length", 0.05),
                "width": ctx.get("width", 0.254),
            },
        )
        results["voltage_drop"] = self.simulate(spec)

        return results

    def simulate_component(self, ref_des: str, context: dict | None = None) -> dict:
        """Run all relevant simulations for a component."""
        ctx = context or self._projections
        results = {}

        # Power dissipation
        spec = SimulationSpec(
            simulation_type=SimulationType.POWER_DISSIPATION,
            target_id=ref_des,
            parameters={
                "voltage": ctx.get("voltage", 0),
                "current": ctx.get("current", 0),
            },
        )
        results["power"] = self.simulate(spec)

        # Junction temperature
        power_result = results["power"]
        if power_result.status == SimulationStatus.COMPLETED:
            spec = SimulationSpec(
                simulation_type=SimulationType.JUNCTION_TEMPERATURE,
                target_id=ref_des,
                parameters={
                    "power": power_result.value,
                    "ambient_temp": ctx.get("ambient_temp", 25),
                },
            )
            results["temperature"] = self.simulate(spec)

        return results

    def available_simulations(self) -> list[str]:
        """List available simulation types."""
        all_types = set(SIMULATORS.keys())
        with self._lock:
            all_types.update(self._custom_simulators.keys())
        return sorted(t.value for t in all_types)
