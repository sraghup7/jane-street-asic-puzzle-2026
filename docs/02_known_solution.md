# Step 2 — The Known Solution, Studied

**Step 2 goal:** understand *the published solution* precisely enough to (a) know what has
already been done, (b) know which of its parts are traps we would inherit by copying, and
(c) log every place where it disagrees with our own measurements — because those
disagreements are what will make Step 3 a genuinely different approach rather than a re-skin.

**Scope rule:** this step documents the known solution and marks it. It does **not** design ours.
Design decisions belong to `docs/03_our_plan.md`.

---

## 1. Source and provenance

| Item | Value |
|---|---|
| Title | "Reverse Engineering the Jane Street ASIC Puzzle" |
| Author | Jagadeesh Mummana (independent; not a Jane Street author) |
| Published | **2026-09-07** — three days *after* submissions closed (2026-09-04) |
| URL | `https://mummanajagadeesh.github.io/blogs/janestreet-asic-puzzle/` |
| Retrieved | 2026-09-12 |
| Local snapshot | `recon/sources/known_solution.html` — 80,144 B, sha256 `0e73f73c567a04f8…` |
| Extracted text | `recon/sources/known_solution.txt` — 29,301 B, sha256 `10ae574bf1e885d4…` |

Snapshot is **untracked** (gitignored): we cite it for verification but do not redistribute it.
Regenerate by fetching the URL and running the same HTML→text strip. The gate
`tools/checks/check_step2.py` verifies every quote below against the snapshot when it is
present, and reports `SKIP` for those checks if it is absent.

**Cited solution repo does not exist on the page.** The writeup's own Files section says
*"The extractor, the Verilator harness, the SBY config, the 121 answer bits, and the
four-constraint checker are in the repo linked at the top."* The only GitHub link the page
actually contains is the author's **profile** (`https://github.com/Mummanajagadeesh`). There is
no link to a solution repository. So the implementation is not public — only the prose
description is. That is a real limit on how far "study the known solution" can go, and it means
our Step 3 plan has to stand on the description alone.

---

## 2. Its claim about the target

> "The chip is a two-star **Star Battle** verifier on an **11x11 grid**. It reads **121 bits**
> of serial input, then checks five things: Exactly **22 stars** (ones) in total. Exactly **two
> stars per row**. Exactly **two stars per column**. **No two stars adjacent** in any of the
> eight directions. Exactly **two stars per hidden region**, where the 11 regions spell out
> **"JS"** across the die."

> "When all five conditions hold, `success` asserts on **cycle 126** and the output ROM prints
> **`(* TWO STARS *)`**. The answer string is **TWO STARS**."

Published response table:

| Input | Output |
|---|---|
| All zeros | `EMPTY SKY` |
| All ones | `BIG BANG` |
| 2/row + 2/col but touching | `TWO NOT TOUCH` |
| Other wrong input | `TRY AGAIN` (matches the example VCD) |
| Correct solution | `(* TWO STARS *)` |

### 2.1 We independently verified the stated target — and found an ordering trap

`tools/target.py` re-derives the grid from the published bit vector and checks it against the
published ASCII grid and the four mechanical constraints. Result: **PASS**, all four constraints
hold (22 ones, exactly 2 per row, exactly 2 per column, zero adjacent pairs).

But it does **not** pass under the convention the prose implies, and this is the single most
valuable thing Step 2 found:

- The witness string the writeup prints is the solver dump of `input_data[120:0]` — i.e.
  **MSB (bit 120) first**. So its **first character is the last bit clocked into `I`**.
- Reading that string row-major top-left yields the published grid **rotated 180°**.
- **Reversing** the string yields the published grid **exactly**.
- The writeup's prose — *"Interpreted as an 11x11 grid in row-major order (first bit fed is
  top-left, last is bottom-right)"* — describes the **grid** correctly but never flags that the
  printed witness is in the opposite order.

Consequences for us, recorded now so Step 5 does not mistake it for a failure:

1. Two distinct constants must be kept: `WITNESS_AS_PRINTED` (solver order) and `FEED_ORDER`
   (`= WITNESS_AS_PRINTED[::-1]`, what is clocked into `I`).
2. Any solver we write will naturally emit the **solver order**; its bit order will likely be
   *opposite* to the published printed string and identical to `FEED_ORDER`. Comparing the wrong
   pair would look like a wrong answer while being exactly right.
