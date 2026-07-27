"""Tests for connection_strategy — net classification logic."""

from agent.connection_strategy import (
    classify_strategy,
    WIRE, LABEL, GLOBAL,
    _is_bus_signal,
    _estimate_span,
)


def test_power_nets_are_global():
    assert classify_strategy("3V3", ["U1:1", "R1:1"], [], {}) == GLOBAL
    assert classify_strategy("GND", ["U1:2", "C1:1"], [], {}) == GLOBAL
    assert classify_strategy("5V", ["U1:3", "J1:1"], [], {}) == GLOBAL
    assert classify_strategy("VIN", ["U1:4"], [], {}) == GLOBAL
    assert classify_strategy("VCC", ["U1:5", "C2:1"], [], {}) == GLOBAL


def test_bus_signals_are_labels():
    assert classify_strategy("SDA", ["U1:21", "U2:5"], [], {}) == LABEL
    assert classify_strategy("SCL", ["U1:22", "U2:6"], [], {}) == LABEL
    assert classify_strategy("SPI_MOSI", ["U1:23", "U3:1"], [], {}) == LABEL
    assert classify_strategy("UART_TX", ["U1:24", "U4:2"], [], {}) == LABEL
    assert classify_strategy("USB_D+", ["U1:25", "J1:3"], [], {}) == LABEL
    assert classify_strategy("SWDIO", ["U1:26", "J2:1"], [], {}) == LABEL


def test_short_span_nets_are_wires():
    placements = {"U1": {"x": 0, "y": 0}, "R1": {"x": 10, "y": 5}}
    result = classify_strategy("GPIO1", ["U1:1", "R1:1"], [], placements)
    assert result == WIRE, f"expected WIRE, got {result}"


def test_long_span_nets_are_labels():
    placements = {"U1": {"x": 0, "y": 0}, "U2": {"x": 200, "y": 150}}
    result = classify_strategy("INT", ["U1:1", "U2:1"], [], placements)
    assert result == LABEL, f"expected LABEL, got {result}"


def test_is_bus_signal():
    assert _is_bus_signal("I2C_SDA")
    assert _is_bus_signal("SDA")
    assert _is_bus_signal("SPI_MOSI")
    assert _is_bus_signal("UART_TX")
    assert _is_bus_signal("USB_D+")
    assert not _is_bus_signal("GPIO1")
    assert not _is_bus_signal("LED_ANODE")
    assert not _is_bus_signal("RESET")


def test_estimate_span():
    placements = {"U1": {"x": 0, "y": 0}, "U2": {"x": 50, "y": 30}}
    span = _estimate_span(["U1:1", "U2:1"], placements)
    assert span == 80.0  # (50 - 0) + (30 - 0)
