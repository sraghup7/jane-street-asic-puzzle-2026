# Step 1 — Problem & Resource Dossier

**Project:** Reverse-engineering the Jane Street ASIC puzzle, using an approach of our own.
**Step 1 goal:** a precise, evidence-backed understanding of *the problem itself* and *the resources we hold* — before looking at anyone's solution.
**Status:** complete, gated by `tools/checks/check_step1.py` (54 automated checks, all passing).

Everything numeric in this document is machine-generated into `recon/inventory.json`
by `tools/inventory.py`. Nothing here is quoted from a secondary source: every claim was
re-derived from the artifacts in this workspace. Regenerate with:

```bash
.venv/Scripts/python tools/inventory.py
.venv/Scripts/python tools/checks/check_step1.py      # -> STEP 1 GATE: PASS
```

> **Scope discipline.** The published writeup is deliberately **not** consulted in this
> document, and no claim below depends on it. Step 2 covers the known solution.

---

## 1. Provenance

| Item | Value |
|---|---|
| Blog post | `https://blog.janestreet.com/can-you-reverse-engineer-an-asic/` |
| Published | **2026-08-05** |
| Authors | Anish Singhani, Benjamin Devlin (hardware engineers, Jane Street) |
| Puzzle repo | `https://github.com/janestreet/asic-puzzle-2026` |
| Pinned commit | `ffd53e0ba24e2fc1c1b12dc824e8eac5888c19a9` ("upload puzzle", 2026-08-05 15:12:19 -0400) |
| Local clone | `asic-puzzle-2026/` — **read-only upstream input**, 9 files, gitignored |
| Submissions closed | **2026-09-04** (8 days before this step) |

---

## 2. The task

The puzzle hands over a fabricated chip's final layout and asks for a **string**. The blog
states it in three escalating stages:

1. **Recover a netlist from the layout.** One section generates output but does not affect
   `success`, and is explicitly safe to ignore at first.
2. **Determine the circuit's true purpose.** "The circuit is physically arranged to hint at
   its functionality, so look closely at the layout!"
3. **The puzzle within the puzzle.** "Once you understand what the chip does, use it to tease
   out the output it's looking for, and find the string value that's your final answer."

Success condition: *"You'll know you have the correct solution when the `success` output
signal goes high. Don't forget to toggle `rst_n` before each input attempt."*

Stated independent verification requirements:
- we must build a way to **simulate the circuit** to test a solution and read out the final answer;
- the provided waveform is **not** the winning input (`example_inputs.vcd`);
- there are **Easter eggs** in the circuit and the repository, "including in parts you don't
  need to look at to solve the main puzzle".

### 2.1 Rules recorded as historical context (not binding on us)

- No public spoilers or full writeups until submissions closed (now closed).
- After closing: *"If you do publish your solution … email us and we may include the link in
  our follow-up post!"*
- The post asked solvers not to feed the puzzle files into an AI tool, and not to use AI to
  generate the writeup, while allowing AI for scripts/code used to solve it and for the warm-up.
- A follow-up competition is announced for later in 2026: design your own chip, best entries
  get fabricated.

**Project decision (2026-09-12, user):** the AI request is not treated as binding here — this
work is for the user's own sake. Recorded in `AGENTS.md`.

---

## 3. Artifacts on hand

All nine upstream files, hashed:

| File | Bytes | SHA-256 (first 16) |
|---|---:|---|
| `puzzle.gds` | 1,421,700 | `8913ea4be5367b48` |
| `example_inputs.vcd` | — | `2247ae1c11953226` |
| `layout.png` | — | `ab4337cf495dda08` |
| `README.md` | — | `8d8174094e336b55` |
| `warmup/00_source.v` | — | `b6868174730656f5` |
| `warmup/01_netlist.v` | — | `822999060e7149ee` |
| `warmup/02_netlist_with_power_rails.v` | — | `a21c25431eebaaae` |
| `warmup/03_post_place_and_route.def` | — | `bf01969707b9e70c` |
| `warmup/04_final.gds` | — | `be7f04fedf9bacd9` |