3. The check is non-vacuous: the grid is **not** 180°-symmetric, so the two orders genuinely
   differ. `tools/target.py` asserts this.

Acceptance constants now fixed in `tools/target.py`: `ANSWER_STRING = 'TWO STARS'`,
`SUCCESS_CYCLE = 126`, `FEED_BITS = 121`, `GRID` (11 rows), `MESSAGE_TABLE`, `FEED_ORDER`.

---

## 3. Their method, stage by stage

Their own framing: extracting the netlist was "a few evenings of iterating"; the solve itself
took **under two minutes** of solver time.

### Stage 1 — Reconnaissance
Opens the GDS in KLayout, reads the VCD. Identifies the interface from the VCD `$var` list. Reads
the `TRY AGAIN` byte stream straight out of the VCD and infers the stimulus timing (3 reset / 1 idle /
121 feed / stream out). Visually surveys metal layers: *"met5 is almost entirely a power grid,
met4 has a few long vertical routes plus power, and signals mostly live on met1 through met3 with
vias hopping up to met4 for longer runs."* Notes the floorplan: *"clear rectangular blocks, one
dense column on the right, two flop banks in the middle, and a small dense block in the top-right
corner that turns out to be the output ROM."*

### Stage 2 — Cell inventory
Walks `top.references` with gdstk, counting by `cell_name`. First pass: **412 cells** — "looked
low". Claims `AREF` array references were the cause and that recursive expansion plus array
replication reached **738**. Filters physical-only cells (taps, fills, decaps, endcaps, antenna
diodes) by name prefix. Treats `clkbuf_*` as logically transparent and **splices** each clkbuf
out (input net → output net) to avoid needing models for its diode pins.

### Stage 3 — Net extraction (the bulk of the work)
Chose a **hand-written polygon-based extractor** (gdstk + shapely) over KLayout's built-in tracer.
Pipeline:

1. Read every polygon on every metal and via layer.
2. Merge overlapping **and edge-abutting** polygons per layer with `shapely.unary_union`.
3. For each via, union the metal polygons above and below that it intersects.
4. Transitive closure (union-find) across layers → nets.

Power/ground are identified **structurally**: run the union-find, compute total polygon area per
net, and **drop the two largest nets**. IO port names are attached by intersecting the GDS text
labels for `clk`, `rst_n`, `enable`, `I`, `O[0..7]`, `success` against whatever net polygon they
overlap — across **every** layer, not just the top metal.

### Stage 4 — Pin matching
Pins come from the **PDK macro LEF** (`libs.ref/sky130_fd_sc_hd/lef/`), not from the GDS. For each
placed cell: gather the pin's rectangles, translate by cell origin (applying rotation), and
intersect against an `STRtree` of merged polygons on that pin's layer. Pins split across several
RECT entries are unioned. After each iteration a **single-driver check** runs: every gate output
(`X`) and flop output (`Q`/`Q_N`) drives exactly one net, every net has ≤ 1 driver.

### Stage 5 — Emit Verilog
Nets renamed `net<N>`, IO nets get port names, each instance becomes a structural instantiation.
Behavioural models come from the **SkyWater PDK Verilog primitives** (`primitives.v`,
`sky130_fd_sc_hd.v`, the UDP definitions) — no transistor-level simulation.

### Stage 6 — Verify against the provided VCD
A **Verilator** testbench replays the exact reset and bit sequence from `example_inputs.vcd` and
prints the `O` bytes. Iterates until the byte stream matches exactly, with `success` low
throughout. (Verilator is run under WSL on this machine; that is our environment's concern, not
theirs.)

### Stage 7 — Solve with formal verification
After "an hour probing internal signals in simulation", they inferred the structure by watching
counters: a counter wrapping at 121 (bit counter), a counter incrementing every 11 (row counter),
two flop banks saturating per row and per column, and an 8-bit counter comparing the running
total against 22. The constant 22 plus the 11×11 shape pointed at Star Battle; a shift register
around the current position matched the eight-direction adjacency rule.

They then **declined to brute-force or decode the region LUT**, on the reasoning that the four
easy constraints alone admit thousands of boards. Instead: **SymbiYosys `cover(success)`** with a
free `anyconst` 121-bit input, feeding bits on the exact VCD-derived schedule. `mode cover`,
`depth 200`, `append 50`, engine `smtbmc bitwuzla`. ~105 s. Then **uniqueness** by adding
`assume(input_data != <known_solution>)` and getting **UNSAT**.

