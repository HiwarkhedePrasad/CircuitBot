"""Download common KiCad 3D models for CircuitBot's 3D viewer.

Run once to bundle models locally:
    python scripts/download_3d_models.py

Models are downloaded from the official KiCad kicad-packages3d repository
and stored in data/3d_models/.
"""

import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

BASE_URL = "https://raw.githubusercontent.com/KiCad/kicad-packages3d/main"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "3d_models"

# Common 3D models to bundle (library.3dshapes/filename.step)
MODELS = [
    # Resistors
    "Resistor_SMD.3dshapes/R_0402_1005Metric.step",
    "Resistor_SMD.3dshapes/R_0603_1608Metric.step",
    "Resistor_SMD.3dshapes/R_0805_2012Metric.step",
    "Resistor_SMD.3dshapes/R_1206_3216Metric.step",
    "Resistor_SMD.3dshapes/R_2512_6332Metric.step",
    # Capacitors
    "Capacitor_SMD.3dshapes/C_0402_1005Metric.step",
    "Capacitor_SMD.3dshapes/C_0603_1608Metric.step",
    "Capacitor_SMD.3dshapes/C_0805_2012Metric.step",
    "Capacitor_SMD.3dshapes/C_1206_3216Metric.step",
    # IC Packages
    "Package_SO.3dshapes/SOIC-8_3.9x4.9_P1.27mm.step",
    "Package_SO.3dshapes/SOIC-16_3.9x9.9_P1.27mm.step",
    "Package_DFN_QFN.3dshapes/QFN-16-1EP_4x4mm_P0.5mm_EP2.5x2.5mm.step",
    "Package_DFN_QFN.3dshapes/QFN-32-1EP_5x5mm_P0.5mm_EP3.65x3.65mm.step",
    "Package_DIP.3dshapes/DIP-8_W7.62mm.step",
    "Package_DIP.3dshapes/DIP-16_W7.62mm.step",
    # LEDs
    "LED_SMD.3dshapes/LED_0603_1608Metric.step",
    "LED_SMD.3dshapes/LED_0805_2012Metric.step",
    # Crystal
    "Crystal.3dshapes/Crystal_SMD_3215-2Pin_3.2x1.5mm.step",
    "Crystal.3dshapes/Crystal_SMD_5032-2Pin_5.0x3.2mm.step",
    # Connectors
    "Connector_PinHeader_2.54mm.3dshapes/PinHeader_1x04_P2.54mm_Vertical.step",
    "Connector_PinHeader_2.54mm.3dshapes/PinHeader_1x08_P2.54mm_Vertical.step",
    "Connector_PinHeader_2.54mm.3dshapes/PinHeader_2x05_P2.54mm_Vertical.step",
    "Connector_USB.3dshapes/USB_C_Receptacle_XKB_U262-16XN-4BVC11.step",
    "Connector_BarrelJack.3dshapes/Barrel_Jack_Horizontal.step",
    # Voltage Regulators
    "Package_TO_SOT_SMD.3dshapes/SOT-23.step",
    "Package_TO_SOT_SMD.3dshapes/SOT-223-3_TabPin2.step",
    "Package_TO_SOT_SMD.3dshapes/SOT-23-5.step",
]


def download_model(rel_path: str) -> bool:
    url = f"{BASE_URL}/{rel_path}"
    out_path = OUTPUT_DIR / rel_path
    if out_path.exists():
        print(f"  [skip] {rel_path} (already exists)")
        return True
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        print(f"  [download] {rel_path} ...", end=" ", flush=True)
        urllib.request.urlretrieve(url, str(out_path))
        size_kb = out_path.stat().st_size / 1024
        print(f"OK ({size_kb:.0f} KB)")
        return True
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}")
        if out_path.exists():
            out_path.unlink()
        return False
    except Exception as e:
        print(f"ERROR: {e}")
        if out_path.exists():
            out_path.unlink()
        return False


def main():
    print(f"Bundling 3D models into {OUTPUT_DIR}\n")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    fail = 0
    for model in MODELS:
        if download_model(model):
            ok += 1
        else:
            fail += 1
    print(f"\nDone: {ok} downloaded, {fail} failed")


if __name__ == "__main__":
    main()
