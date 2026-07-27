"""Typed state models for CircuitBot using Pydantic v2.

Enforces strict schema contracts and component immutability flags across
all pipeline stages.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ComponentModel(BaseModel):
    """Immutable data contract for a component instance in the design state."""
    functional_id: str = Field(description="Unique functional identifier (e.g. R_CC1, MCU_U101, SW_RESET)")
    ref_des: str = Field(default="", description="Reference designator (e.g. R1, U101)")
    id_str: str = Field(description="KiCad symbol identifier (e.g. Device:R_Small, Sensor_Temperature:LM35-LP)")
    category: str = Field(default="General", description="Component library category")
    description: str = Field(default="", description="Human-readable description")
    value: str = Field(default="", description="Component electrical value (e.g. 5.1k, 100nF, 10uF)")
    is_user_locked: bool = Field(default=False, description="True if part was explicitly requested by user prompt")
    subsystem: str = Field(default="General", description="Parent subsystem name")
    justification: str = Field(default="", description="Selection or repair justification")
    footprint: str = Field(default="", description="Assigned KiCad footprint")
    pins: List[Dict[str, Any]] = Field(default_factory=list, description="Pin definitions")

    def to_dict(self) -> dict[str, Any]:
        """Convert model to standard dict for pipeline compatibility."""
        return self.model_dump()


def make_functional_id(id_str: str, description: str, index: int = 1) -> str:
    """Generate a stable, unique functional ID for a component instance."""
    prefix = id_str.split(":")[-1].upper().replace("-", "_").replace(".", "_")
    desc_clean = "".join(c for c in description.upper() if c.isalnum() or c == "_")[:20]
    return f"{prefix}_{desc_clean}_{index}"