Their explicit stance: *"The solver did not care about the structure of the problem or how clever
the region encoding was, it just needed the circuit unrolled deep enough to reach the success
condition."*

### Stage 8 — Recover the region map (post hoc, for understanding)
Rather than decode the `magic_index` LUT: **stimulate each cell position one star at a time** and
record which per-region saturation counter fires. Plotting which counter each cell belongs to
recovers the region map, which spells **"JS"**. Their own framing: *"The region map is
confirmation, not prerequisite."*

---

## 4. Their tooling stack

| Tool | Role |
|---|---|
| KLayout | visual survey, manual route tracing, layer/geometry oracle |
| gdstk | GDS reading, instance walking |
| shapely | per-layer polygon union, STRtree spatial index |
| SkyWater PDK (`google/skywater-pdk`) | **macro LEF for pin geometry**; Verilog primitives for cell behaviour |
| Verilator | VCD replay regression harness |
| SymbiYosys + bitwuzla | `cover(success)`; z3 and yices tried and rejected as slower |
| `yosys-witness display` | extract the witness bit vector |

---

## 5. Their failure modes and traps — what actually cost the time

The post is unusually candid about this, and it is the most directly reusable part: every one of
these is a trap **we** could fall into.

| # | Symptom | Root cause | Their fix |
|---|---|---|---|
| T1 | Instance count 412, "looked low" | claimed unexpanded `AREF`s | recursive walk + array replication → 738 |
| T2 | Whole columns of flops resolve to `X` on reset | `conb` tie cells wrongly bucketed as physical-only, so reset pins had no driver | remove `conb` from the physical-only filter |
| T3 | Floating diode pins on clkbuf outputs | LEF distribution was the "antenna diode removed" variant; GDS has the diode cells attached | treat `clkbuf` as a wire, splice it out |
| T4 | Tens of thousands of net fragments, union-find taking minutes | merging only *overlapping* polys, not edge-abutting ones | `shapely.unary_union` once per layer |
| T5 | Naive intersection minutes per run | O(N²) polygon tests | build `STRtree` once per layer |
| T6 | ~a dozen pins near the top of the die appear unconnected | layer map taken from a third-party fork had **`via3` at the wrong datatype**; long met4 routes broken | take the canonical layer map from the PDK's own `layers.lyp` |
| T7 | Pins appear to float at random | GDS is **1 nm DBU**, LEF is microns; fractional-micron LEF coords become floats like `12959.999999999998` | integer-snap LEF→DBU with **one consistent** rounding direction |
| T8 | ~90 % of signal pins matched no polygon | pointed at `sky130_fd_sc_hd.tlef` — the **technology** LEF — which contains **no pin geometry**; script didn't check that rectangles existed | switch to the full macro LEF under `libs.ref/sky130_fd_sc_hd/lef/` |
| T9 | Pins whose output is comb-shaped appear unconnected | higher drive strengths split one pin across several RECT entries; stopped at first intersection | iterate all rectangles per pin and union the nets they touch |
| T10 | ROM cells' pins float / wrong nets; output bytes garbled but recognisably ASCII | (their claim) first pass applied only translation, no rotation | apply rotation from `ref.rotation` |
| T11 | `O[3]` wrong at the 3rd character | one pin's Y coordinate landed at a half-integer via 0.5 nm rounding, so the intersection jittered between two adjacent polygons by STRtree order | the integer snap from T7 |
| T12 | Solver returns `UNKNOWN` / times out | harness held reset for **4** cycles instead of **3**, overlapping reset with the first feed cycle | match the VCD exactly: 3 reset / 1 idle / 121 feed |
| T13 | Port labels missed on the lower half of the output bus | restricted the label search to met4; labels sit on varied layers | iterate every text element in the top cell, all layers |

Their own stated meta-lesson: *"none of the individual steps in GDS-to-netlist extraction
required a conceptual leap. What took time was mechanical: keeping coordinate systems aligned,
finding the LEF variant that actually had pin geometry, sourcing the layer map from the PDK
itself rather than a third-party fork, checking each step against the warmup before trusting it
on the full design."* And: *"whatever broke on the 738-cell netlist also broke on the 27-cell
warmup"* — which they only noticed in hindsight.

