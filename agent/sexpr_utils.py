# KiCad coordinate system constants
GRID = 1.27  # KiCad default schematic grid in mm (50 mil)


def snap(v: float) -> float:
    """Snap a coordinate to the nearest 1.27mm grid point."""
    return round(v / GRID) * GRID


def escape_sexpr_string(val: str) -> str:
    """Escape a string for safe embedding into KiCad S-expression files."""
    if val is None:
        return '""'
    s = str(val)
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")
    return f'"{s}"'


def pin_transform(pin_x: float, pin_y: float, rotation: int = 0) -> tuple:
    """Transform pin position from library Y-up space to schematic Y-down offset.

    In library space: Y-up (positive Y = up on screen)
    In schematic space: Y-down (positive Y = down on screen)

    Args:
        pin_x, pin_y: Pin position in library (symbol) coordinates
        rotation: Symbol rotation in degrees (0, 90, 180, 270)

    Returns:
        (dx, dy): Offset to add to symbol placement position
    """
    transforms = {
        0:   ( pin_x, -pin_y),
        90:  ( pin_y,  pin_x),
        180: (-pin_x,  pin_y),
        270: (-pin_y, -pin_x),
    }
    if rotation not in transforms:
        raise ValueError(f"Rotation must be 0, 90, 180, or 270. Got {rotation}")
    return transforms[rotation]


def pin_abs(sx: float, sy: float, px: float, py: float,
            rotation: int = 0) -> tuple:
    """Compute absolute schematic position of a pin.

    Args:
        sx, sy: Symbol placement position in schematic
        px, py: Pin position in library (symbol) coordinates
        rotation: Symbol rotation (0, 90, 180, 270)

    Returns:
        (abs_x, abs_y): Absolute pin position, grid-snapped
    """
    dx, dy = pin_transform(px, py, rotation)
    return (snap(sx + dx), snap(sy + dy))


def validate_sexpr_parentheses(content: str) -> bool:
    """Check that S-expression parentheses are balanced.

    Returns True if balanced, False otherwise.
    """
    depth = 0
    in_string = False
    for i, c in enumerate(content):
        if c == '"' and (i == 0 or content[i-1] != '\\'):
            in_string = not in_string
        elif not in_string:
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
        if depth < 0:
            return False
    return depth == 0


def _parse_sexpr_to_ops(sexpr_str: str, lib_name: str, _depth: int = 0) -> list:
    acc = []
    extends = None

    def parse(s):
        tokens, i = [], 0
        while i < len(s):
            c = s[i]
            if c == '(':
                tokens.append(c); i += 1
            elif c == ')':
                tokens.append(c); i += 1
            elif c in ' \t\n\r':
                i += 1
            elif c == '"':
                j = i + 1
                while j < len(s) and not (s[j] == '"' and s[j-1] != '\\'):
                    j += 1
                tokens.append(s[i:j+1]); i = j + 1
            else:
                j = i
                while j < len(s) and s[j] not in '() \t\n\r':
                    j += 1
                tokens.append(s[i:j]); i = j
        stack, root = [], []
        stack.append(root)
        for t in tokens:
            if t == '(':
                n = []; stack[-1].append(n); stack.append(n)
            elif t == ')':
                if len(stack) > 1: stack.pop()
            else:
                v = t[1:-1] if t.startswith('"') and t.endswith('"') else t
                stack[-1].append(v)
        return root[0] if root else []

    ast = parse(sexpr_str)
    if not ast:
        return acc

    def walk(node):
        nonlocal extends
        if not isinstance(node, list) or not node:
            return
        typ = node[0]
        if typ in ("rectangle", "polyline", "circle", "arc", "pin", "property", "text"):
            acc.append(node)
        if typ == "extends" and len(node) > 1 and extends is None:
            extends = node[1]
        if typ in ("symbol", "kicad_symbol_lib"):
            for child in node[1:]:
                walk(child)

    walk(ast)
    if extends and _depth < 5:
        try:
            from agent.tools import fetch_sexpr
            parent_sexpr = fetch_sexpr(f"{lib_name}:{extends}")
            parent_ops = _parse_sexpr_to_ops(parent_sexpr, lib_name, _depth + 1)
            parent_ops.extend(acc)
            return parent_ops
        except Exception as e:
            print(f"Failed to resolve extends '{extends}' in lib '{lib_name}': {e}")
    return acc


def _extract_pins_from_ops(ops: list, ref_des: str) -> dict:
    GRID_SIZE = 1.27
    pin_matrix = {}
    for op in ops:
        if op[0] != "pin":
            continue
        at = _get_attr(op, "at")
        len_node = _get_attr(op, "length")
        num_node = _get_attr(op, "number")
        if not at or not len_node or not num_node:
            continue
        try:
            px = float(at[1])
            py = float(at[2])
            ang_deg = float(at[3]) if len(at) > 3 else 0
            length = float(len_node[1])
        except (ValueError, IndexError):
            continue
        ang_rad = ang_deg * 3.14159 / 180.0
        cos_a = round(1.0 if ang_deg == 0 else (-1.0 if ang_deg == 180 else 0.0), 2)
        sin_a = round(1.0 if ang_deg == 90 else (-1.0 if ang_deg == 270 else 0.0), 2)
        if abs(cos_a) < 0.1 and abs(sin_a) < 0.1:
            import math
            cos_a = math.cos(ang_rad)
            sin_a = math.sin(ang_rad)
        name_node = _get_attr(op, "name")
        pin_name = name_node[1] if name_node else ""
        pin_num = num_node[1].replace('"', '').strip()
        if not pin_num:
            continue
        etype_node = _get_attr(op, "electrical_type")
        if etype_node:
            etype = etype_node[1]
        elif len(op) > 1 and isinstance(op[1], str):
            etype = op[1]
        else:
            etype = "passive"
        key = f"{ref_des}:{pin_num}"
        if key in pin_matrix:
            continue
        # Keep pin coordinates faithful to the symbol definition.  Moving
        # coincident hidden pins here made backend wires terminate somewhere
        # that neither the renderer nor KiCad considered a pin.
        pin_matrix[key]={
            "x":round(px/GRID_SIZE)*GRID_SIZE,
            "y":round(py/GRID_SIZE)*GRID_SIZE,
            "name":pin_name.strip(),
            "ref_des":ref_des,
            "pin_num":pin_num,
            "angle":int(round(ang_deg))%360,
            "etype":etype,
        }

    return pin_matrix


def _get_attr(node, name):
    if not isinstance(node, list):
        return None
    for child in node[1:]:
        if isinstance(child, list) and child[0] == name:
            return child
    return None
