from __future__ import annotations

from agent.routing.constants import BBOX_CLEARANCE
from agent.routing.geometry import _seg_intersects_bbox


def _path_collisions(path: list[tuple[float, float]],
                     components: list[dict],
                     src_ref: str,
                     tgt_ref: str) -> int:
    if len(path) < 2:
        return 0

    def _point_in_comp_clearance(px: float, py: float, c: dict) -> bool:
        bbox = c.get('bbox') or c.get('geom_bbox')
        if not bbox:
            return False
        margin = BBOX_CLEARANCE
        left   = c['x'] + bbox['x'] - margin
        right  = left + bbox['w'] + 2 * margin
        top    = c['y'] + bbox['y'] - margin
        bottom = top + bbox['h'] + 2 * margin
        return left <= px <= right and top <= py <= bottom

    last_index = len(path) - 2
    hits = 0
    for i in range(len(path) - 1):
        p1, p2 = path[i], path[i + 1]
        if abs(p1[0] - p2[0]) < 1e-3 and abs(p1[1] - p2[1]) < 1e-3:
            continue
        for c in components:
            ref = c['ref_des']
            if ref == src_ref:
                # Only the very first segment may leave the source body.
                if i == 0 and _point_in_comp_clearance(*p1, c):
                    continue
            if ref == tgt_ref:
                # Only the very last segment may enter the target body.
                if i == last_index and _point_in_comp_clearance(*p2, c):
                    continue

            bbox = c.get('bbox') or c.get('geom_bbox')
            if not bbox:
                continue
            if _seg_intersects_bbox(p1, p2, bbox, c['x'], c['y'],
                                    margin=BBOX_CLEARANCE):
                hits += 1
    return hits
