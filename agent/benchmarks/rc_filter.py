"""4-component RC low-pass filter — simplest possible benchmark."""


def load():
    return {
        "name": "RC Filter",
        "description": "4-component RC low-pass filter with input, resistor, capacitor, output",
        "components": [
            {
                "ref_des": "J1",
                "id_str": "Connector:Conn_01x01",
                "category": "Connector",
                "description": "Input connector",
                "for_component": "",
                "ops": [["rectangle", ["start", -5, -3], ["end", 5, 3]]],
                "footprint": "",
            },
            {
                "ref_des": "R1",
                "id_str": "Device:R",
                "category": "Device",
                "description": "1k resistor",
                "for_component": "",
                "ops": [["rectangle", ["start", -5, -2], ["end", 5, 2]]],
                "footprint": "",
            },
            {
                "ref_des": "C1",
                "id_str": "Device:C_Small",
                "category": "Device",
                "description": "10uF capacitor",
                "for_component": "",
                "ops": [["rectangle", ["start", -4, -3], ["end", 4, 3]]],
                "footprint": "",
            },
            {
                "ref_des": "J2",
                "id_str": "Connector:Conn_01x01",
                "category": "Connector",
                "description": "Output connector",
                "for_component": "",
                "ops": [["rectangle", ["start", -5, -3], ["end", 5, 3]]],
                "footprint": "",
            },
        ],
        "netlist": [
            {"source": "J1:1", "target": "R1:1", "net": "IN"},
            {"source": "R1:2", "target": "C1:1", "net": "OUT"},
            {"source": "C1:2", "target": "J2:1", "net": "OUT"},
        ],
        "pin_matrix": {
            "J1:1":  {"x": 5, "y": 0, "angle": 0},
            "R1:1":  {"x": -5, "y": 0, "angle": 180},
            "R1:2":  {"x": 5, "y": 0, "angle": 0},
            "C1:1":  {"x": -4, "y": 0, "angle": 180},
            "C1:2":  {"x": 4, "y": 0, "angle": 0},
            "J2:1":  {"x": -5, "y": 0, "angle": 180},
        },
    }
