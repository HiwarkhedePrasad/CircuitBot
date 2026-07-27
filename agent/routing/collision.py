from __future__ import annotations

from agent.routing.constants import BBOX_CLEARANCE
from agent.routing.geometry import _seg_intersects_bbox, _rotated_bbox


def _path_collisions(path: list[tuple[float, float]],
                     components: list[dict],
                     src_ref: str,
                     tgt_ref: str) -> int:
    if len(path) < 2:
        return 0

    last_index = len(path) - 2
    hits = 0
    for i in range(len(path) - 1):
        p1, p2 = path[i], path[i + 1]
        if abs(p1[0] - p2[0]) < 1e-3 and abs(p1[1] - p2[1]) < 1e-3:
            continue
        for c in components:
            ref = c['ref_des']
            if ref == src_ref and i <= 1:
                continue
            if ref == tgt_ref and i >= last_index - 1:
                continue

            bbox = _rotated_bbox(c)
            if not bbox:
                continue
            if _seg_intersects_bbox(p1, p2, bbox, c['x'], c['y'],
                                    margin=BBOX_CLEARANCE):
                hits += 1
    return hits
