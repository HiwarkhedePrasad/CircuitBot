"""Test RAG search behavior with library filters and part numbers."""

from agent.tools import search_components
from kicad_rag.client import KicadRAG

rag = KicadRAG()

print("1. Direct part search for 'DS18B20':")
res1 = search_components("DS18B20", k=5)
for r in res1:
    print(f"  - {r['id_str']} (score={r['score']:.4f})")

print("\n2. Direct part search for 'AMS1117-3.3':")
res2 = search_components("AMS1117-3.3", k=5)
for r in res2:
    print(f"  - {r['id_str']} (score={r['score']:.4f})")

print("\n3. Subsystem search for 'Power Input' with library_filter='Connector_USB|Connector':")
res3 = search_components("Power Input", k=5, library_filter="Connector_USB|Connector")
for r in res3:
    print(f"  - {r['id_str']} (score={r['score']:.4f})")

print("\n4. Subsystem search for 'Power Regulation' with library_filter='Regulator_Linear|Regulator_Switching':")
res4 = search_components("Power Regulation", k=5, library_filter="Regulator_Linear|Regulator_Switching")
for r in res4:
    print(f"  - {r['id_str']} (score={r['score']:.4f})")