---

## 6. Their self-declared limits

Quoted, because these mark exactly where headroom exists for us:

1. *"I never pushed the KLayout path far enough to say whether it would have worked too."* —
   KLayout's built-in tracer was abandoned untested.
2. *"I do not know how well this holds for a design much bigger than this one, or for a puzzle
   with a different kind of constraint."* — scale is unaddressed.
3. *"Rather than fully reverse-engineer that LUT by hand, formal verification can answer the
   question directly from the netlist."* — the `magic_index` LUT was **never decoded**. The
   region constraint is taken on trust from the solver.
4. The region map was recovered by **stimulus probing**, not by analysis.

---

## 7. Discrepancy log — their claims vs our own measurements

This is the heart of Step 2. Every row is backed by a passing Step 1 gate check
(`recon/inventory.json`) or by `tools/target.py`.

| # | Their claim | Our measurement | Verdict |
|---|---|---|---|
| D1 | "738 standard cells" | 1618 std cells − 676 taps − 204 decaps = **738** | ✅ **reproduced exactly** |
| D2 | "Cell instance names are preserved in the GDS" | 69 sky130 masters present as `SNAME`s on every reference | ✅ confirmed |
| D3 | Morse strip reads `PER ARENAM AD ASTRA` | independently decoded, 36 bars, all groups map, `unmapped_groups == 0` | ✅ **independently reproduced** |
| D4 | VCD `$version` comment + leap-second `$date` | both present verbatim in `example_inputs.vcd` | ✅ confirmed |
| D5 | Provided VCD prints `TRY AGAIN` and `success` stays low | parsed: `TRY AGAIN` ×2, `success` never high | ✅ confirmed |
| D6 | Interface = `clk, rst_n, enable, I, O[7:0], success` | VCD header + GDS layer-70/5 labels, 13 ports | ✅ confirmed |
| D7 | Feed length 121 | measured **121** in both trials of the VCD | ✅ **independently reproduced** |
| **D8** | **"a lot of them are AREFs (array references) that the pad ring and fill-cell rows use; gdstk does not automatically expand array references"**, hence 412 → 738 | **Zero AREFs in the top cell.** `array_references == 0`; all **9875** placements are plain SREFs. Verified three independent ways: gdstk walk, KLayout `each_inst`, and our own dependency-free GDSII record parser. gdstk returned all 9875 refs on the first pass — no expansion needed. | ❌ **not reproducible from the published artifact** |
| **D9** | **"Cells in the output ROM block are placed with 90-degree rotation. KLayout shows them clearly as a grid oriented perpendicular to the main standard-cell rows."** | **Zero 90°/270° placements.** All 1618 cells are `rot000`/`rot180` ± mirror. Verified three ways: gdstk `(rotation°, mirror) ∈ {(0,F),(0,T),(180,F),(180,T)}`; KLayout agrees; and the decisive test — **all 1618 instances keep their native 3.20 µm row-height bounding box**, whereas a 90° rotation would make them width-tall. | ❌ **contradicted** |
| D10 | Implied by D9: KLayout "shows" a perpendicular grid | KLayout's `Trans.angle` is in **90° units**, so its `r2`/`m2` mean **180°**, not 2°. Reading it as degrees manufactures exactly the phantom population D9 describes. | ⚠️ **probable root cause of D9** |
| D11 | "738 standard cells" as the functional count | 738 includes 10 antenna diodes; excluding all physical-only gives **728** | ⚠️ convention ambiguity — we record both |
| D12 | Floorplan: two central flop banks + adjacency block between them + vertical counter column lower right + ROM block top right | We have 92 flops and spatially clustered geometry (see `recon/hints/`), **C3 labelled the blocks** (counters, shift register, comparator, ROM) from the flop traces, and E1 reads the message out of the ROM | ✅ **verified in outline** (C3, E1) |
| D13 | 11 regions spell "JS" | C4 recovered a partition of the 121 cells into 11 classes, two of which (cropped to their own bounding box) draw as the literal glyphs **J** and **S**, and E2 shows the chip corroborates the partition on all 189 boards of the swap family, with 0 of 2000 look-alikes reproducing that agreement | ✅ **reproduced** (AC6 PASS, 2026-09-16 — recovered by the same single-star stimulus probing method §8 describes, disclosed rather than presented as a symbolic decode) |
| D14 | `success` asserts on cycle 126 | **E1**: `success` rises at cycle 126 at offset 4 and at no other offset (5–8 answer `TRY AGAIN`) | ✅ **verified** (AC2) |
| D15 | "the output ROM prints `(* TWO STARS *)`" ⟷ our own byte-level reading of the provided VCD gives `TRY AGAIN` | consistent: the provided VCD is a *wrong* input | ✅ consistent |