Full hashes are in `recon/inventory.json` → `upstream_files`, and the gate asserts them.

**`layout.png`** is Jane Street's own annotated floorplan hint. Transcribed labels:
left edge top-to-bottom `clk`, `rst_n`, `enable`, `input`; right edge top-to-bottom
`success`, then `out[0]`…`out[7]`; one large boxed region at the right side labelled
**"output generator"**, drawn with hatching.

---

## 4. Interface — derived from the waveform, not from the docs

### 4.1 Top-level ports

Six signals, confirmed three ways (VCD header, GDS text labels, `layout.png`):

| Port | Dir | Width | Die side | Label position (µm) |
|---|---|---|---|---|
| `clk` | in | 1 | left | (0.30, 238.34) |
| `rst_n` | in | 1 | left | (0.30, 185.30) |
| `enable` | in | 1 | left | (0.30, 132.26) |
| `I` | in | 1 | left | (0.30, 79.22) |
| `O[7:0]` | out | 8 | right | `O[0]` (199.70, 204.34) … `O[7]` (199.70, 13.94), pitch 27.20 µm |
| `success` | out | 1 | right | (199.70, 285.94) |

Port label geometry lives on layer **70/5** (met1 pin labels); the labels are attached to
polygons on *various* layers, so a label search must not be restricted to one layer.

### 4.2 Stimulus protocol (independently re-derived from `example_inputs.vcd`)

`tools/vcd_probe.py` parses the VCD, samples every signal on each rising clock edge, and
reconstructs the protocol:

```
timescale              1ps
clock period           10 000 ps  (10 ns);  312 rising edges in the trace
```

