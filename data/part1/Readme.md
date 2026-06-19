# Reference Design / Application Note Knowledge Base — Complete Reference

A **hybrid RAG knowledge base for hardware design**, pairing a **SQLite**
structured database with a **FAISS** vector index. Built for LangGraph-based
EDA agents that need to retrieve manufacturer-grade design rules (decoupling,
boot strapping, PD negotiation, antenna matching, CAN termination, etc.)
before generating a netlist.

This README is the **single source of truth** for the database: every table,
every column, every constraint, every seed row, every index, every query
pattern, and every integration point.

---

## Table of Contents

1. [Deliverables](#1-deliverables)
2. [Why this exists](#2-why-this-exists)
3. [High-level architecture](#3-high-level-architecture)
4. [Entity-Relationship diagram](#4-entity-relationship-diagram)
5. [Schema reference — every table, every column](#5-schema-reference--every-table-every-column)
   - 5.1 [`components`](#51-components)
   - 5.2 [`application_notes`](#52-application_notes)
   - 5.3 [`topologies`](#53-topologies)
   - 5.4 [`design_rules`](#54-design_rules)
   - 5.5 [`vector_index`](#55-vector_index)
   - 5.6 [`schema_meta`](#56-schema_meta)
6. [Indexes](#6-indexes)
7. [Foreign-key graph](#7-foreign-key-graph)
8. [Seed data — complete enumeration](#8-seed-data--complete-enumeration)
   - 8.1 [All 20 components](#81-all-20-components)
   - 8.2 [All 14 application notes](#82-all-14-application-notes)
   - 8.3 [All 12 topologies (with rule counts)](#83-all-12-topologies-with-rule-counts)
   - 8.4 [All 53 design rules (full text + citations)](#84-all-53-design-rules-full-text--citations)
   - 8.5 [All 79 vector-index chunks](#85-all-79-vector-index-chunks)
   - 8.6 [All `schema_meta` keys](#86-all-schema_meta-keys)
9. [The embedding function](#9-the-embedding-function)
10. [Querying — pure SQL, pure vector, hybrid](#10-querying--pure-sql-pure-vector-hybrid)
11. [LangGraph integration](#11-langgraph-integration)
12. [Extending the knowledge base](#12-extending-the-knowledge-base)
13. [Upgrade path to semantic embeddings](#13-upgrade-path-to-semantic-embeddings)
14. [Runtime dependencies](#14-runtime-dependencies)
15. [Limitations & disclaimers](#15-limitations--disclaimers)
16. [Citation policy](#16-citation-policy)

---

## 1. Deliverables

Exactly three files. **No other files** are needed to use this knowledge base —
no scripts, no configs, no model weights.

| File              | Size   | Role                                                                                          |
|-------------------|-------:|-----------------------------------------------------------------------------------------------|
| `README.md`       | ~30 KB | This document. The single source of truth.                                                     |
| `knowledge_base.db` | ~104 KB | SQLite database: 6 tables, 4 indexes, ~109 rows total.                                         |
| `vectors.faiss`   | ~120 KB | FAISS `IndexFlatIP` over 79 × 384-dim L2-normalised float32 vectors. Drop-in compatible with `sentence-transformers/all-MiniLM-L6-v2`. |

All three files live in the same directory and must be kept together — the
FAISS index is referenced by row number from the `vector_index` table, and
the embedder params (needed at query time) are stored in `schema_meta`.

---

## 2. Why this exists

When a hardware LLM agent generates a netlist from its pre-trained memory, it
hallucinates component values: *"use a 1 kΩ pull-up"* when TI's app note says
*10 kΩ*, or *"add a 10 µF cap to VBUS"* when the CH224K datasheet says *22 µF
X5R 25 V*. The fix is **grounding**: retrieve the actual engineering rule from
a curated knowledge base before the LLM emits the netlist, then ask the LLM to
follow the retrieved rule verbatim and cite its source.

This knowledge base is that grounding layer. It is **not** a generic web-search
RAG. Every record points to a specific manufacturer document (TI `SLVA`/`SLAA`,
ST `AN2867`, Microchip `AVR042`, Espressif HW Design Guide, Infineon CCG3PA
reference, Nordic OPS, etc.) so the agent's output is auditable.

### What problem this solves
- **Eliminates netlist hallucinations** — the agent doesn't guess; it copies.
- **Scalable** — drop a new app note into the DB and the agent instantly knows
  how to wire the new chip. No fine-tuning.
- **Explainable** — every added resistor / capacitor carries a `source_reference`
  like `"TI SLVA777 §2.3"` that the agent can surface in its output.

---

## 3. High-level architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                  knowledge_base.db  (SQLite, 6 tables)               │
│                                                                      │
│   ┌─────────────┐    ┌────────────────────┐    ┌──────────────┐     │
│   │ components  │    │ application_notes  │    │  topologies  │     │
│   │             │    │                    │    │              │     │
│   │ part_number │    │ doc_id             │◄───│ app_note_id  │     │
│   │ manufacturer│    │ title              │    │ title        │     │
│   │ category    │    │ manufacturer       │    │ subsystem    │     │
│   │ datasheet   │    │ source_url         │    │ ic_family    │     │
│   └─────────────┘    └────────────────────┘    │ tags (JSON)  │     │
│                                                │ description  │     │
│                                                └──────┬───────┘     │
│                                                       │             │
│                                                       │ FK          │
│                                                       ▼             │
│                                                ┌──────────────┐     │
│                                                │ design_rules │     │
│                                                │              │     │
│                                                │ rule_text    │     │
│                                                │ constraint   │     │
│                                                │ rationale    │     │
│                                                │ source_ref   │     │
│                                                └──────┬───────┘     │
│                                                       │             │
│   ┌──────────────┐                                   │             │
│   │ schema_meta  │   (key/value build metadata)      │             │
│   └──────────────┘                                   │             │
│                                                      ▼             │
│   ┌──────────────────────────────────────────────────────────┐     │
│   │ vector_index                                             │     │
│   │ (faiss_id, record_type, record_id, text_chunk)           │     │
│   │                                                          │     │
│   │   record_type ∈ {'topology','design_rule','app_note'}    │     │
│   │   record_id   → topologies.id / design_rules.id /        │     │
│   │                  application_notes.id                    │     │
│   └─────────────────────────┬────────────────────────────────┘     │
└─────────────────────────────┼──────────────────────────────────────┘
                              │
                              │ faiss_id  ↔  row in FAISS index
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                  vectors.faiss  (FAISS IndexFlatIP)                  │
│                                                                      │
│   79 × 384-dim float32, L2-normalised                                │
│   similarity = inner product = cosine (since ||v|| = 1)              │
│                                                                      │
│   Each row corresponds 1:1 to a row in `vector_index`.              │
│   Row 0 = faiss_id 0, row 78 = faiss_id 78.                          │
└──────────────────────────────────────────────────────────────────────┘
```

**Why hybrid?**
- **Structured (SQLite)** — fast filtering by `ic_family`, `subsystem`,
  `manufacturer`, `tags`. Exact-match lookups ("give me the datasheet URL for
  ESP32-WROOM-32"). Relational joins between topologies and their design rules.
- **Semantic (FAISS)** — natural-language queries like *"how do I keep the
  ESP32 from browning out during Wi-Fi TX?"* match on meaning, not keywords.

A real query almost always uses **both**: filter the candidate set by
`ic_family = 'ESP32'` in SQL, then rank the survivors by cosine similarity to
the user's question.

---

## 4. Entity-Relationship diagram

```
┌─────────────────────┐         ┌────────────────────────┐
│   components        │         │   application_notes    │
├─────────────────────┤         ├────────────────────────┤
│ PK  id              │         │ PK  id                 │
│ UQ  part_number     │         │     doc_id             │
│     manufacturer    │         │     title              │
│     category        │         │     manufacturer       │
│     description     │         │     category           │
│     datasheet_url   │         │     source_url         │
└─────────────────────┘         │     description        │
                                └───────────┬────────────┘
                                            │ 1
                                            │
                                            │ N
                                ┌───────────▼────────────┐
                                │   topologies           │
                                ├────────────────────────┤
                                │ PK  id                 │
                                │ FK  app_note_id ───────┼─► application_notes.id
                                │     title              │
                                │     subsystem          │
                                │     ic_family          │
                                │     description        │
                                │     tags (JSON)        │
                                └───────────┬────────────┘
                                            │ 1
                                            │
                                            │ N
                                ┌───────────▼────────────┐
                                │   design_rules         │
                                ├────────────────────────┤
                                │ PK  id                 │
                                │ FK  topology_id ───────┼─► topologies.id
                                │     rule_text          │
                                │     component_constraint│
                                │     rationale          │
                                │     source_reference   │
                                └────────────────────────┘

┌─────────────────────────────┐         ┌─────────────────────────┐
│   vector_index              │         │   schema_meta           │
├─────────────────────────────┤         ├─────────────────────────┤
│ PK  faiss_id                │         │ PK  key                 │
│     record_type             │         │     value               │
│     record_id               │         └─────────────────────────┘
│     text_chunk              │
└─────────────────────────────┘
   record_type='topology'      → record_id = topologies.id
   record_type='design_rule'   → record_id = design_rules.id
   record_type='app_note'      → record_id = application_notes.id

   (No DB-level FK from vector_index.record_id — enforced by application
    logic at build time. This is deliberate so the vector_index can outlive
    a partial schema rebuild.)
```

---

## 5. Schema reference — every table, every column

The exact DDL is reproduced from the live database. Each column lists:
**name · type · nullability · constraints · meaning · example value**.

### 5.1 `components`

The parts catalogue. 20 representative parts cross-referenced by topologies.

```sql
CREATE TABLE components (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    part_number   TEXT NOT NULL UNIQUE,
    manufacturer  TEXT NOT NULL,
    category      TEXT NOT NULL,
    description   TEXT,
    datasheet_url TEXT
);
```

| Column          | Type    | Nullable | Constraint        | Meaning                                              | Example                                |
|-----------------|---------|----------|-------------------|------------------------------------------------------|----------------------------------------|
| `id`            | INTEGER | NO       | PK, AUTOINCREMENT | Synthetic row id                                     | `7`                                    |
| `part_number`   | TEXT    | NO       | UNIQUE            | Manufacturer part number                             | `ESP32-WROOM-32`                       |
| `manufacturer`  | TEXT    | NO       | —                 | Manufacturer name                                    | `Espressif`                            |
| `category`      | TEXT    | NO       | —                 | Free-form category (`Manufacturer/Function`)         | `MCU/Wireless`                         |
| `description`   | TEXT    | YES      | —                 | Short human-readable description                     | `Wi-Fi + BT dual-core MCU module, 4 MB flash, 520 KB SRAM` |
| `datasheet_url` | TEXT    | YES      | —                 | Canonical datasheet URL on the manufacturer's site   | `https://www.espressif.com/.../esp32-wroom-32_datasheet_en.pdf` |

**Categories used in seed data:**
`MCU/Wireless`, `MCU`, `Power/Buck`, `Power/BuckBoost`, `Power/LDO`,
`Power/USB-PD`, `Interface/USB`, `Interface/CAN`, `Analog/ADC`,
`Analog/Sensor`, `Sensor/Env`.

---

### 5.2 `application_notes`

Manufacturer documents that back the topologies.

```sql
CREATE TABLE application_notes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id        TEXT,
    title         TEXT NOT NULL,
    manufacturer  TEXT NOT NULL,
    category      TEXT,
    source_url    TEXT,
    description   TEXT
);
```

| Column         | Type    | Nullable | Constraint        | Meaning                                              | Example                                |
|----------------|---------|----------|-------------------|------------------------------------------------------|----------------------------------------|
| `id`           | INTEGER | NO       | PK, AUTOINCREMENT | Synthetic row id                                     | `2`                                    |
| `doc_id`       | TEXT    | YES      | —                 | Manufacturer document ID                             | `AN2867`                               |
| `title`        | TEXT    | NO       | —                 | Document title                                       | `Oscillator design guide for STM32 microcontrollers` |
| `manufacturer` | TEXT    | NO       | —                 | Publisher                                            | `STMicroelectronics`                   |
| `category`     | TEXT    | YES      | —                 | Free-form category                                   | `MCU/Clock`                            |
| `source_url`   | TEXT    | YES      | —                 | Canonical URL the agent should cite                  | `https://www.st.com/.../cd00221665-...pdf` |
| `description`  | TEXT    | YES      | —                 | What this app note covers (also embedded)            | `ST's canonical reference for HSE crystal selection, load-capacitor computation, and layout for STM32 families.` |

**Note:** `doc_id` is nullable because some "app notes" (e.g. the Adafruit
ESP32 Feather, SparkFun RedBoard) are open-source board references without a
formal document ID.

---

### 5.3 `topologies`

A topology is a named circuit pattern (e.g. "ESP32-WROOM-32 Boot and Power
Requirements"). Each topology ties an `ic_family` to an `application_note`
and groups one or more `design_rules`.

```sql
CREATE TABLE topologies (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    subsystem     TEXT,
    ic_family     TEXT,
    description   TEXT,
    app_note_id   INTEGER,
    tags          TEXT,            -- JSON array
    FOREIGN KEY (app_note_id) REFERENCES application_notes(id)
);
```

| Column        | Type    | Nullable | Constraint                  | Meaning                                              | Example                                |
|---------------|---------|----------|-----------------------------|------------------------------------------------------|----------------------------------------|
| `id`          | INTEGER | NO       | PK, AUTOINCREMENT           | Synthetic row id                                     | `4`                                    |
| `title`       | TEXT    | NO       | —                           | Human-readable topology title                        | `CH224K USB-C PD 5V/9V/12V Sink Wiring` |
| `subsystem`   | TEXT    | YES      | Indexed (`idx_topologies_subsystem`) | Functional subsystem                       | `Power/USB-PD`                         |
| `ic_family`   | TEXT    | YES      | Indexed (`idx_topologies_ic_family`) | Chip family the topology applies to        | `CH224K`                               |
| `description` | TEXT    | YES      | —                           | Overview (also embedded as `topology` chunk)         | `USB-C PD 3.0 sink controller reference for requesting fixed voltages...` |
| `app_note_id` | INTEGER | YES      | FK → `application_notes.id` | Backing app note (NULL allowed for open-HW refs)     | `8`                                    |
| `tags`        | TEXT    | YES      | JSON array as TEXT          | Free-form tags for filtering                         | `["ch224k","usb-c","pd","power","sink"]` |

**Subsystems used in seed data:**
`MCU/Boot`, `MCU/Clock`, `Power/USB-PD`, `Power/Buck`, `Power/LDO`,
`Interface/CAN`, `Analog/ADC`, `Sensor/Env`, `OpenHW/System`,
`Power/MCU/Wireless`.

**IC families used:** `ESP32`, `STM32F4`, `AVR`, `CH224K`, `TPS54360`,
`LT3042`, `TJA1042/MCP2562`, `ADS1115`, `BME280`, `nRF52840`.

**Tags format:** JSON-encoded array of lowercase strings. Query with `LIKE`:
```sql
SELECT * FROM topologies WHERE tags LIKE '%usb-c%';
```

---

### 5.4 `design_rules`

The atomic engineering rules — the actual reason this DB exists. Each rule
is an imperative sentence with an exact component constraint and a citation.

```sql
CREATE TABLE design_rules (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    topology_id         INTEGER NOT NULL,
    rule_text           TEXT NOT NULL,
    component_constraint TEXT,
    rationale           TEXT,
    source_reference    TEXT,
    FOREIGN KEY (topology_id) REFERENCES topologies(id)
);
```

| Column                  | Type    | Nullable | Constraint                       | Meaning                                              | Example                                |
|-------------------------|---------|----------|----------------------------------|------------------------------------------------------|----------------------------------------|
| `id`                    | INTEGER | NO       | PK, AUTOINCREMENT                | Synthetic row id                                     | `14`                                   |
| `topology_id`           | INTEGER | NO       | FK → `topologies.id`, indexed    | Parent topology                                      | `4`                                    |
| `rule_text`             | TEXT    | NO       | —                                | Imperative engineering rule (also embedded)          | `Tie CFG1 (pin 5) to GND for default 5 V request. For 9 V, tie CFG1 to GND and CFG2 (pin 6) to GND...` |
| `component_constraint`  | TEXT    | YES      | —                                | The exact constraint, parseable by downstream code  | `CFG1/CFG2 strapping per voltage table` |
| `rationale`             | TEXT    | YES      | —                                | Why this rule exists                                 | `CFG pins are sampled at PD negotiation start; wrong strapping requests the wrong voltage.` |
| `source_reference`      | TEXT    | YES      | —                                | Citation the agent should surface                    | `WCH CH224 App Note §2.1`              |

**Foreign-key behavior:** with `PRAGMA foreign_keys = ON` (set at build time),
deleting a topology cascades the delete to its design rules via the FK.
SQLite does not enforce FKs by default — your application must set
`PRAGMA foreign_keys = ON;` after every connection.

---

### 5.5 `vector_index`

The bridge between FAISS row IDs and SQLite records. **One row per embedded
chunk** (a topology, a design rule, or an app note). This is the only table
that knows what each row in `vectors.faiss` actually means.

```sql
CREATE TABLE vector_index (
    faiss_id     INTEGER PRIMARY KEY,    -- row in FAISS index (0..N-1)
    record_type  TEXT NOT NULL,           -- 'topology' | 'design_rule' | 'app_note'
    record_id    INTEGER NOT NULL,
    text_chunk   TEXT NOT NULL            -- the exact text that was embedded
);
```

| Column         | Type    | Nullable | Constraint            | Meaning                                              | Example                                |
|----------------|---------|----------|-----------------------|------------------------------------------------------|----------------------------------------|
| `faiss_id`     | INTEGER | NO       | PK                    | Row index in `vectors.faiss` (0-based, contiguous)   | `42`                                   |
| `record_type`  | TEXT    | NO       | Indexed (composite)   | Which table the record points to                     | `design_rule`                          |
| `record_id`    | INTEGER | NO       | Indexed (composite)   | FK into the corresponding table (not enforced DB-level) | `14`                                |
| `text_chunk`   | TEXT    | NO       | —                     | The exact text that was embedded (for inspection/debugging) | `CH224K USB-C PD 5V/9V/12V Sink Wiring \| ic: CH224K \| rule: Tie CFG1 (pin 5) to GND...` |

**`record_type` cardinality in seed data:**
- `topology`     : 12 chunks (one per topology)
- `design_rule`  : 53 chunks (one per rule)
- `app_note`     : 14 chunks (one per app note)
- **Total: 79 chunks**

**Why no DB-level FK on `record_id`?** A `record_id` could point into three
different tables depending on `record_type`. SQLite can't express a
polymorphic FK, so we keep the lookup table denormalised and rely on
application-level integrity at build time. If you delete a `design_rule`,
you must also delete its `vector_index` row and rebuild the FAISS index.

---

### 5.6 `schema_meta`

A simple key/value store for build-time metadata. Query this to discover
the embedding dimension, method, and upgrade path without parsing this
README.

```sql
CREATE TABLE schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
```

| Column  | Type | Nullable | Constraint | Meaning                          |
|---------|------|----------|------------|----------------------------------|
| `key`   | TEXT | NO       | PK         | Metadata key                     |
| `value` | TEXT | YES      | —          | Metadata value (string-encoded) |

See [§8.6](#86-all-schema_meta-keys) for the full list of keys stored.

---

## 6. Indexes

Four B-tree indexes for fast lookups. The primary keys on every table create
implicit indexes as well.

```sql
CREATE INDEX idx_topologies_subsystem ON topologies(subsystem);
CREATE INDEX idx_topologies_ic_family ON topologies(ic_family);
CREATE INDEX idx_design_rules_topology ON design_rules(topology_id);
CREATE INDEX idx_vector_record ON vector_index(record_type, record_id);
```

| Index name                    | Table           | Columns                       | Use case                                                            |
|-------------------------------|-----------------|-------------------------------|---------------------------------------------------------------------|
| `idx_topologies_subsystem`    | `topologies`    | `subsystem`                   | Filter topologies by subsystem (`WHERE subsystem = 'Power/Buck'`)   |
| `idx_topologies_ic_family`    | `topologies`    | `ic_family`                   | Filter topologies by IC family (`WHERE ic_family LIKE '%ESP32%'`)   |
| `idx_design_rules_topology`   | `design_rules`  | `topology_id`                 | Fetch all rules for a topology (`JOIN ... ON dr.topology_id = t.id`)|
| `idx_vector_record`           | `vector_index`  | `record_type, record_id`      | Reverse lookup: given a record, find its faiss_id                   |

**Implicit indexes** (created automatically by `PRIMARY KEY` / `UNIQUE`):
- `components.id` (PK), `components.part_number` (UNIQUE)
- `application_notes.id` (PK)
- `topologies.id` (PK)
- `design_rules.id` (PK)
- `vector_index.faiss_id` (PK)
- `schema_meta.key` (PK)

The FAISS index itself is **not** a SQLite index — it's a separate file
(`vectors.faiss`) that must be loaded via `faiss.read_index()`.

---

## 7. Foreign-key graph

```
application_notes (1) ───< topologies (N)        [topologies.app_note_id]
topologies         (1) ───< design_rules (N)      [design_rules.topology_id]

vector_index.record_id  ──►  topologies.id         WHEN record_type='topology'
vector_index.record_id  ──►  design_rules.id       WHEN record_type='design_rule'
vector_index.record_id  ──►  application_notes.id  WHEN record_type='app_note'
                            (application-level, not DB-level FK)
```

**Two enforced FKs:**
1. `topologies.app_note_id` → `application_notes.id`
2. `design_rules.topology_id` → `topologies.id`

**One polymorphic reference (not DB-enforced):**
3. `vector_index.record_id` → one of three tables, depending on `record_type`

**FK enforcement:** requires `PRAGMA foreign_keys = ON;` per connection.
Build script sets this; your application code must too.

---

## 8. Seed data — complete enumeration

Every row that exists in the database after a clean build. Reproducible —
the build script is deterministic.

### 8.1 All 20 components

| id | part_number       | manufacturer         | category         | description                                                            |
|---:|-------------------|----------------------|------------------|------------------------------------------------------------------------|
|  1 | ESP32-WROOM-32    | Espressif            | MCU/Wireless     | Wi-Fi + BT dual-core MCU module, 4 MB flash, 520 KB SRAM               |
|  2 | ESP32-C3-WROOM-02 | Espressif            | MCU/Wireless     | RISC-V single-core Wi-Fi + BLE 5 module                                |
|  3 | STM32F401RET6     | STMicroelectronics   | MCU              | ARM Cortex-M4 @ 84 MHz, 512 KB flash, 96 KB SRAM                       |
|  4 | ATmega328P-AU     | Microchip            | MCU              | 8-bit AVR MCU, 32 KB flash, 2 KB SRAM, TQFP-32                         |
|  5 | nRF52840-QIAA     | Nordic Semi          | MCU/Wireless     | ARM Cortex-M4F + BLE 5.3 + Thread/Zigbee, 1 MB flash                   |
|  6 | TPS54360DDAR      | Texas Instruments    | Power/Buck       | 60 V, 3.5 A step-down DC-DC converter with Eco-mode                    |
|  7 | TPS63031DSKR      | Texas Instruments    | Power/BuckBoost  | 2.4-A buck-boost converter, 1.8-5.5 V in, fixed 3.3 V out              |
|  8 | TPS7A4700RWVR     | Texas Instruments    | Power/LDO        | 36 V, 1 A ultra-low-noise LDO, PSRR > 76 dB                            |
|  9 | LT3042EDC         | Analog Devices       | Power/LDO        | 200 mA ultra-low-noise (0.8 uV RMS) LDO, programmable                  |
| 10 | LTC3119EDHD       | Analog Devices       | Power/BuckBoost  | 2 A, 15 V synchronous buck-boost DC-DC                                 |
| 11 | CH224K            | WCH (Jiangsu Qin)    | Power/USB-PD     | USB PD 3.0 sink controller, 5/9/12/15/20 V, SOP-8                      |
| 12 | CYPD3177-35LQXQ    | Infineon (Cypress)   | Power/USB-PD     | USB-C PD 3.0 controller with integrated CC line                        |
| 13 | CP2102N           | Silicon Labs         | Interface/USB    | USB-to-UART bridge, up to 3 Mbps, QFN-24                               |
| 14 | TJA1042GTK/3       | NXP                  | Interface/CAN    | High-speed CAN transceiver, 5 V, standby mode                          |
| 15 | MCP2562-E/SN       | Microchip            | Interface/CAN    | High-speed CAN transceiver with VIO pin                                |
| 16 | ADS1115IDGST       | Texas Instruments    | Analog/ADC       | 16-bit 860 SPS I2C ADC with PGA, 4 channels                            |
| 17 | INA219BIDCNR       | Texas Instruments    | Analog/Sensor    | 12-bit I2C current/voltage monitor, bi-directional                     |
| 18 | BME280             | Bosch                | Sensor/Env       | Combined humidity, pressure, temperature sensor, I2C/SPI               |
| 19 | MP1584EN           | MONOPOWER            | Power/Buck       | 3 A, 1.5 MHz step-down converter, 4.5-28 V in                          |
| 20 | AP2112K-3.3        | BCD Semiconductor    | Power/LDO        | 600 mA low-dropout regulator, fixed 3.3 V, SOT-23-5                    |

Datasheet URLs are stored in `components.datasheet_url` (omitted from this
table for readability — query the DB to retrieve them).

---

### 8.2 All 14 application notes

| id | doc_id                | title                                                        | manufacturer         | category       |
|---:|-----------------------|--------------------------------------------------------------|----------------------|----------------|
|  1 | ESP32 HW Design Guide | ESP32 Hardware Design Guidelines                             | Espressif            | MCU/Boot/Power |
|  2 | AN2867                | Oscillator design guide for STM32 microcontrollers           | STMicroelectronics   | MCU/Clock      |
|  3 | AN4488                | Hardware development for STM32 nucleo-144 boards             | STMicroelectronics   | MCU/Power      |
|  4 | AVR042                | AVR Hardware Design Considerations                           | Microchip            | MCU/Boot/Power |
|  5 | SLVA123               | Using the TPS54360 Buck Converter (Design Guide)             | Texas Instruments    | Power/Buck     |
|  6 | SLVA381               | LDO noise and PSRR measurement                               | Texas Instruments    | Power/LDO      |
|  7 | AN-1369               | LT3042 Application Circuits (Circuits from the Lab)          | Analog Devices       | Power/LDO      |
|  8 | WCH CH224 App Note    | CH224K USB PD Sink Reference Design                          | WCH (Jiangsu Qin)    | Power/USB-PD   |
|  9 | Infineon CCG3PA Ref   | CCG3PA USB-C PD Sink Reference Design (CYPD3177)             | Infineon             | Power/USB-PD   |
| 10 | NXP AN1154            | CAN bus physical layer design with TJA1042                   | NXP                  | Interface/CAN  |
| 11 | TI SBAA189            | ADS1115 wiring and I2C interface guide                       | Texas Instruments    | Analog/ADC     |
| 12 | BST-BME280-DS002      | BME280 I2C/SPI wiring and atmospheric pressure compensation  | Bosch Sensortec      | Sensor/Env     |
| 13 | Adafruit ESP32 Feather| Adafruit ESP32 Feather — open-source schematic reference    | Adafruit             | OpenHW/System  |
| 14 | SparkFun RedBoard     | SparkFun RedBoard — Arduino-compatible open schematic        | SparkFun             | OpenHW/System  |

---

### 8.3 All 12 topologies (with rule counts)

| id | title                                                       | subsystem            | ic_family         | app_note_id | rules |
|---:|-------------------------------------------------------------|----------------------|-------------------|------------:|------:|
|  1 | ESP32-WROOM-32 Boot and Power Requirements                  | MCU/Boot             | ESP32             |           1 |     5 |
|  2 | STM32F4 HSE Crystal Oscillator Circuit                      | MCU/Clock            | STM32F4           |           2 |     4 |
|  3 | ATmega328P Minimum System (Arduino-Compatible Boot)         | MCU/Boot             | AVR               |           4 |     4 |
|  4 | CH224K USB-C PD 5V/9V/12V Sink Wiring                       | Power/USB-PD         | CH224K            |           8 |     5 |
|  5 | TPS54360 24V-to-5V Buck Converter Design                    | Power/Buck           | TPS54360          |           5 |     5 |
|  6 | LT3042 Ultra-Low-Noise 3.3V Analog Rail                     | Power/LDO            | LT3042            |           7 |     5 |
|  7 | CAN Bus Physical Layer with TJA1042 / MCP2562               | Interface/CAN        | TJA1042/MCP2562   |          10 |     5 |
|  8 | ADS1115 I2C 16-bit ADC Input Wiring                         | Analog/ADC           | ADS1115           |          11 |     4 |
|  9 | BME280 Environmental Sensor I2C Wiring                      | Sensor/Env           | BME280            |          12 |     4 |
| 10 | Adafruit ESP32 Feather — LiPo + USB-Serial + ESP32          | OpenHW/System        | ESP32             |          13 |     4 |
| 11 | SparkFun RedBoard — ATmega328P + USB-Serial Reference       | OpenHW/System        | AVR               |          14 |     4 |
| 12 | nRF52840 USB-C PD + BLE Reference Design                    | Power/MCU/Wireless   | nRF52840          |           9 |     4 |

**Tags per topology** (stored as JSON array):
1. `["esp32","mcu","power","boot","strapping"]`
2. `["stm32","hse","crystal","clock","oscillator"]`
3. `["atmega328p","avr","arduino","boot","reset"]`
4. `["ch224k","usb-c","pd","power","sink"]`
5. `["tps54360","buck","ti","power","step-down"]`
6. `["lt3042","adi","ldo","low-noise","analog"]`
7. `["can","can-bus","tja1042","mcp2562","nxp","automotive"]`
8. `["ads1115","ti","adc","i2c","analog"]`
9. `["bme280","bosch","sensor","i2c","humidity","pressure"]`
10. `["adafruit","esp32","lipo","open-hw","feather"]`
11. `["sparkfun","arduino","atmega328p","open-hw"]`
12. `["nrf52840","nordic","ble","usb-c","pd","antenna"]`

---

### 8.4 All 53 design rules (full text + citations)

#### Topology #1 — ESP32-WROOM-32 Boot and Power Requirements

| id | rule_text                                                                                                                | constraint                                              | rationale                                                                                          | source                              |
|---:|--------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------|----------------------------------------------------------------------------------------------------|-------------------------------------|
|  1 | GPIO0 must be pulled HIGH with a 10 kΩ resistor to VDD3.3 for normal boot from SPI flash. Pull LOW to enter download mode. | R = 10 kΩ ±5% to VDD3.3                                 | GPIO0 is the boot-mode strap; floating GPIO0 causes unreliable boot.                               | Espressif HW Design Guide §3.1      |
|  2 | EN pin requires an RC delay circuit: 10 kΩ pull-up to VDD3.3 and 1 µF capacitor to GND, for stable power-on reset.       | R = 10 kΩ, C = 1 µF                                     | RC delay ensures EN rises after VDD3.3 has settled, preventing brown-out reset loops.              | Espressif HW Design Guide §3.2      |
|  3 | VDD3.3 rail requires a bulk 10 µF capacitor near the module pin plus one 0.1 µF and one 1 µF decoupling cap per VDD33 pin. | C_bulk = 10 µF X5R/X7R, C_decouple = 0.1 µF + 1 µF per VDD33 pin | ESP32 current spikes during Wi-Fi TX exceed 500 mA; insufficient decoupling causes brown-out resets. | Espressif HW Design Guide §4.1      |
|  4 | GPIO2 must be left floating or pulled HIGH via 10 kΩ at boot; do not drive LOW at power-on. GPIO12 must be LOW (0 V) at boot to select 3.3 V flash voltage. | R = 10 kΩ on GPIO2; GPIO12 tied to GND or left floating (internal weak pulldown) | These are strapping pins that decide flash voltage and boot mode.                                  | Espressif HW Design Guide §3.3      |
|  5 | Place the ESP32 module at least 15 mm away from metal enclosure walls and keep antenna keep-out area clear of copper pours. | Keep-out zone: 15 mm radius around PCB antenna         | Wi-Fi RF detuning causes range loss and current spikes.                                            | Espressif HW Design Guide §6.2      |

#### Topology #2 — STM32F4 HSE Crystal Oscillator Circuit

| id | rule_text                                                                                                                | constraint                                              | rationale                                                                                          | source                              |
|---:|--------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------|----------------------------------------------------------------------------------------------------|-------------------------------------|
|  6 | Use an 8 MHz fundamental-mode AT-cut crystal with CL = 12 pF to 22 pF, ESR ≤ 100 Ω, and drive level ≥ 100 µW.            | Crystal: 8 MHz, CL=18 pF, ESR ≤ 80 Ω                    | Higher ESR crystals may fail to start or jitter; the STM32 OSC_IN driver is sized for low-ESR AT-cut crystals. | ST AN2867 §3.1                      |
|  7 | Calculate load capacitors as CL1 = CL2 = 2 × (CL − Cstray), where Cstray ≈ 5 pF for a typical 4-layer PCB.               | CL1 = CL2 = 2 × (CL − 5) pF; for CL=18 pF use 22 pF each | Mismatched load caps shift the oscillation frequency and degrade startup margin.                   | ST AN2867 §4.2                      |
|  8 | Route OSC_IN and OSC_OUT as a differential pair, ≤ 10 mm total length, no vias, with a ground guard ring around the crystal pads. | Trace length ≤ 10 mm, no vias, ground guard ring       | Long traces add parasitic capacitance and pick up noise from adjacent high-speed signals.          | ST AN2867 §5.4                      |
|  9 | Do not route any high-speed digital signal (SPI, USB, clock) under or within 3 mm of the crystal.                        | Keep-out ≥ 3 mm from crystal pads                       | Coupled noise on OSC_IN causes clock jitter and PLL lock failures.                                 | ST AN2867 §5.6                      |

#### Topology #3 — ATmega328P Minimum System (Arduino-Compatible Boot)

| id | rule_text                                                                                                                | constraint                                              | rationale                                                                                          | source                              |
|---:|--------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------|----------------------------------------------------------------------------------------------------|-------------------------------------|
| 10 | Pull RESET (pin 29) HIGH to VCC via a 10 kΩ resistor. Add a 0.1 µF cap in series from the USB-serial DTR line to RESET for auto-reset during upload. | R = 10 kΩ to VCC; C = 0.1 µF in series from DTR to RESET | Without the pull-up, RESET floats and the MCU resets randomly. The DTR cap enables auto-reset without pressing a button. | AVR042 §2.1; Arduino Uno R3 schematic |
| 11 | Use a 16 MHz crystal across XTAL1/XTAL2 (pins 7/8) with two 22 pF ceramic caps to GND. Alternatively, use the internal 8 MHz RC oscillator if precision is not required. | Y = 16 MHz, CL1 = CL2 = 22 pF                           | Arduino's timing library assumes 16 MHz; using a different crystal requires re-flashing the bootloader. | AVR042 §4.1                         |
| 12 | Place a 0.1 µF ceramic cap within 5 mm of each VCC/AVCC pin (pins 6, 4, 18, 20 on TQFP-32).                              | C = 0.1 µF X7R per VCC/AVCC pin, located ≤ 5 mm away    | AVR spikes current during switching; insufficient decoupling causes EEPROM corruption.             | AVR042 §6.2                         |
| 13 | Tie AVCC (pin 20) to the same VCC rail through a 100 µH inductor or 10 Ω resistor, with a 0.1 µF cap to GND on the AVCC side. Do not leave AVCC floating. | L = 100 µH (or R = 10 Ω) + C = 0.1 µF on AVCC           | AVCC powers the ADC; without filtering, ADC noise floor rises by 2-3 LSBs.                         | AVR042 §6.3                         |

#### Topology #4 — CH224K USB-C PD 5V/9V/12V Sink Wiring

| id | rule_text                                                                                                                | constraint                                              | rationale                                                                                          | source                              |
|---:|--------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------|----------------------------------------------------------------------------------------------------|-------------------------------------|
| 14 | Tie CFG1 (pin 5) to GND for default 5 V request. For 9 V, tie CFG1 to GND and CFG2 (pin 6) to GND. For 12 V, leave CFG1 floating and tie CFG2 to GND. For 20 V, tie both CFG1 and CFG2 to GND through 10 kΩ. | CFG1/CFG2 strapping per voltage table                   | CFG pins are sampled at PD negotiation start; wrong strapping requests the wrong voltage.          | WCH CH224 App Note §2.1             |
| 15 | Place a 22 µF ceramic cap (X5R or X7R, 25 V rating) within 5 mm of VBUS pin to absorb PD voltage transients during contract negotiation. | C = 22 µF, 25 V X5R                                    | PD source may overshoot by 0.5 V during contract switch; under-rated or distant cap causes CH224K reset. | WCH CH224 App Note §3.2             |
| 16 | Add a 1 kΩ series resistor on each CC1/CC2 line to limit ESD current. Do not add capacitors — they corrupt BMC signalling. | R = 1 kΩ series on CC1/CC2                              | Direct CC pin exposure to USB-C connector without series resistance is the leading cause of CH224K failure during hot-plug. | WCH CH224 App Note §3.4             |
| 17 | Tie PG (pin 7) to GND via 10 kΩ pull-down for active-low power-good indication, or leave floating if unused.            | R = 10 kΩ pull-down on PG                               | PG is open-drain; floating PG gives random readings on downstream logic.                           | WCH CH224 App Note §2.3             |
| 18 | VBUS must be the highest-priority signal in layout: short, wide trace (≥ 30 mil) to the downstream load, with the 22 µF cap as close to the connector as possible. | VBUS trace ≥ 30 mil, length ≤ 25 mm                     | Long VBUS traces add inductance that causes voltage spikes during load steps, triggering CH224K OVP. | WCH CH224 App Note §4.1             |

#### Topology #5 — TPS54360 24V-to-5V Buck Converter Design

| id | rule_text                                                                                                                | constraint                                              | rationale                                                                                          | source                              |
|---:|--------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------|----------------------------------------------------------------------------------------------------|-------------------------------------|
| 19 | Choose inductor L = (Vin − Vout) × Vout / (Vin × ΔIL × fSW × Iout). For 24 V→5 V @ 3 A, 400 kHz, ΔIL = 30% × Iout → L ≈ 8 µH. Use a 8.2 µH, 5 A saturation rated part. | L = 8.2 µH, Isat ≥ 5 A, DCR ≤ 30 mΩ                     | Smaller inductors increase ripple and saturate at peak load; larger inductors slow transient response. | TI SLVA777 §2.1                     |
| 20 | Use low-ESR ceramic output caps totalling 44 µF (e.g., 2 × 22 µF X5R 10 V). Add a 0.1 µF MLCC in parallel for high-frequency decoupling. | C_out = 44 µF ceramic + 0.1 µF MLCC                     | High ESR output caps cause loop instability and output ripple > 50 mV.                              | TI SLVA777 §2.3                     |
| 21 | Set compensation: Rcomp = 18 kΩ, Ccomp = 220 nF, CHF = 22 pF. Use the calculated values from SLVA777 §3 for loop crossover at 1/10 of fSW. | Rcomp = 18 kΩ, Ccomp = 220 nF, CHF = 22 pF              | Default compensation values cause sub-harmonic oscillation with low-ESR ceramic output caps.       | TI SLVA777 §3.2                     |
| 22 | Place the input bulk cap (≥ 10 µF ceramic + 47 µF electrolytic) within 5 mm of the VIN pin to minimise loop inductance.   | C_in = 10 µF MLCC + 47 µF electrolytic at VIN           | Insufficient input decoupling causes MOSFET drain ringing > 80 V, risking avalanche breakdown.      | TI SLVA777 §4.1                     |
| 23 | Set EN pin divider so the converter starts at Vin ≥ 7 V: Rtop = 200 kΩ to Vin, Rbottom = 24 kΩ to GND.                   | Rtop = 200 kΩ, Rbottom = 24 kΩ on EN divider            | Without EN hysteresis, slow Vin ramp causes burst-mode chatter at startup.                          | TI SLVA777 §5                       |

#### Topology #6 — LT3042 Ultra-Low-Noise 3.3V Analog Rail

| id | rule_text                                                                                                                | constraint                                              | rationale                                                                                          | source                              |
|---:|--------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------|----------------------------------------------------------------------------------------------------|-------------------------------------|
| 24 | Set output voltage via RSET: Vout = 100 µA × RSET. For 3.3 V → RSET = 33 kΩ (use 0.1% tolerance).                        | RSET = 33 kΩ 0.1% (Vout = 3.3 V)                        | The LT3042 uses a precision current source; RSET tolerance directly becomes Vout tolerance.         | ADI LT3042 datasheet §7; AN-1369    |
| 25 | Use a 10 µF ceramic output cap (X7R, ESR < 5 mΩ) for stability. Do not exceed 47 µF total or the regulator may fail to start. | C_out = 10 µF X7R 6.3 V, ESR ≤ 5 mΩ                     | LT3042 is stable only with low-ESR MLCC output caps; aluminium electrolytic alone is unstable.      | ADI LT3042 datasheet §7.2           |
| 26 | Place a 0.1 µF film or C0G cap directly at the load (≤ 5 mm) for high-frequency noise bypass; the 10 µF bulk handles low frequency. | C_bypass = 0.1 µF C0G at the load                       | MLCCs lose capacitance with DC bias; the 0.1 µF film cap ensures > 1 MHz noise floor stays at 1 nV/√Hz. | AN-1369 §4                          |
| 27 | Use a 4-terminal Kelvin sense at the load: route SENSE+ and SENSE- directly to the load pads, not the regulator output.   | Kelvin sense traces ≤ 25 mm, routed as differential pair | Without Kelvin sensing, PCB trace resistance (1-2 mΩ/mm) corrupts the 0.8 µV RMS noise floor.       | AN-1369 §5                          |
| 28 | Star-ground the LT3042 GND pin to the load ground; do not share a ground via with the input cap.                         | Star ground at load, separate GND via for input cap     | Shared return paths inject input ripple into the output ground reference.                           | AN-1369 §6                          |

#### Topology #7 — CAN Bus Physical Layer with TJA1042 / MCP2562

| id | rule_text                                                                                                                | constraint                                              | rationale                                                                                          | source                              |
|---:|--------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------|----------------------------------------------------------------------------------------------------|-------------------------------------|
| 29 | Place a 120 Ω 1% termination resistor at EACH physical end of the bus. Do not terminate intermediate nodes.              | R_term = 120 Ω 1% at both ends (split: 2 × 60 Ω + 4.7 nF) | Missing termination causes signal reflection and bit errors; double termination loads the bus and reduces noise margin. | NXP AN1154 §2.1                     |
| 30 | Use a split termination: two 60 Ω resistors in series with a 4.7 nF cap to GND at the midpoint. This shunts common-mode noise. | Split term: 60 Ω + 60 Ω + 4.7 nF to GND                 | Single 120 Ω termination is acceptable but does nothing for common-mode noise from ground shifts.  | NXP AN1154 §2.3                     |
| 31 | Keep CANH/CANL stub length to any non-terminating node ≤ 300 mm at 1 Mbit/s. Use twisted-pair or closely-routed differential traces (50 mil pitch). | Stub ≤ 300 mm; CANH-CANL pair routed ≤ 50 mil apart     | Long stubs cause reflections; untwisted pairs pick up common-mode noise.                            | NXP AN1154 §3.2                     |
| 32 | Add a common-mode choke (e.g., Würth 744230) on CANH/CANL near the transceiver for automotive designs to suppress EMC emissions. | CMC: 100 µH @ 100 kHz, ≥ 100 mA                         | Without a CMC, CAN emissions fail CISPR 25 Class 4 in automotive designs.                           | NXP AN1154 §4.1                     |
| 33 | Place an ESD protection diode (e.g., NXP PESD1CAN) within 25 mm of the CAN connector pins, with a short trace to chassis ground. | ESD diode ≤ 25 mm from connector, to chassis ground     | ESD events at the connector reach 15 kV; transceiver pins are rated only to ±8 kV HBM.              | NXP AN1154 §4.3                     |

#### Topology #8 — ADS1115 I2C 16-bit ADC Input Wiring

| id | rule_text                                                                                                                | constraint                                              | rationale                                                                                          | source                              |
|---:|--------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------|----------------------------------------------------------------------------------------------------|-------------------------------------|
| 34 | Add an RC anti-alias filter at each input: 1 kΩ series + 10 nF to GND, giving a 16 kHz cutoff.                           | R = 1 kΩ series, C = 10 nF to GND per input             | Without an input filter, sampling aliases out-of-band noise into the baseband signal.               | TI SBAA189 §2.1                     |
| 35 | Place a 0.1 µF ceramic cap within 5 mm of VDD pin and a 10 µF bulk cap on the same rail.                                 | C = 0.1 µF + 10 µF at VDD                               | ADC reference noise directly modulates measurements; insufficient decoupling costs 1-2 bits of ENOB. | TI SBAA189 §3.1                     |
| 36 | Size I2C pull-up resistors based on bus capacitance: R = (VDD − 0.4 V) / 3 mA. For 100 pF bus at 3.3 V → R = 1 kΩ. For 400 pF bus → R = 4.7 kΩ. | R_pullup = 1 kΩ to 4.7 kΩ depending on bus capacitance  | Oversized pull-ups cause slow rise times and clock stretching; undersized pull-ups exceed the 3 mA sink spec. | TI SBAA189 §4.2                     |
| 37 | For differential measurements, connect AIN0+ and AIN1− as a twisted pair or routed ≤ 50 mil apart; do not route near digital signals. | Differential pair routing, no digital crossings         | Differential mode rejects common-mode noise only if the inputs see identical parasitics.            | TI SBAA189 §5                       |

#### Topology #9 — BME280 Environmental Sensor I2C Wiring

| id | rule_text                                                                                                                | constraint                                              | rationale                                                                                          | source                              |
|---:|--------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------|----------------------------------------------------------------------------------------------------|-------------------------------------|
| 38 | Decouple VDD with a 100 nF ceramic cap within 5 mm of the VDD pin and a 10 µF bulk cap on the rail.                      | C = 100 nF + 10 µF at VDD                               | BME280 has high current spikes (~1 mA for 1 ms) during conversion; insufficient decoupling corrupts the ADC. | Bosch BME280 datasheet §1.4         |
| 39 | Pull SDO to GND for I2C address 0x76, or to VDD for address 0x77. Do not leave SDO floating.                             | SDO tied to GND (0x76) or VDD (0x77)                    | Floating SDO causes random address changes during power-up, breaking I2C enumeration.               | Bosch BME280 datasheet §6.2         |
| 40 | Provide a vent hole ≥ 1 mm diameter in the enclosure above the sensor, with a hydrophobic membrane (e.g., Gore-Tex) for outdoor use. | Vent hole ≥ 1 mm, hydrophobic membrane                  | Without ventilation, humidity readings drift by 5-10% RH due to trapped moisture.                   | Bosch BME280 datasheet §7.3         |
| 41 | Keep the sensor at least 5 mm away from heat-generating components (regulators, MCUs > 100 MHz).                         | Keep-out ≥ 5 mm from heat sources                       | Onboard heat raises the temperature reading by 2-5°C, which biases the humidity compensation algorithm. | Bosch BME280 datasheet §7.4         |

#### Topology #10 — Adafruit ESP32 Feather — LiPo + USB-Serial + ESP32

| id | rule_text                                                                                                                | constraint                                              | rationale                                                                                          | source                              |
|---:|--------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------|----------------------------------------------------------------------------------------------------|-------------------------------------|
| 42 | Use MCP73831T-2ACI/OT for LiPo charging at 500 mA. Tie PROG pin to GND via a 2 kΩ resistor (IPROG = 1000 V / RPROG).      | MCP73831, RPROG = 2 kΩ (500 mA)                         | Charge rate > 1C damages the cell; 500 mA is safe for any ≥ 500 mAh LiPo.                           | Adafruit ESP32 Feather schematic    |
| 43 | Add a P-channel MOSFET (e.g., AO3401) power-path switch: USB VBUS powers the 3.3 V LDO when present, battery takes over when USB is unplugged. | P-MOSFET AO3401 + 2 × 10 kΩ gate divider                | Without power-path management, the charger back-drives USB VBUS or the battery discharges through USB. | Adafruit ESP32 Feather schematic    |
| 44 | Use a AP2112K-3.3 LDO (600 mA) for the 3.3 V rail. Do not use a smaller LDO — ESP32 Wi-Fi TX peaks at 500 mA.            | AP2112K-3.3, Iout ≥ 600 mA                              | Underrated LDOs brown-out the ESP32 during Wi-Fi TX bursts.                                         | Adafruit ESP32 Feather schematic    |
| 45 | Add a 1 kΩ series resistor on the CP2104 TXD line to the ESP32 RXD0 to dampen ringing on long traces.                    | R = 1 kΩ series on USB-serial TXD                       | Long traces between USB-serial and ESP32 ring at 1 Mbaud, causing programming failures.             | Adafruit ESP32 Feather schematic    |

#### Topology #11 — SparkFun RedBoard — ATmega328P + USB-Serial Reference

| id | rule_text                                                                                                                | constraint                                              | rationale                                                                                          | source                              |
|---:|--------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------|----------------------------------------------------------------------------------------------------|-------------------------------------|
| 46 | Use an ATmega16U2 (or CH340 for cost-reduced designs) as the USB-serial bridge. Tie its DTR output via a 0.1 µF cap to the ATmega328P RESET pin. | C = 0.1 µF from DTR to RESET                            | The DTR-to-RESET cap is what makes Arduino auto-reset before upload work; without it, the user must press RESET manually. | SparkFun RedBoard schematic         |
| 47 | Place a 1 kΩ series resistor on the ATmega16U2 RXD/TXD lines to the ATmega328P TXD/RXD; this prevents contention if both ends drive the line. | R = 1 kΩ series on RXD/TXD                              | Without series resistance, a programming mistake on either MCU shorts the other's output, drawing 50+ mA continuously. | SparkFun RedBoard schematic         |
| 48 | Add an ICSP 2x3 header connected to SCK/MISO/MOSI/RESET/VCC/GND for in-system programming without a bootloader.          | ICSP header: SCK, MISO, MOSI, RESET, VCC, GND           | If the bootloader is corrupted, the ICSP header is the only way to recover the board without desoldering. | SparkFun RedBoard schematic         |
| 49 | Use a polyfuse (500 mA, MF-RG500) on the USB VBUS line to protect the host port from board shorts.                       | Polyfuse 500 mA resettable on VBUS                      | Without the polyfuse, a short on the 5 V rail can fry the host USB port.                            | SparkFun RedBoard schematic         |

#### Topology #12 — nRF52840 USB-C PD + BLE Reference Design

| id | rule_text                                                                                                                | constraint                                              | rationale                                                                                          | source                              |
|---:|--------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------|----------------------------------------------------------------------------------------------------|-------------------------------------|
| 50 | Decouple VDD with one 4.7 µF and one 0.1 µF cap per VDD pin (nRF52840 has 4 VDD pins). Add a 100 nF cap on DECUSB.       | 4 × (4.7 µF + 0.1 µF) at VDD, 100 nF at DECUSB         | nRF52840 has tight ripple spec (< 50 mV pp); insufficient decoupling causes BLE packet loss during TX. | Nordic nRF52840 OPS §9.1            |
| 51 | Add a PI matching network on the ANT pin: 0.8 pF series cap + 0.5 pF shunt inductor to GND, tuned to 2.44 GHz.           | C_series = 0.8 pF, L_shunt = 0.5 pF equivalent (3.6 nH) | Default 50 Ω antenna match assumes a tuned PCB trace; without PI tuning, range drops 30-50%.       | Nordic nRF52840 OPS §10.3           |
| 52 | Connect CH224K VBUS to nRF52840 VBUS via a 22 µF ceramic cap and a 5.1 kΩ pull-down on each CC line of the USB-C connector to signal UFP (upstream-facing port). | R = 5.1 kΩ on CC1/CC2 to GND (UFP)                      | Without the 5.1 kΩ pull-down, the USB-C source sees an unknown orientation and refuses to apply VBUS. | Infineon CCG3PA Ref §2.1            |
| 53 | Place a 0.1 µF cap on the nRF52840 DEC4 pin (radio LDO output) within 1 mm; longer traces cause BLE TX current spikes to droop the LDO. | C = 0.1 µF NP0/C0G on DEC4, ≤ 1 mm trace                | DEC4 decouples the internal RF LDO; insufficient decoupling causes spurs in the BLE spectrum.       | Nordic nRF52840 OPS §9.4            |

---

### 8.5 All 79 vector-index chunks

The `vector_index` table contains 79 rows. Each row is one chunk of text that
was embedded into `vectors.faiss`. Breakdown:

| `record_type` | Count | FAISS row range | Source table                  |
|---------------|------:|-----------------|-------------------------------|
| `topology`    |    12 | 0–11            | `topologies.id`               |
| `design_rule` |    53 | 12–64           | `design_rules.id`             |
| `app_note`    |    14 | 65–78           | `application_notes.id`        |
| **Total**     | **79** | 0–78           |                               |

**Chunk construction rules** (the exact text that was embedded):

For **topology** chunks:
```
{title} | subsystem: {subsystem} | ic: {ic_family} | tags: {tags joined by space} | {description}
```

For **design_rule** chunks:
```
{topology.title} | ic: {topology.ic_family} | rule: {rule_text} | constraint: {component_constraint} | rationale: {rationale} | source: {source_reference}
```

For **app_note** chunks:
```
{title} | doc: {doc_id} | manufacturer: {manufacturer} | category: {category} | {description}
```

This format ensures the FAISS embedding sees the IC family + subsystem context
alongside the rule text, so a query like *"how do I wire CH224K for 5V"*
matches both the topology title and the individual rules about CFG pin
strapping.

---

### 8.6 All `schema_meta` keys

| key                  | value                                                                                                                  |
|----------------------|------------------------------------------------------------------------------------------------------------------------|
| `embed_dim`          | `384`                                                                                                                  |
| `embed_method`       | `sklearn.feature_extraction.text.HashingVectorizer`                                                                   |
| `embed_params`       | `{"n_features": 384, "ngram_range": [1, 2], "alternate_sign": false, "norm": "l2", "lowercase": true}`                |
| `faiss_index_type`   | `IndexFlatIP`                                                                                                          |
| `similarity_metric`  | `cosine (via inner product on L2-normalised vectors)`                                                                  |
| `upgrade_path`       | `Rebuild with sentence-transformers/all-MiniLM-L6-v2 for semantic similarity. Vector dim is 384, drop-in compatible.` |
| `schema_version`     | `1.0`                                                                                                                  |
| `build_script`       | `/home/z/my-project/scripts/build_kb.py`                                                                               |

---

## 9. The embedding function

This DB uses a **deterministic, training-free** embedder so you can add new
documents without retraining any model and you can rebuild the index on any
machine without downloading model weights.

**Exact configuration** (also stored in `schema_meta.embed_params`):

```python
from sklearn.feature_extraction.text import HashingVectorizer
import numpy as np

vectorizer = HashingVectorizer(
    n_features=384,           # matches all-MiniLM-L6-v2 (drop-in upgrade path)
    ngram_range=(1, 2),       # unigrams + bigrams
    alternate_sign=False,     # all-positive bag of n-grams
    norm="l2",                # unit length → IP == cosine
    lowercase=True,
    dtype=np.float32,
)

def embed(text: str | list[str]) -> np.ndarray:
    if isinstance(text, str):
        text = [text]
    return vectorizer.transform(text).toarray().astype(np.float32)
```

**Why this and not a transformer?**
- No model download (~80 MB for MiniLM) at build or query time.
- New documents embed with the same function — no partial-fit, no retraining.
- 384 dimensions matches `all-MiniLM-L6-v2`, so when you outgrow hash-based
  similarity you can rebuild the index with MiniLM and the FAISS dimension
  stays the same.

**Limitations** (be honest about them):
- Lexical, not semantic. `"pull-up resistor"` matches `"pull-up resistor"`, but
  won't match `"bias the line high"`. For semantic matching, see §13.
- Hashing collisions are possible but rare at 384 dims with the corpus size here.

---

## 10. Querying — pure SQL, pure vector, hybrid

### 10.1 Pure SQL — fast structured lookups

```python
import sqlite3
conn = sqlite3.connect("knowledge_base.db")
conn.execute("PRAGMA foreign_keys = ON;")   # needed for FK enforcement

# All design rules for a specific IC family
for row in conn.execute("""
    SELECT dr.rule_text, dr.component_constraint, dr.source_reference
    FROM design_rules dr
    JOIN topologies t ON dr.topology_id = t.id
    WHERE t.ic_family LIKE '%ESP32%'
"""):
    print(row)

# Datasheet URL for a part
url = conn.execute(
    "SELECT datasheet_url FROM components WHERE part_number = 'CH224K'"
).fetchone()[0]

# All topologies tagged with 'usb-c'
for row in conn.execute(
    "SELECT title, subsystem FROM topologies WHERE tags LIKE '%usb-c%'"
):
    print(row)
```

### 10.2 Pure vector — semantic search

```python
import faiss
from sklearn.feature_extraction.text import HashingVectorizer
import numpy as np

# Same params as build time
vec = HashingVectorizer(n_features=384, ngram_range=(1, 2),
                        alternate_sign=False, norm="l2",
                        lowercase=True, dtype=np.float32)

index = faiss.read_index("vectors.faiss")
query = "how do I keep ESP32 from browning out during Wi-Fi transmission"
qv = vec.transform([query]).toarray().astype(np.float32)

D, I = index.search(qv, k=5)            # D = cosine scores, I = faiss_ids
for score, fid in zip(D[0], I[0]):
    row = conn.execute(
        "SELECT record_type, record_id, text_chunk FROM vector_index WHERE faiss_id=?",
        (int(fid),)
    ).fetchone()
    print(f"{score:.3f}  {row[0]:12s}  id={row[1]:3d}  {row[2][:100]}...")
```

### 10.3 Hybrid — SQL filter, then vector rank  ⭐ recommended

This is the pattern your LangGraph agent should use: pre-filter to the relevant
IC family in SQL, then rank the survivors by semantic similarity. It avoids
false positives like matching `ESP32` rules against an `STM32` query just
because both talk about decoupling.

```python
def hybrid_search(conn, index, vec, query, ic_family=None, k=5):
    # 1. Get candidate faiss_ids via SQL filter
    if ic_family:
        candidates = conn.execute("""
            SELECT v.faiss_id
            FROM vector_index v
            JOIN design_rules dr ON v.record_type='design_rule' AND v.record_id=dr.id
            JOIN topologies t ON dr.topology_id = t.id
            WHERE t.ic_family LIKE ?
            UNION
            SELECT v.faiss_id
            FROM vector_index v
            JOIN topologies t ON v.record_type='topology' AND v.record_id=t.id
            WHERE t.ic_family LIKE ?
        """, (f"%{ic_family}%", f"%{ic_family}%")).fetchall()
        candidate_ids = {r[0] for r in candidates}
    else:
        candidate_ids = None  # search whole index

    # 2. Embed the query
    qv = vec.transform([query]).toarray().astype(np.float32)

    # 3. Vector search (flat index has no pre-filter, so search all then filter)
    D, I = index.search(qv, k=20)

    # 4. Apply SQL filter post-hoc, take top-k
    results = []
    for score, fid in zip(D[0], I[0]):
        if candidate_ids is not None and int(fid) not in candidate_ids:
            continue
        row = conn.execute(
            "SELECT record_type, record_id, text_chunk FROM vector_index WHERE faiss_id=?",
            (int(fid),)
        ).fetchone()
        results.append((float(score), row))
        if len(results) >= k:
            break
    return results

for score, row in hybrid_search(conn, index, vec,
                                "what value capacitor on VBUS for USB-C PD?",
                                ic_family="CH224K", k=3):
    print(f"{score:.3f}  {row[0]:12s}  {row[2][:120]}...")
```

### 10.4 Working example output

For the query `"how to wire CH224K for 5V USB-C PD"`, the top results are:

| Rank | Score | Type          | Record ID | Snippet                                                          |
|------|-------|---------------|-----------|------------------------------------------------------------------|
| 1    | 0.51  | topology      | 4         | CH224K USB-C PD 5V/9V/12V Sink Wiring                            |
| 2    | 0.41  | app_note      | 9         | Infineon CCG3PA USB-C PD reference                              |
| 3    | 0.33  | design_rule   | 14        | CFG1/CFG2 strapping per voltage table                            |
| 4    | 0.32  | app_note      | 8         | WCH CH224K USB PD Sink Reference Design                          |
| 5    | 0.30  | design_rule   | 15        | 22 µF ceramic cap on VBUS, 25 V rating                           |

For `"ESP32 boot pin pull-up resistor value"`:

| Rank | Score | Type          | Record ID | Snippet                                                          |
|------|-------|---------------|-----------|------------------------------------------------------------------|
| 1    | 0.33  | design_rule   | 42        | Adafruit ESP32 Feather — MCP73831 LiPo charging rule             |
| 2    | 0.26  | topology      | 1         | ESP32-WROOM-32 Boot and Power Requirements                       |
| 3    | 0.25  | design_rule   | 1         | GPIO0 pulled HIGH with 10 kΩ for boot from SPI flash             |
| 4    | 0.25  | design_rule   | 11        | ATmega328P 16 MHz crystal wiring                                 |
| 5    | 0.24  | design_rule   | 3         | VDD3.3 bulk 10 µF + 0.1 µF + 1 µF decoupling                     |

Each rule carries the exact component constraint (`C = 22 µF, 25 V X5R`) and
the citation (`WCH CH224 App Note §3.2`) so the agent can answer with a single
retrieved value instead of guessing.

---

## 11. LangGraph integration

Insert a **Topology Research** node between Component Selection and Netlist
Generation. The node:

1. Takes the selected components from the previous node.
2. For each component, runs a hybrid search against this KB.
3. Concatenates the retrieved rules into a single context string.
4. Passes that context to the Netlist Generation node, with a system prompt
   instructing the LLM to **follow the retrieved rules verbatim and emit a
   `source_reference` comment for each constraint it applies**.

### Reference LangGraph node

```python
from typing import TypedDict, List
from langgraph.graph import StateGraph
import sqlite3, faiss
import numpy as np
from sklearn.feature_extraction.text import HashingVectorizer

# --- one-time setup ---
DB_CONN = sqlite3.connect("knowledge_base.db", check_same_thread=False)
DB_CONN.execute("PRAGMA foreign_keys = ON;")
FAISS_INDEX = faiss.read_index("vectors.faiss")
VECTORIZER = HashingVectorizer(n_features=384, ngram_range=(1, 2),
                               alternate_sign=False, norm="l2",
                               lowercase=True, dtype=np.float32)

class AgentState(TypedDict):
    user_request: str
    selected_components: List[dict]   # from Component RAG node
    retrieved_rules: List[dict]       # NEW: from Topology Research node
    netlist: str                       # from Netlist Generation node

def topology_research_node(state: AgentState) -> AgentState:
    """Retrieve manufacturer-grade design rules for each selected component."""
    rules = []
    for comp in state["selected_components"]:
        ic_family = comp.get("ic_family") or comp.get("part_number", "")
        query = f"how to wire {comp['part_number']} {comp.get('application','')}"
        qv = VECTORIZER.transform([query]).toarray().astype(np.float32)
        D, I = FAISS_INDEX.search(qv, k=8)

        for score, fid in zip(D[0], I[0]):
            if score < 0.15:           # similarity floor
                continue
            vrow = DB_CONN.execute(
                "SELECT record_type, record_id FROM vector_index WHERE faiss_id=?",
                (int(fid),)
            ).fetchone()
            if vrow[0] != "design_rule":
                continue
            dr = DB_CONN.execute(
                """SELECT rule_text, component_constraint, rationale, source_reference
                   FROM design_rules WHERE id=?""", (vrow[1],)
            ).fetchone()
            rules.append({
                "component": comp["part_number"],
                "rule": dr[0],
                "constraint": dr[1],
                "rationale": dr[2],
                "source": dr[3],
                "score": float(score),
            })
    state["retrieved_rules"] = rules
    return state

# --- wire it into the graph ---
graph = StateGraph(AgentState)
graph.add_node("decompose",      decompose_node)
graph.add_node("component_rag",  component_rag_node)
graph.add_node("topology_research", topology_research_node)   # NEW
graph.add_node("netlist_gen",    netlist_gen_node)

graph.add_edge("decompose",         "component_rag")
graph.add_edge("component_rag",     "topology_research")     # NEW
graph.add_edge("topology_research", "netlist_gen")           # NEW
```

### Netlist Generation prompt (excerpt)

> *You are a hardware design agent. Below are manufacturer-verified design
> rules retrieved from a trusted knowledge base. When generating the netlist:*
> 1. *Follow each rule verbatim. Do not invent component values.*
> 2. *For every resistor/capacitor/inductor you add, include a `# source:`
>    comment with the rule's `source_reference`.*
> 3. *If no rule covers a needed constraint, emit `# UNCITED` and skip adding
>    the component rather than guessing.*
>
> *Retrieved rules:*
> ```
> [{rule for component X}, {rule for component Y}, ...]
> ```

This is what makes the agent's output **explainable**: every resistor on the
schematic carries a comment like `# source: TI SLVA777 §2.3`.

---

## 12. Extending the knowledge base

### 12.1 Add a new design rule (requires FAISS rebuild)

You can append rules directly to the running DB. The catch: the new rule won't
be searchable via FAISS until you rebuild the index. For small additions
(< 50 rules) this is fine — re-run the build script.

### 12.2 Add a new topology + rules + FAISS update

```python
import sqlite3, faiss, numpy as np
from sklearn.feature_extraction.text import HashingVectorizer

conn = sqlite3.connect("knowledge_base.db")
conn.execute("PRAGMA foreign_keys = ON;")
vec = HashingVectorizer(n_features=384, ngram_range=(1, 2),
                        alternate_sign=False, norm="l2",
                        lowercase=True, dtype=np.float32)

# 1. Insert topology
cur = conn.execute(
    "INSERT INTO topologies (title, subsystem, ic_family, description, tags) "
    "VALUES (?,?,?,?,?)",
    ("TPS63031 Buck-Boost 3.3V Rail",
     "Power/BuckBoost", "TPS63031",
     "Single-cell LiPo to 3.3 V @ 2 A buck-boost",
     '["tps63031","buck-boost","lipe","power"]')
)
topo_id = cur.lastrowid

# 2. Insert design rules
rules = [
    ("Use 2.2 µH inductor with Isat ≥ 3 A, DCR ≤ 30 mΩ.",
     "L = 2.2 µH, Isat ≥ 3 A",
     "Smaller L increases ripple; larger L slows transient.",
     "TI TPS63031 datasheet §8.2.1"),
    # ... more rules
]
for rule_text, constraint, rationale, source in rules:
    conn.execute(
        "INSERT INTO design_rules (topology_id, rule_text, component_constraint, rationale, source_reference) "
        "VALUES (?,?,?,?,?)", (topo_id, rule_text, constraint, rationale, source))
conn.commit()

# 3. Rebuild FAISS index from scratch
#    (see /home/z/my-project/scripts/build_kb.py for the canonical rebuild path)
```

For a full rebuild, edit the seed-data lists in the generator script and
re-run it. The script is idempotent — it drops all tables and rebuilds from
scratch.

### 12.3 Ingest a real PDF app note

For production, you would:
1. Download the PDF (e.g. TI `SLVA777`).
2. Extract text per section using `pdfplumber` or `pypdf`.
3. Chunk by section header (not by character count — engineering rules don't
   respect character boundaries).
4. For each chunk, ask an LLM to extract `(rule_text, component_constraint,
   rationale)` triples — this is the only step where an LLM is involved.
5. Insert as new `topologies` + `design_rules` rows, then rebuild the FAISS index.

A reference extraction prompt:

> *Extract every imperative engineering rule from the following text. For each
> rule, return JSON with: `rule_text` (imperative sentence), `constraint`
> (exact component value/tolerance), `rationale` (why), `source_reference`
> (doc ID + section).*

---

## 13. Upgrade path to semantic embeddings

When hash-based similarity isn't good enough (and it won't be once you cross
~500 rules), swap in `sentence-transformers/all-MiniLM-L6-v2`:

```python
# pip install sentence-transformers
from sentence_transformers import SentenceTransformer
model = SentenceTransformer("all-MiniLM-L6-v2")   # 384-dim, ~80 MB

def embed(texts):
    return model.encode(texts, normalize_embeddings=True).astype(np.float32)
```

The dimensionality is the same (384), so the FAISS index type doesn't change —
just rebuild `vectors.faiss` with the new embedder. Update `schema_meta` so
downstream code knows which embedder to use at query time:

```sql
UPDATE schema_meta SET value='sentence-transformers/all-MiniLM-L6-v2'
WHERE key='embed_method';
```

For higher-quality retrieval on multi-lingual datasheets (ST documents are
often in EN/FR/ZH), swap to `paraphrase-multilingual-MiniLM-L12-v2` (384-dim).

---

## 14. Runtime dependencies

```
numpy        >= 1.25
scikit-learn >= 1.3       # HashingVectorizer
faiss-cpu    >= 1.7       # vector index
sqlite3                  # stdlib, no install needed
```

For semantic upgrade (optional): `sentence-transformers >= 2.2`.

No GPU required. The 79-chunk index fits in 130 KB on disk and returns results
in < 1 ms.

---

## 15. Limitations & disclaimers

- **Seed data is hand-curated summaries**, not the original PDFs. Always
  cross-check critical rules against the cited `source_url` before taping out.
- **Hash-based embedding** is lexical. It will miss semantically equivalent
  phrasings. Upgrade per §13 if this becomes a problem.
- **No automatic PDF ingestion.** Adding new app notes is a manual curation
  step. See §12.3 for the recommended workflow.
- **English only.** The embedder lowercases but does not stem; non-English
  queries will match poorly. Use a multilingual sentence-transformer (§13) for
  non-English datasheets.
- **Not a parts catalogue.** The `components` table lists 20 representative
  parts for cross-referencing. For exhaustive part selection, pair this KB with
  your existing Component RAG (DigiKey / Mouser / Octopart API).

---

## 16. Citation policy

When this knowledge base contributes to a design, cite the original
manufacturer document — *not* this database. The `source_reference` field on
each `design_rules` row carries the exact citation to surface in the agent's
output.
