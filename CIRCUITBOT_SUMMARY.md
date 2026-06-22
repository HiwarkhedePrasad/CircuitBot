# CircuitBot E2E Audit — Session Summary

## v0.1 — First run analysis (initial fixes)

The first e2e run (prompt: *"ESP32-S3 with TMP117 temperature sensor"*) revealed **6 systemic bugs**:

| # | Bug | Root Cause | Fix Applied |
|---|-----|-----------|-------------|
| 1 | **Duplicate MCU** (ESP32-S3 selected by both `select:rank` AND `support_rules`→ESP32-WROOM) | `support_rules.py` injected CP2102N + WROOM module unconditionally for any ESP32 | Added dedup guard in `select.py:433` — `_is_duplicate_of()` checks same library prefix + base part number before injecting |
| 2 | **TMP117 ≠ DS18B20** (user asked for DS18B20, got TMP117xxYBG) | `ANALYZE_SYSTEM` prompt created generic "I2C temperature sensor" subsystem; DS18B20 is a Dallas 1-Wire part, so the reranker had no reason to pick it | Strengthened prompt in `analyze.py` to preserve user-specified part numbers verbatim in subsystems |
| 3 | **PWR_FLAG omitted** for multi-rail designs | `support_rules.py` only injected one PWR_FLAG; second rail (3.3V) was left floating | Added `PWR_FLAG` injection logic in `support_rules.py` for each distinct voltage rail |
| 4 | **Module preference loop** (validator flags bare IC → retry selects another bare IC → validator flags again → MAX_RETRIES=2 exhausted) | `rejected_ids` wasn't propagated back to `select.py`, so failed bare ICs were re-selected | Added `rejected_ids` tracking in `validate.py:463-467` to block previously-failed `id_str` values on retry |
| 5 | **WireBender crash** (pipeline hard-failed when `wire_bender` module missing) | `schematic_layout.py` unconditionally called WireBender; `pytest` dependency missing on some installs | Added import guard (`try/except ImportError`) to bypass when module absent |
| 6 | **`etype` extraction crash** (KeyError in `select.py`) | Some candidates lack `"etype"` key; `select.py` used `sub["etype"]` instead of `.get()` | Changed to `sub.get("etype", "")` |

### Files touched in v0.1

- `agent/nodes/analyze.py` — prompt string
- `agent/nodes/select.py` — dedup guard + etype fallback
- `agent/nodes/validate.py` — `rejected_ids` tracking
- `agent/nodes/schematic_layout.py` — WireBender import guard
- `agent/nodes/support_rules.py` — PWR_FLAG injection

---

## v0.2 — Second run analysis (prompt: *"ESP32 with DS18B20 and USB-C power connector"*)

**Result:** Pipeline completed (`total_pipeline_time=115s`, `score=51`, `valid=True`).
**Netlist:** ~24 components, 2 unfixable validation warnings tolerated.

### What held from v0.1 fixes ✅

| Fix | Verdict |
|-----|---------|
| Dedup guard (`#1`) | ✅ **Worked** — ESP32-C3 selected for both `Processing` and `User-specified parts` subsystems; second occurrence correctly skipped. |
| `rejected_ids` tracking (`#4`) | ✅ **Partial** — ESP32-S3 and ESP32-S2 correctly blocked on retry. Bare IC **still** re-selected on 2nd retry because ESP32-C3 was not added to rejected_ids in time (see below). |
| WireBender bypass (`#5`) | ✅ Pipeline didn't crash. |
| `etype` fallback (`#6`) | ✅ No crash. |
| PWR_FLAG (`#3`) | ✅ Injected. |

### What's still broken 🔴

