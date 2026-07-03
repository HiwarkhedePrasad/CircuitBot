"""Routing package — obstacle-aware orthogonal schematic wire routing.

Usage::

    from agent.routing import route_traces, count_crossings

    traces, dropped = route_traces(components, netlist, pin_matrix)
    crossings = count_crossings(traces)
"""

from __future__ import annotations

from agent.routing.constants import (
    GRID_SIZE, PIN_STUB_LEN, MAX_WIRE_MANHATTAN, MAX_COLLISIONS,
    BBOX_CLEARANCE, MAX_WIRE_PT2PT, MATRIX_SIZE, MATRIX_OFFSET,
)
from agent.routing.geometry import _snap, _pin_direction, _stub_point, _seg_intersects_bbox
from agent.routing.collision import _path_collisions
from agent.routing.path_utils import _path_length, _bend_count, _clean_path, _is_orthogonal
from agent.routing.candidates import (
    _candidate_straight, _candidate_L, _candidate_Z, _candidate_U,
)
from agent.routing.astar import _astar_orthogonal
from agent.routing.make_path import make_path
from agent.routing.api import (
    route_traces, count_crossings, log_placement_metrics,
    repair_placement_for_routing,
)

# Import placement mode for routing decisions
from agent.placement import PLACEMENT_ENGINE as PLACEMENT_MODE
