from __future__ import annotations

import heapq

from agent.routing.constants import GRID_SIZE, BBOX_CLEARANCE
from agent.routing.path_utils import _clean_path
from agent.routing.geometry import _snap
from agent.routing.geometry import _rotated_bbox


def _astar_orthogonal(
    start: tuple[float, float],
    goal: tuple[float, float],
    components: list[dict],
    src_ref: str,
    tgt_ref: str,
    max_length: float,
    blocked_vertices: set[tuple[float, float]],
) -> list[tuple[float, float]] | None:
    margin = 200.0
    min_x = min(start[0], goal[0]) - margin
    max_x = max(start[0], goal[0]) + margin
    min_y = min(start[1], goal[1]) - margin
    max_y = max(start[1], goal[1]) + margin

    gs = GRID_SIZE
    cols = max(3, int(round((max_x - min_x) / gs)))
    rows = max(3, int(round((max_y - min_y) / gs)))
    max_x = min_x + cols * gs
    max_y = min_y + rows * gs

    def _to_grid(wx: float, wy: float) -> tuple[int, int]:
        return (int(round((wx - min_x) / gs)),
                int(round((wy - min_y) / gs)))

    def _to_world(gx: int, gy: int) -> tuple[float, float]:
        return (_snap(min_x + gx * gs), _snap(min_y + gy * gs))

    blocked_cells: set[tuple[int, int]] = set()
    for c in components:
        ref = c['ref_des']
        if ref in (src_ref, tgt_ref):
            continue
        bbox = _rotated_bbox(c)
        if not bbox:
            continue
        left   = c['x'] + bbox['x'] - BBOX_CLEARANCE
        right  = left + bbox['w'] + 2 * BBOX_CLEARANCE
        top    = c['y'] + bbox['y'] - BBOX_CLEARANCE
        bottom = top + bbox['h'] + 2 * BBOX_CLEARANCE
        gx1, gy1 = _to_grid(left, top)
        gx2, gy2 = _to_grid(right, bottom)
        for gx in range(max(0, gx1), min(cols, gx2 + 1)):
            for gy in range(max(0, gy1), min(rows, gy2 + 1)):
                blocked_cells.add((gx, gy))

    gs_pos = _to_grid(*start)
    gg_pos = _to_grid(*goal)

    if gs_pos == gg_pos:
        return []

    if not (0 <= gs_pos[0] < cols and 0 <= gs_pos[1] < rows and
            0 <= gg_pos[0] < cols and 0 <= gg_pos[1] < rows):
        return None

    blocked_cells.discard(gs_pos)
    blocked_cells.discard(gg_pos)

    for origin in (gs_pos, gg_pos):
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = origin[0] + dx, origin[1] + dy
            if 0 <= nx < cols and 0 <= ny < rows:
                blocked_cells.discard((nx, ny))

    for vx, vy in blocked_vertices:
        gx, gy = _to_grid(vx, vy)
        if 0 <= gx < cols and 0 <= gy < rows:
            blocked_cells.add((gx, gy))

    max_steps = int(max_length / gs) * 4

    def _heuristic(gx, gy):
        return abs(gx - gg_pos[0]) + abs(gy - gg_pos[1])

    open_set = [(0, gs_pos)]
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    g_score: dict[tuple[int, int], float] = {gs_pos: 0}
    f_score: dict[tuple[int, int], float] = {gs_pos: _heuristic(*gs_pos)}

    steps = 0
    while open_set:
        steps += 1
        if steps > max_steps:
            return None

        _, current = heapq.heappop(open_set)

        if current == gg_pos:
            path_grid = []
            while current in came_from:
                path_grid.append(current)
                current = came_from[current]
            path_grid.append(gs_pos)
            path_grid.reverse()
            waypoints = [_to_world(gx, gy) for gx, gy in path_grid]
            return _clean_path(waypoints)

        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = current[0] + dx, current[1] + dy
            if not (0 <= nx < cols and 0 <= ny < rows):
                continue
            if (nx, ny) in blocked_cells:
                continue
            neighbor = (nx, ny)
            tentative = g_score[current] + 1
            if tentative < g_score.get(neighbor, float('inf')):
                came_from[neighbor] = current
                g_score[neighbor] = tentative
                f = tentative + _heuristic(nx, ny)
                f_score[neighbor] = f
                heapq.heappush(open_set, (f, neighbor))

    return None