| # | Bug | First seen | Root Cause |
|---|-----|-----------|------------|
| **B1** | **DS18B20 STILL ignored** — TMP117xxYBG selected again | v0.1 → still broken v0.2 | Two issues: (a) `ANALYZE_SYSTEM` prompt fix didn't propagate to the reranker, because the analyzer creates a generic *subsystem name* like `Sensing` while the user's specific part number `DS18B20` is embedded only in the description string. The reranker scores by `id_str` keyword match, not by description. (b) DS18B20 is a 1-Wire (Dallas) part, not I²C, so the subsystem type `Sensing (I2C)` misdirects the reranker toward I²C parts like TMP117. **Fix needed:** Either override the reranker score when a user-specified part number exists in the subsystem description, or inject the exact user-requested part (DS18B20) via `support_rules` after selection. |
| **B2** | **Duplicate USB-UART bridge** — J601 (CP2102C-Axx-xQFN24) selected by reranker for `Programming & Debug`, then U10 (CP2102N) injected by `support_rules` | v0.2 | My dedup guard compares exact base part name (`CP2102N` vs `CP2102C` — different!). Both are SilLabs CP2102x family, both are USB-UART bridges, but the guard treats them as distinct. **Fix needed:** Expand `_is_duplicate_of()` to match by **functional family** (e.g., any CP2102 variant), or check using the library prefix `Interface_USB` plus a broader match. |

### What degraded into a new issue 🟡

| # | Issue | v0.1 state | v0.2 state | Notes |
|---|-------|-----------|-----------|-------|
| **B3** | **Module preference loop** | Bare IC flagged → retry picks same bare IC → MAX_RETRIES=2 exhausted → pipeline completed with error | Same, but worst-case: first selection (S3+S2) → retry 1 (C3) → retry 2 (C3 again, not rejected properly) | The 3rd pass selected ESP32-C3 again because `rejected_ids` from the 2nd validation included it, but `select.py`'s retry logic didn't re-read the updated state properly. Also, the only module candidate (`ESP32-C3-DevKitM-1`) scores 5 vs bare IC's 8 — the reranker never picks it. **Fix needed:** (a) In `validate.py`, when bare IC is flagged, also auto-inject a module alternative into the candidate list. (b) Increase `MAX_VALIDATION_RETRIES` from 2 to 3. (c) Fix `rejected_ids` state propagation so the 3rd retry properly blocks the 2nd-batch rejected IDs. |
| **B4** | **Ref_des collision** | Not tracked | R2 used by both a decoupling cap and an EN pull-up | The dedup guard resolves this by skipping the duplicate R2. But if the collision were between two critical components, one would be silently dropped. **Fix needed:** None urgent, but `support_rules` should allocate ref_des from a separate pool or check `selected_components` before assigning. |

---

## Code Map

```
agent/
├── nodes/
│   ├── select.py          — Component selection + dedup guard (line 433)
│   ├── validate.py        — Module preference check (line 234) + rejected_ids (line 463) + DevKit redundancy (line 328)
│   ├── analyze.py         — System prompt for subsystem decomposition
│   ├── support_rules.py   — Auto-injection of support components (PWR_FLAG, pull-ups, UART bridges)
│   ├── schematic_layout.py — WireBender call with import guard
│   └── ...
└── ...
```

### Remaining critical files to change

- `validate.py` — Auto-inject module alternative when bare RF IC flagged
- `select.py` — Broaden `_is_duplicate_of()` for USB-UART family matching
- `analyze.py` — Ensure user-specified part numbers survive into the reranker context
- `agent.py` or config — Increase `MAX_VALIDATION_RETRIES` from 2 to 3
- `support_rules.py` — Optionally inject exact user-requested parts (DS18B20) when detected in user prompt

---

## Verdict

**Score trend:** v0.1 = 47 → v0.2 = 51 (modest improvement).
**Reliability:** Pipeline no longer crashes, but produces designs with tolerance of known errors (duplicate UART, wrong temp sensor, bare IC).
**Next priority:** Fix **B1** (DS18B20 override) and **B2** (USB-UART dedup) for correctness; fix **B3** (module preference) for reliability.
