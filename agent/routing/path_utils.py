from __future__ import annotations


def _path_length(path: list[tuple[float, float]]) -> float:
    total = 0.0
    for i in range(len(path) - 1):
        total += abs(path[i][0] - path[i + 1][0]) + \
                 abs(path[i][1] - path[i + 1][1])
    return total


def _bend_count(path: list[tuple[float, float]]) -> int:
    if len(path) < 3:
        return 0
    bends = 0
    for i in range(1, len(path) - 1):
        dx1 = path[i][0] - path[i - 1][0]
        dy1 = path[i][1] - path[i - 1][1]
        dx2 = path[i + 1][0] - path[i][0]
        dy2 = path[i + 1][1] - path[i][1]
        if abs(dx1 - dx2) > 1e-3 or abs(dy1 - dy2) > 1e-3:
            bends += 1
    return bends


def _clean_path(path: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not path:
        return []
    cleaned = [path[0]]
    for p in path[1:]:
        last = cleaned[-1]
        if abs(last[0] - p[0]) < 1e-3 and abs(last[1] - p[1]) < 1e-3:
            continue
        cleaned.append(p)
    if len(cleaned) < 3:
        return cleaned
    out = [cleaned[0]]
    for i in range(1, len(cleaned) - 1):
        x0, y0 = out[-1]
        x1, y1 = cleaned[i]
        x2, y2 = cleaned[i + 1]
        dx1, dy1 = x1 - x0, y1 - y0
        dx2, dy2 = x2 - x1, y2 - y1
        if abs(dx1 * dy2 - dy1 * dx2) < 1e-6 and \
           dx1 * dx2 >= -1e-6 and dy1 * dy2 >= -1e-6:
            continue
        out.append(cleaned[i])
    out.append(cleaned[-1])
    return out


def _is_orthogonal(path: list[tuple[float, float]]) -> bool:
    for i in range(len(path) - 1):
        dx = abs(path[i][0] - path[i + 1][0])
        dy = abs(path[i][1] - path[i + 1][1])
        if dx > 1e-3 and dy > 1e-3:
            return False
    return True
