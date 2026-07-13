"""Smoke test: verify no-connect flags appear in KiCad schematic export for unconnected pins."""
from agent.kicad_export import generate_kicad_sch


def _make_design():
    return {
        'selected_components': [
            {'id_str': 'Device:R', 'ref_des': 'R1', 'category': 'resistor', 'description': '10k'},
            {'id_str': 'Device:LED', 'ref_des': 'D1', 'category': 'led', 'description': 'LED'},
        ],
        'component_ops': {
            'R1': [
                ['pin', ['name', '"1"'], ['number', '"1"'], ['at', '0', '0', '0'], ['length', '2.54']],
                ['pin', ['name', '"2"'], ['number', '"2"'], ['at', '0', '5.08', '180'], ['length', '2.54']],
            ],
            'D1': [
                ['pin', ['name', '"K"'], ['number', '"1"'], ['at', '0', '0', '0'], ['length', '2.54']],
                ['pin', ['name', '"A"'], ['number', '"2"'], ['at', '0', '5.08', '180'], ['length', '2.54']],
            ],
        },
        'component_placements': [
            {'ref_des': 'R1', 'x': 0, 'y': 0, 'rotation': 0},
            {'ref_des': 'D1', 'x': 20, 'y': 0, 'rotation': 0},
        ],
        # Only R1:2 -> D1:2 wired; R1:1 and D1:1 are unconnected
        'wire_paths': [
            {'source': 'R1:2', 'target': 'D1:2', 'path': [
                {'x': 0, 'y': 5.08}, {'x': 20, 'y': 5.08}
            ]},
        ],
        'power_labels': [],
        'netlist': [],
    }


def test_no_connect_flags_generated():
    """Unconnected pins should have (no_connect ...) entries in the export."""
    sch = generate_kicad_sch(_make_design())
    nc_lines = [l for l in sch.split('\n') if 'no_connect' in l]
    # R1:1 and D1:1 are unconnected — should get no_connect flags
    assert len(nc_lines) >= 2, f"Expected >=2 no_connect entries, got {len(nc_lines)}: {nc_lines}"
    for line in nc_lines:
        assert 'no_connect' in line
        assert '(at ' in line
        assert '(uuid ' in line


def test_connected_pins_no_flag():
    """Connected pins should NOT get no_connect flags."""
    design = _make_design()
    # Wire ALL pins
    design['wire_paths'].append({
        'source': 'R1:1', 'target': 'D1:1', 'path': [
            {'x': 0, 'y': 0}, {'x': 20, 'y': 0}
        ]
    })
    sch = generate_kicad_sch(design)
    nc_lines = [l for l in sch.split('\n') if 'no_connect' in l]
    # All pins connected — no no_connect flags
    assert len(nc_lines) == 0, f"Expected 0 no_connect entries when all pins wired, got {len(nc_lines)}"


def test_no_connect_format_valid():
    """Each no_connect entry should be valid KiCad S-expression."""
    sch = generate_kicad_sch(_make_design())
    for line in sch.split('\n'):
        if 'no_connect' not in line:
            continue
        stripped = line.strip()
        assert stripped.startswith('(no_connect'), f"Invalid format: {stripped}"
        assert '(at ' in stripped, f"Missing (at ...): {stripped}"
        assert '(uuid ' in stripped, f"Missing (uuid ...): {stripped}"
        assert stripped.endswith('))'), f"Invalid closing: {stripped}"
