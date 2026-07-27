"""
tscircuit_data — CircuitJSON component data for CircuitBot.

Provides access to the tscircuit component registry (symbols, footprints,
pin definitions, metadata) without the rendering engine.

Usage:
    from tscircuit_data import TscircuitClient
    client = TscircuitClient()
    results = client.search("ESP32")
"""

from .client import TscircuitClient
from .schema import Component, Footprint, Symbol, Pin, Net

__all__ = ["TscircuitClient", "Component", "Footprint", "Symbol", "Pin", "Net"]
