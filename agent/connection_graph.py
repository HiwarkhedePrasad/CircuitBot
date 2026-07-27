"""ConnectivityGraph — intermediate representation between netlist and connection records."""

from __future__ import annotations

from agent.connection_strategy import classify_strategy, _is_passive


class ConnectivityGraph:
    def __init__(self, nets, pin_matrix, components, placements):
        self.nets: list[NetRecord] = []
        self.pin_matrix: dict = pin_matrix
        self.components: dict[str, dict] = {c["ref_des"]: c for c in components}
        self.placements: dict[str, dict] = placements or {}

        self._build(nets)
        self._classify()

    def _build(self, nets):
        for net_entry in nets:
            name = net_entry.get("net", "")
            pins = net_entry.get("pins", [])

            comps = list(dict.fromkeys(p.split(":")[0] for p in pins if ":" in p))
            comp_objs = [self.components[r] for r in comps if r in self.components]
            active = [c for c in comp_objs if self._is_active(c)]
            passive = [c for c in comp_objs if not self._is_active(c)]

            span = 0.0
            if len(pins) >= 2:
                xs, ys = [], []
                for pk in pins:
                    ref = pk.split(":")[0]
                    p = self.placements.get(ref)
                    if p:
                        xs.append(p.get("x", 0))
                        ys.append(p.get("y", 0))
                if xs:
                    span = max(xs) - min(xs) + max(ys) - min(ys)

            self.nets.append(NetRecord(
                name=name,
                pins=pins,
                active_components=active,
                passive_components=passive,
                span=span,
            ))

    def _is_active(self, comp: dict) -> bool:
        id_str = comp.get("id_str", "")
        category = comp.get("category", "")
        if _is_passive(id_str, category):
            return False
        if id_str.startswith("Connector:") or (category or "").upper() == "CONNECTOR":
            return False
        if id_str.startswith("power:"):
            return False
        return True

    def _classify(self):
        for net in self.nets:
            net.strategy = classify_strategy(net.name, net.pins, self.components, self.placements)


class NetRecord:
    def __init__(self, name="", pins=None, active_components=None, passive_components=None, span=0.0):
        self.name = name
        self.pins = pins or []
        self.active_components = active_components or []
        self.passive_components = passive_components or []
        self.span = span
        self.strategy = None

    def __repr__(self):
        return f"NetRecord({self.name}, {len(self.pins)} pins, strategy={self.strategy})"
