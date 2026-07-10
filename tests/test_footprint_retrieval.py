"""Tests for footprint retrieval and fp_filters resolution."""
import pytest
from kicad_rag.store import lookup_footprint, resolve_footprint_from_filters, _con


def test_lookup_footprint_with_empty_string():
    """Test that empty footprint returns valid dict, not None."""
    result = lookup_footprint("Device:R")
    assert result is not None
    assert "fp_filters" in result
    assert result["footprint"] == ""
    assert result["fp_filters"] == ["R_*"]


def test_lookup_footprint_with_non_empty():
    """Test that components with footprints work correctly."""
    # Find a component with a non-empty footprint
    con = _con()
    try:
        row = con.execute(
            "SELECT id_str FROM symbols WHERE length(footprint) > 0 LIMIT 1"
        ).fetchone()
        if row:
            result = lookup_footprint(row[0])
            assert result is not None
            assert result["footprint"] != ""
    finally:
        con.close()


def test_lookup_footprint_nonexistent():
    """Test that nonexistent component returns None."""
    result = lookup_footprint("NonExistent:Component")
    assert result is None


def test_resolve_footprint_from_filters():
    """Test fp_filters resolution."""
    result = resolve_footprint_from_filters("Device:R")
    assert result is not None
    assert "Resistor_SMD" in result
    # Any R_ footprint is valid (R_0805, R_0402, R_01005, etc.)
    assert result.startswith("Resistor_SMD:R_")


def test_resolve_footprint_from_filters_capacitor():
    """Test fp_filters resolution for capacitor."""
    result = resolve_footprint_from_filters("Device:C")
    assert result is not None
    assert "Capacitor_SMD" in result


def test_resolve_footprint_from_filters_led():
    """Test fp_filters resolution for LED."""
    result = resolve_footprint_from_filters("Device:LED")
    assert result is not None
    assert "LED_SMD" in result


def test_resolve_footprint_no_match():
    """Test when no footprint matches filters."""
    result = resolve_footprint_from_filters("NonExistent:Component")
    assert result is None


def test_resolve_footprint_returns_first_match():
    """Test that resolve_footprint returns a valid footprint path."""
    result = resolve_footprint_from_filters("Device:R")
    if result:
        from kicad_rag.store import footprint_path_for
        path = footprint_path_for(result)
        assert path.is_file(), f"Footprint file not found: {path}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
