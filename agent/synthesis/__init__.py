"""Synthesis package — canonical circuit graph, topology rules, and netlist synthesis.

Every stage in the synthesis pipeline operates on a common graph representation
rather than mutating raw LLM output directly.

Pipeline:
    selected_components + pin_matrix
        ↓
    SynthesisGraph.from_state()
        ↓
    Classifier → assigns PinRole to every pin (one-time, no downstream string matching)
        ↓
    Topology matcher → recognizes functional motifs (indicator LED, regulator, USB, etc.)
        ↓
    Constraint generator → produces ConstraintEdges (relationships, not hardcoded nets)
        ↓
    Graph validation → compares LLM netlist against constraints
        ↓
    Graph repair → reconciles differences
        ↓
    Physical netlist (source→target pairs + power_pins)
"""
