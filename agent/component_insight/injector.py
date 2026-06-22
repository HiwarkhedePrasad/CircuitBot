from agent.component_insight.summarizer import generate_pin_summary


def lookup_pins_from_research(id_str: str, research_results: list[dict]) -> list[dict]:
    """Search through all research subsystem results to find the pin list for a given id_str."""
    for sub in research_results:
        for r in sub.get("results", []):
            if r.get("id_str") == id_str:
                return r.get("pins", [])
    return []


def build_component_pin_summary(id_str: str, research_results: list[dict]) -> str:
    """Convenience: lookup pins for a component and generate the summary string."""
    pins = lookup_pins_from_research(id_str, research_results)
    return generate_pin_summary(pins)