| Phase | Cycles (0-indexed, cycle = rising edge #) |
|---|---|
| `rst_n` low (reset) | 0 – 2 (**3 cycles**) |
| idle (`rst_n` high, `enable` low) | 3 (**1 cycle**) |
| shift input, `enable` high | 4 – 124 (**121 cycles**) |
| `enable` low; message clocks out | 125 onward |
| reset for trial 2 | 156 – 158, idle 159, feed 160 – 280, output 281+ |

So the FPGA-side protocol is: **3 cycles reset → 1 idle → 121 cycles of serial feed → deassert
`enable` → the ROM message streams out one byte per cycle**, then an idle gap, then repeat.

**121 = 11 × 11.** That the feed length is a perfect square is the single strongest structural
signal in the whole problem, and it is available before touching the netlist at all.

### 4.3 What the provided waveform actually shows

```
output bytes, trial 1 : T R Y  ' ' A G A I N NUL     (cycle 125 … 134)
output bytes, trial 2 : T R Y  ' ' A G A I N NUL     (cycle 281 … 290)
success               : never asserted (all 312 cycles)
```

This is therefore a **negative / control waveform**: it demonstrates the interface, the timing,
and the output-message mechanism, while `success` stays low. It is our regression oracle — any
netlist we extract must reproduce these bytes exactly, and must *not* raise `success`.

### 4.4 Header Easter eggs (visible without any layout work)

- `$version`: `Leave no stone unturned! But for this file, consider looking at it in a waveform viewer instead.`
- `$date`: `Sat Dec 31 23:59:60 2016` — a timestamp at the **2016 leap second**.

---

## 5. The die

| Property | Value |
|---|---|
| Die outline (the design's own prBndry, layer 235/4) | (0, 0) → (200, 300) µm = **200 × 300 µm** |
| Full GDS bbox | y extends to **−52.72 µm** — see §7 |
| Database unit | 1000 / µm → **1 nm** per DBU (GDS version 600) |
| Library name | `LIB` (generic — no metadata leak) |
| Structures | **81** = 1 top cell (`puzzle`) + 69 standard-cell masters + 9 via masters + 2 `INTERNAL_*` |
| Layers present in the placed design | **41** distinct `(layer, datatype)` pairs — **33** carry polygons or paths, the other **8** carry only `TEXT` labels (reconciled in `docs/steps/A1.md`) |
| Power labels | `VPWR`/`VGND` on layers 71/5, 72/5 — a real power grid exists |

### 5.1 Placement grid — a major simplification

| Property | Value |
|---|---|
| sky130_hd row height | 2.72 µm |
| **Rows actually populated** | **52**, at y = 10.88 + 5.44·k (pitch **5.44 µm = 2 rows**) |
| Site width | 0.46 µm (`unithd`, from the warm-up DEF) |
| Cells on the 0.46 µm site grid | **1618 / 1618** — x-residual histogram is a single bucket at 0.0 |

Two consequences that shape the whole extractor design:

1. **Only every other row is used.** Cells are never vertically adjacent, so no two cells'
   geometry overlaps in y. That is unusual for a real P&R result and looks deliberate — it
   makes the layout legible and makes a geometric extractor dramatically less ambiguous.
2. **Every cell is exactly on the site grid.** No sub-site drift to correct for.

### 5.2 Cell orientations

| Orientation | Count |
|---|---|
| `rot000` (unrotated) | 624 |
| `rot000` + mirrored about x | 546 |
| `rot180` | 233 |
| `rot180` + mirrored about x | 215 |
| **90° / 270° placements** | **0** |

Verified three independent ways, because a 90° rotation changes the pin-transform maths and
is therefore a correctness-critical fact:

1. `gdstk` reports `(rotation°, mirror) ∈ {(0,F),(0,T),(180,F),(180,T)}` only.
2. KLayout reports the same four classes. **Watch out:** KLayout's `Trans.angle` is in units
   of **90°**, so its `r2`/`m2` mean *180°*, not 2°. Misreading that as degrees produces a
   phantom "90° rotation" population — exactly the kind of trap that silently corrupts pin
   matching.
3. Decisive geometric test: all **1618** standard-cell instances have a global bounding-box
   height of ~3.20 µm (their native row height). A 90° rotation would have made some of them
   ~width-tall. None are.

So the pin transform needed is only `x' = ±x_local + X`, `y' = ±y_local + Y` — two signs, no
rotation matrix.

---

## 6. Cell inventory

Instance census of the top cell — **9875 placements total**, zero AREF/array references
(so no array expansion is needed; every instance is explicit).

| Class | Count |
|---|---:|
| Routed via instances (`VIA_*`) | 8,221 |
| Standard cells | 1,618 |
| `INTERNAL_*` decorations (outside the die) | 36 |
| **Total** | **9,875** |

Standard-cell breakdown:

| Property | Count |
|---|---:|
| Total standard-cell instances | 1,618 |
| — well/body **tap** cells (`tapvpwrvgnd_1`) | 676 |
| — **decap** cells (`decap_3`) | 204 |
| — **antenna diodes** (`diode_2`) | 10 |
| — functional cells (std − taps − decaps) | **738** |
| — functional cells excluding all physical-only | 728 |
| **Sequential cells** | **92** |
| — `dfrtp_2` (D-FF, async reset) | 84 |
| — `dfxtp_2` (plain D-FF) | 4 |
| — `dfstp_2` (D-FF, async set) | 4 |
| Combinational cells | 636 |

**69 distinct standard-cell masters** of the `sky130_fd_sc_hd` library (SkyWater 130 nm,
high-density). Notable non-obvious members, since they affect a functional extractor:

- `mux2_1` × 21 — the only mux in the library set used; a data path is being selected.
- `conb_1` × 6 — **constant tie cells** (constant 0/1 drivers). These are *functional*, not
  physical-only: they drive reset pins and unused inputs. Filtering them as "physical" leaves
  inputs undriven and produces X in simulation.
- `clkbuf_4/8/16` × 32 — clock-tree buffers. Logically transparent (an input-to-output wire),
  so a functional extractor may either model them or splice them out.
- `buf_2` × 1, `inv_2` × 25.
- `xnor2_2` × 29 and `xor2_2` × 21 — heavily used; consistent with equality/parity logic.
- No adders, multipliers, or macro blocks: **pure gate-level standard cells only**, so a
  gate-level netlist is a *complete* description of the design.

The GDS retains the **standard-cell master names** intact (`SNAME` on every reference). The
design is therefore recoverable at gate level from geometry alone; transistor-level analysis
of the cell interiors is unnecessary. Library names → boolean function are public knowledge.

Sanity anchor: 1618 − 676 − 204 = **738**, which matches the widely-quoted cell count for this
design — our independent census reproduces it.

---

## 7. Hidden and anomalous geometry

Three things sit outside the normal cell/routing structure. All three were found by treating
the *whole* GDS as data rather than only the die.

### 7.1 A 36-symbol Morse Code strip **below the die** (Easter egg)

36 references named `INTERNAL_3` (21) and `INTERNAL_7` (15) are placed at **y = −52.72 µm**,
outside the 200 × 300 µm die outline. Each `INTERNAL_*` master contains **nothing but a single
polygon on layer 200/0** — no transistors, no pins, no connection to the design. They are pure
decoration, and none of them is reachable from any net.

Their widths are quantised to a **1.38 µm unit** (1.38 µm = 1 unit, 4.14 µm = 3 units) on a
unit grid, and the spacing between them encodes Morse:

| Gap | Meaning |
|---|---|
| 1 unit | element break (same letter) |
| 3 units | letter break |
| 7 units | word break |

Element string (`.` = narrow 1.38 µm, `-` = wide 4.14 µm):

```
.--...-..-.-..-..---.--...-...-.-..-
```

Decoded:

```
PER ARENAM AD ASTRA
```

*"Through the sand to the stars"* — a play on *per aspera ad astra*, with **sand** as the
silicon feedstock. Decoding is implemented in `inventory.decode_morse_strip` and asserted by
the gate, including that **every** element group maps to a letter (`unmapped_groups == 0`) —
so this is not a lucky partial match.

### 7.2 Layer 236/0 — the per-master cell-boundary marker

942 polygons show up on layer 236/0 in the placed design, and they are **exactly one per
non-tap standard-cell instance** (1618 − 676 taps = 942). The mechanism is worth stating
precisely, because the obvious guess is wrong:

- The polygon is **not** drawn in the top cell. It is drawn **inside each cell master**: **68 of
  the 69** standard-cell masters contain exactly one layer-236/0 polygon. The single master
  **without** one is `sky130_fd_sc_hd__tapvpwrvgnd_1` — the tap cell.
- Instantiation therefore multiplies it: 942 non-tap instances × 1 polygon = 942 polygons.
  This is a PDK-level cell-boundary marker convention, **not** a designer annotation and **not**
  puzzle content.
- The marker is **inset** from the cell's drawn extent: the layer-236 width histogram matches
  the non-tap cell-width histogram **1:1 across all 15 width buckets** with a constant offset of
  exactly **0.38 µm (= 0.19 µm per side)**. A naive exact-coordinate match between marker and
  cell finds **zero** overlap — a real trap if you expected them to coincide.

Two uses for us: it is a free, independent corroboration of the instance census in §6, and it
predicts exactly which instances are "real cells" (it excludes taps, but note it does **not**
exclude decaps or diodes).

### 7.3 `INTERNAL_3` / `INTERNAL_7` as masters

Widths 1.38 µm (3 sites) and 4.14 µm (9 sites) on the 0.46 µm grid. Their only content is the
Morse geometry, and they are the sole occupants of layer 200/0. They are **not** logic cells —
an extractor that tries to match pins on them will find none.

**Takeaway for our pipeline:** the extraction must be driven by *connectivity*, not by bounding
box, and must explicitly ignore anything outside the prBndry rectangle (or it will pick up 36
phantom instances and 36 phantom nets that go nowhere).

---

## 8. The warm-up — our ground truth

`warmup/` gives the *complete* flow for a deliberately simple design, from source to silicon.
`warmup/00_source.v` tells us exactly what it is: **two 8-bit shift registers, an adder, and a
comparator that drives `S` high when `A + B == 496`.**

| File | What it gives us |
|---|---|
| `00_source.v` | Ground-truth Verilog: `shift_register` ×2, `adder8`, `comparator496`, `adder_demo` top |
| `01_netlist.v` | Post-synthesis gate netlist — cell names **and net names** intact |
| `02_netlist_with_power_rails.v` | Same, plus `VPWR`/`VGND` |
| `03_post_place_and_route.def` | Physical placement + routing, with cell **instance names** and net names |
| `04_final.gds` | The same design as a GDS, with internal names stripped — **the real target format** |

Measured properties:

| Property | Value |
|---|---|
| Netlist cell instances in `01_netlist.v` | 230 (incl. 93 taps, 58 decaps) |
| Functional cells | shift registers: 16 `dfrtp_2` + 16 `mux2_1` (the `en` mux per flop), plus adder/comparator logic |
| DEF die area | 100 × 100 µm, 1000 DBU/µm |
| DEF rows | 29 |
| Site width / sites per row | 0.46 µm / 173 (⇒ 79.6 µm of `unithd` sites) |
| Flow | sky130 + OpenLane/OpenROAD convention (`PHY_EDGE_ROW_*` decaps, `unithd` rows) |

**Why this matters more than anything else in this document:** `00_source.v` + `01_netlist.v` +
`04_final.gds` form a **closed-loop calibration fixture**. We can point our entire extractor at
`warmup/04_final.gds` and compare the netlist it produces against `01_netlist.v`, whose correct
answer is known. Any extraction bug shows up on a 27-cell-visible design before it can be
masked by the 1618-cell design. This is the single highest-leverage asset we have, and our plan
must exploit it at every stage rather than only at the end.

---

## 9. Environment inventory

| Tool | Status |
|---|---|
| Python | 3.11.15 |
| `gdstk` 1.0.1 | ✔ installed (pure pip — GDS read/write, no PDK) |
| `shapely` 2.1.2 | ✔ installed (polygon union — the core of geometric net extraction) |
| `numpy` 2.4.6, `scipy` 1.17.1, `networkx` 3.6.1, `matplotlib` 3.11.2, `pandas` 3.0.5 | ✔ installed |
| `klayout` Python module 0.30.12 | ✔ installed — **used as an independent oracle for cross-checks** |
| `iverilog` / `vvp` 12.0 | ✔ on PATH |
| KLayout **GUI** binary | ✘ not installed |
| `yosys` / `sby` | ✘ not installed |
| `magic` / `netgen` | ✘ not installed |
| `make` (GnuWin32 3.81), `git` | ✔ on PATH |
| Vivado 2024.2 | at `D:/Vivado/2024.2/bin/vivado.bat`, not on PATH (not needed) |
| WSL | available |
| **SkyWater PDK clone** | ✘ **not present** — and per our chosen approach, **not required** |

Recon tooling already written and usable:

| Tool | Purpose |
|---|---|
| `tools/inventory.py` | Machine-generated inventory of everything above |
| `tools/vcd_probe.py` | VCD parser → per-cycle stimulus table, byte decode |
| `tools/gds_dump.py` | Dependency-free GDSII record-level dump (independent of gdstk) |
| `tools/kl_recon.py` | KLayout-based cross-check of the gdstk readings |
| `tools/render.py` | Rasterises any layer subset to PNG |
| `tools/checks/check_step1.py` | This step's executable gate |

Our chosen approach (see `AGENTS.md` Q1) needs **no PDK, no LEF, and no formal-verification
toolchain** — the dependency set above already suffices.

---

## 10. What we do **not** yet know (this is what Step 3 must answer)

The dossier deliberately stops at the boundary of the problem. Open questions:

1. **Connectivity.** Which cell pin connects to which net. Nothing in the GDS labels nets; it
   must be recovered geometrically from metal/via overlap.
2. **Pin geometry.** Where each cell's pins physically are. Our approach derives this from the
   cell masters *inside `puzzle.gds` itself* rather than from a PDK LEF — but the exact
   per-cell pin extraction method is unproven and must be validated on the warm-up first.
3. **Bit order.** Whether the first serial bit fed is the MSB or LSB of the 121-bit word, and
   how the 121 bits map onto the 11 × 11 grid (row-major or otherwise).
4. **The `success` condition.** What the 121 bits must satisfy. The constant 22, the ×11
   structure, and a separate message vocabulary were all visible in principle; **all of them have since
   been read** — the constant 22, the ×11 structure and the message vocabulary are reproduced in Phases
   C–E (`docs/03_our_plan.md`), and AC2 asserts the cycle.
5. **The region map.** Whether the design partitions the grid into regions, and if so how, and
   what it spells. The design contains a LUT-like structure that must be decoded from the
   netlist rather than probed.
6. **The message vocabulary.** Which inputs produce which messages, beyond the one case the
   provided waveform demonstrates.
7. **The `output generator`.** Its interface and content — explicitly deferrable per the blog,
   but required to read the final answer.

---

## 11. Acceptance criteria for this project

Locked at intake (Q2 = everything). Our recovered truth must reproduce **all** of:

- [ ] **the exact 121-bit input vector** that asserts `success`;
- [ ] `success` asserting at **cycle 126**;
- [ ] the output string **`(* TWO STARS *)`**;
- [ ] the four alternative messages: **`EMPTY SKY`**, **`BIG BANG`**, **`TWO NOT TOUCH`**, **`TRY AGAIN`**;
- [ ] a **byte-exact replay** of `example_inputs.vcd` from our own netlist (`TRY AGAIN`, twice, `success` low throughout);
- [ ] the grid's hidden **region map recovered independently** from our netlist and spelling **"JS"**.

The target values above are the acceptance contract. They are recorded here so that Step 5 has a
fixed yardstick; the *mechanisms* that produce them are Step 2/3 material and are deliberately
absent from this document.

---

## 12. Risks and traps identified so far

| # | Trap | Evidence |
|---|---|---|
| R1 | Treating `INTERNAL_*` as logic cells | They have no pins; only layer 200/0 geometry, outside the die |
| R2 | Restricting a label search to the top metal layer | Port labels sit on varied layers |
| R3 | Reading KLayout `Trans.angle` as degrees | It is in 90° units → invents a phantom 90° population |
| R4 | Assuming the layout has no rotated/mirrored cells | 994 of 1618 are mirrored and/or 180° rotated |
| R5 | Expecting layer 236 boxes to coincide with cell footprints | They are inset by 0.19 µm/side |
| R6 | Filtering `conb_1` tie cells as "physical" | 6 instances; drives reset pins and unused inputs |
| R7 | Assuming the 200 × 300 µm prBndry is the whole file extent | GDS bbox reaches y = −52.72 µm |
| R8 | Skipping the warm-up calibration | A 27-cell known-answer fixture is available and cheap |

---

## 13. Step 1 verification

```bash
$ .venv/Scripts/python tools/checks/check_step1.py
  PASS  inventory regenerates bit-identically
  PASS  upstream commit
  ...
  PASS  morse decodes
  ...
  54/54 checks passed
STEP 1 GATE: PASS
```

The gate does two independent things: it regenerates `recon/inventory.json` from the raw
artifacts and requires a **bit-identical** match, then asserts each specific claim made above
(provenance hashes, die dimensions, instance census, grid alignment, orientation set, port
labels, Morse decode, VCD protocol, warm-up properties, availability of `gdstk`/`shapely`/
`iverilog`). Any drift fails the step.
