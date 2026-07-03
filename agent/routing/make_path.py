from __future__ import annotations

from agent.routing.constants import MAX_WIRE_MANHATTAN, MAX_COLLISIONS, GRID_SIZE
from agent.routing.geometry import _stub_point, _snap
from agent.routing.candidates import _candidate_straight, _candidate_L, _candidate_Z, _candidate_U
from agent.routing.astar import _astar_orthogonal
from agent.routing.path_utils import _clean_path, _path_length, _bend_count
from agent.routing.collision import _path_collisions


def make_path(s_pos, s_dir, t_pos, t_dir, components, src_ref, tgt_ref,
              blocked_vertices: set[tuple[float, float]] | None = None):
    s_stub = _stub_point(*s_pos, s_dir)
    t_stub = _stub_point(*t_pos, t_dir)
    blocked = blocked_vertices or set()

    candidates = []
    candidates += _candidate_straight(s_pos, s_stub, t_pos, t_stub)
    candidates += _candidate_L(s_pos, s_stub, t_pos, t_stub)
    candidates += _candidate_Z(s_pos, s_stub, t_pos, t_stub, components)
    candidates += _candidate_U(s_pos, s_stub, t_pos, t_stub, components)

    best_path = None
    best_score = float('inf')
    for raw in candidates:
        path = _clean_path(raw)
        if len(path) < 2:
            continue
        length = _path_length(path)
        if length > MAX_WIRE_MANHATTAN:
            continue
        collisions = _path_collisions(path, components, src_ref, tgt_ref)
        if collisions > MAX_COLLISIONS:
            continue
        bends = _bend_count(path)
        vertex_overlap = False
        for v in path[1:-1]:
            if v in blocked:
                vertex_overlap = True
                break
        if vertex_overlap:
            continue
        score = collisions * 10000 + length + bends * 2
        if score < best_score:
            best_score = score
            best_path = path

    if best_path is None:
        relaxed_collisions = MAX_COLLISIONS
        for raw in candidates:
            path = _clean_path(raw)
            if len(path) < 2:
                continue
            length = _path_length(path)
            if length > MAX_WIRE_MANHATTAN * 1.5:
                continue
            collisions = _path_collisions(path, components, src_ref, tgt_ref)
            if collisions > relaxed_collisions:
                continue
            bends = _bend_count(path)
            vertex_overlap = False
            for v in path[1:-1]:
                if v in blocked:
                    vertex_overlap = True
                    break
            if vertex_overlap:
                continue
            score = collisions * 10000 + length + bends * 2
            if score < best_score:
                best_score = score
                best_path = path

    if best_path is None:
        offsets = [(GRID_SIZE, 0), (-GRID_SIZE, 0), (0, GRID_SIZE), (0, -GRID_SIZE)]
        for raw in candidates:
            path = _clean_path(raw)
            if len(path) < 2:
                continue
            base_length = _path_length(path)
            if base_length > MAX_WIRE_MANHATTAN * 1.5:
                continue
            fully_repaired = True
            for i in range(1, len(path) - 1):
                if path[i] in blocked:
                    repaired = False
                    for ox, oy in offsets:
                        shifted = _snap(path[i][0] + ox), _snap(path[i][1] + oy)
                        if shifted not in blocked:
                            path[i] = shifted
                            repaired = True
                            break
                    if not repaired:
                        fully_repaired = False
                        break
            if not fully_repaired:
                continue
            new_path = _clean_path(path)
            if len(new_path) < 2:
                continue
            length = _path_length(new_path)
            if length > MAX_WIRE_MANHATTAN * 1.5:
                continue
            collisions = _path_collisions(new_path, components, src_ref, tgt_ref)
            if collisions > relaxed_collisions:
                continue
            bends = _bend_count(new_path)
            score = collisions * 10000 + length + bends * 2
            if score < best_score:
                best_score = score
                best_path = new_path

    if best_path is None:
        astar_path = _astar_orthogonal(
            s_stub, t_stub, components, src_ref, tgt_ref,
            MAX_WIRE_MANHATTAN * 1.5, blocked,
        )
        if astar_path:
            path = [s_pos] + astar_path + [t_pos]
            path = _clean_path(path)
            if len(path) >= 2:
                length = _path_length(path)
                if length <= MAX_WIRE_MANHATTAN * 1.5:
                    collisions = _path_collisions(path, components, src_ref, tgt_ref)
                    if collisions <= MAX_COLLISIONS:
                        best_path = path

    if best_path is None:
        relaxed_collisions = max(MAX_COLLISIONS + 1, 1)
        for raw in candidates:
            path = _clean_path(raw)
            if len(path) < 2:
                continue
            length = _path_length(path)
            if length > MAX_WIRE_MANHATTAN * 1.5:
                continue
            collisions = _path_collisions(path, components, src_ref, tgt_ref)
            if collisions > relaxed_collisions:
                continue
            bends = _bend_count(path)
            score = collisions * 10000 + length + bends * 2
            if score < best_score:
                best_score = score
                best_path = path

    return best_path