### Why D8 and D9 matter

Both are cases where **a claim in the published record is not reproducible from the artifact that
was published**. Neither is cosmetic:

- **D8** means the stated provenance of the "738" count is unreliable, even though the number
  itself checks out. Anyone reproducing by expanding AREFs would find nothing to expand.
- **D9** is the dangerous one. If we took the writeup at face value we would build rotation
  handling for "ROM cells" that do not exist, and — worse — we would **not** build the two-sign
  mirror/180° handling that the design actually requires across **994 of 1618** instances
  (761 mirrored, 448 at 180°). That is a correctness bug in pin matching, i.e. in the netlist,
  i.e. in the answer.

They are also the strongest support for the Step 1 conclusion that **independent measurement
beats inherited assumption** — which is the premise of our whole approach.

---

## 8. What we inherit, what we must not, and what is still open

**Trap list we now inherit for free** (no excuse for hitting these): T1–T13 in §5, plus R1–R8 from
`docs/01_problem.md`. In particular the physical-only filter list, the `conb` exception, the
`clkbuf` transparency, per-layer polygon union, STRtree, integer snapping, all-rectangle pin
union, all-layer label search, and exact VCD timing.

**Explicitly off-limits for Step 3** — these are the published approach, and copying them is
prohibited by `AGENTS.md`:

1. Hand-written shapely polygon-union net extractor with union-find across layers.
2. Pin geometry sourced from the **PDK macro LEF**.
3. Behavioural cell models from the **PDK Verilog primitives**.
4. `cover(success)` via **SymbiYosys + bitwuzla** to obtain the answer.
5. Region map by **stimulus probing** of the saturation counters.

**Open ground we can occupy** (to be designed in Step 3, listed here only to show it exists):

- **Pin geometry from the GDS itself.** The cell masters inside `puzzle.gds` contain their own pin
  label layers and pin metal. Deriving pins from the artifact removes the entire PDK/LEF
  dependency — and with it traps T3, T8 and T9, which were all LEF-variant or LEF-convention bugs.
- **Decode the region logic instead of trusting it.** The published work never decoded the
  `magic_index` LUT; the region constraint is inherited from the solver. Reading it out of our own
  netlist is analysis rather than probing, and is the difference between "the solver found
  something" and "we understand the chip".
- **Ours own solve.** A constraint/SAT/CP formulation over the *recovered* region map, instead of
  a formal `cover()` over an opaque netlist. Cheap, explainable, and it also produces the map.
- **KLayout's tracer**, which they explicitly left unevaluated.
- **Scale**, which they explicitly left unanswered.

**Still genuinely unknown to us** (must be resolved by our own work, not by reading):
the bit↔grid bit-order at the *input* side (how a fed bit indexes the grid), the exact `success`
condition as implemented, the region partition mechanism, the counter architecture, the output
ROM interface, and the ROM's full message vocabulary.

---

## 9. Step 2 verification

```bash
$ .venv/Scripts/python tools/target.py
witness length           : 121 (expect 121)
feed order  -> grid == grid  : True
as-printed  -> grid == rot180: True   (distinction meaningful: True)
ones total               : 22 (expect 22)   OK
row counts               : [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2]   OK
col counts               : [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2]   OK
adjacent star pairs      : 0   OK
TARGET VERIFICATION: PASS

$ .venv/Scripts/python tools/checks/check_step2.py
  ... PASS ...
STEP 2 GATE: PASS
```

The Step 2 gate verifies: (a) every verbatim quote cited in this document appears in the
retrieved source snapshot; (b) the target answer constants are internally consistent and satisfy
the four mechanical constraints, and the feed-order/as-printed relationship holds non-vacuously;
and (c) each discrepancy row above is backed by the Step 1 inventory — specifically that
`array_references == 0` (D8) and that no 90°/270° placement exists (D9).
