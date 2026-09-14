# Step 3 — Our Own End-to-End Plan

**Step 3 goal:** a complete, unambiguous, executable plan that reaches the already-known
answer (`TWO STARS` / success at cycle 126 / `(* TWO STARS *)`) by a route that is **ours**,
not the published one. Split into small steps so each can be debugged in isolation and
verified before the next begins.

**Execution rule (locked, Q3b):** one step per turn. Execute → verify → report evidence → **stop
for go-ahead.** Never two steps at once. After every step, re-run every earlier step's gate.

**Reading order:** §1–§6 are the design. §7 is the step sequence — that is what gets executed.
§8–§11 are the risk, gate, and traceability apparatus.

---

## 1. Objective and acceptance contract

Reproduce **all** of the following from our own pipeline (locked at intake, Q2a):

| # | Acceptance criterion | Authoritative value |
|---|---|---|
| AC1 | the exact 121-bit input vector asserting `success` | `tools/target.py :: FEED_ORDER` |
| AC2 | `success` asserts at **cycle 126** | `SUCCESS_CYCLE = 126` |
| AC3 | output string | `(* TWO STARS *)` |
| AC4 | four wrong-input messages | `EMPTY SKY`, `BIG BANG`, `TWO NOT TOUCH`, `TRY AGAIN` |
| AC5 | byte-exact replay of `example_inputs.vcd` | `TRY AGAIN` ×2, `success` low throughout |
| AC6 | region partition recovered from the design's own latches, spelling "JS" | **Re-scoped 2026-09-13 (C4 R20), see §C4.** The partition is recovered: 11 capacity-2 classes, two of the answer's stars each, found as an exact cover over measured trigger sets. **E2 then corroborated it through the chip's own message** (`TWO NOT TOUCH` on all 23 boards built to satisfy it, and not on the 8 built to break it), which is evidence the verdict channel could not give. It is still **not** met as "the map, confirmed by the chip", and the "JS" spelling is not reproduced. AC6 is therefore met as "recovered by our own method, corroborated by the chip's message, with its evidential weight measured" |

The target is already independently verified against itself in Step 2 (`tools/target.py` → PASS),
including the non-vacuous **feed-order vs as-printed** bit-order distinction. Step 5 compares
against that, not against prose.

---

## 2. Non-negotiable constraints

**Hygiene (from `AGENTS.md`):** `asic-puzzle-2026/` is read-only. No step may write inside it.
One step at a time, verified. Honest reporting — if a step fails, say so with real output.

**Prohibited — the published approach.** From `docs/02_known_solution.md` §8, we may not build:

| # | Off-limits | Why it is excluded |
|---|---|---|
| P1 | Hand-written shapely `unary_union` + union-find polygon-merge extractor | it *is* the published extraction method |
| P2 | Pin geometry from the **PDK macro LEF** | it *is* their pin source, and it caused traps T3/T8/T9 |
| P3 | Cell behaviour from the **PDK Verilog primitives** | it *is* their simulation model source |
| P4 | `cover(success)` via **SymbiYosys + bitwuzla** | it *is* their solve |
| P5 | Region map by **stimulus probing** (one star at a time) | it *is* their region-recovery method |

Inheriting their **trap list** (T1–T13, R1–R8) is encouraged — that is free knowledge, not
copying a method.

**Dependency policy (Q5a).** No SkyWater PDK. No `yosys`/`sby`. No new heavyweight toolchain.
Everything stays on pip packages plus `iverilog`, all already present. If any step later needs a
large download we quote its size **before** pulling it.

---

## 3. Declared differentiators

Required by `AGENTS.md` ("say what we do differently and why it is ours"). Each maps to a step.

| # | Differentiator | Where | Why it is ours, and why it is better |
|---|---|---|---|
| **Δ1** | **The chip describes itself: layer roles and connectivity rules derived from the GDS's own via masters**, not from any tech file or layer map | A1, A2 | Their trap **T6** was a wrong `via3` datatype from a third-party map. Deriving the layer stack from the via masters' own geometry removes that entire failure class — a wrong map is impossible because there is no map to be wrong. Their writeup also notes the PDK's own `layers.lyp` was needed; we need no such file. |
| **Δ2** | **Pin names and pin geometry extracted from the cell masters inside `puzzle.gds`** — the name is read from the master's own pin-label text; the shape is the conductor geometry the label annotates | A3, A4 | Removes the LEF dependency (P2) and with it traps **T3, T8, T9** (absent pin geometry, technology-LEF confusion, split-RECT pins). It also *grounds the cell semantics in the artifact*: `RESET_B`/`SET_B` label names tell us reset/set polarity without consulting any library documentation. |
| **Δ3** | **KLayout's built-in connectivity extraction engine, driven by our derived layer stack** — the path the published work abandoned untested | B2 | Their self-declared limit #1 is *"I never pushed the KLayout path far enough to say whether it would have worked too."* We test it. A mature, maintained engine replaces a bespoke union-find, and `shapely` drops out of the dependency list entirely. |
| **Δ4** | **Two independent solver implementations, and no third-party solver** | D1, D2 | They used a formal `cover()` over an opaque netlist (P4). We solve the constraint problem we recovered, with our own search, and prove uniqueness by exhaustive enumeration — two separately-written enumerators agreeing. Deterministic, explainable, and it needs the region map, so understanding is *required*, not optional. |
| **Δ5** | **The hidden constraint is characterised by measuring the design, and the strength of that characterisation is measured too** — every latch's single-star trigger set is swept, the partition is *found* as an exact cover over those sets rather than assumed, and three controls bound what the result is worth | C4, C5 | **Amended 2026-09-13 (R20); the original claim was not satisfied and is not claimed.** As written, Δ5 said the region-select cone would be reduced to a function of the grid index with no stimulus. Measurement refuted the premise: there is no per-cell region decode (R9, R10), no per-region counter (R11–R15, R18), no window rule (R16) and no picture in the GDS (R17), and the eleven counters are the eleven **column** counters. What is ours, and stands: the partition comes from the netlist's own latches by measurement, the *controls* (test power, look-alike uniqueness) are part of the result rather than an afterthought, and the published per-region-counter method is shown to target objects this netlist does not contain — its premise, not just its output, is corrected |
| **Δ6** | **Two independent behavioural oracles** — the warm-up *source* and the provided *VCD* — used to validate our cell models and netlist, not just the final answer | C1, C2, B7 | Their own hindsight lesson was that *"whatever broke on the 738-cell netlist also broke on the 27-cell warmup"* and that checking the warm-up earlier would have cut debugging. We invert that: the warm-up is the **primary** correctness gate for the extractor, and the VCD is the primary gate for the models. |

**Where we deliberately stay in the same family:** both approaches must, at some point, recover
connectivity from geometry. That is inherent to the problem, and `AGENTS.md` explicitly tolerates
family similarity. What differs is the *engine*, the *inputs to that engine*, the *model source*,
and the *solve*.

---

## 4. Why this has a good chance of working — evidence already in hand

From `docs/01_problem.md`, the layout is far more structured than generic P&R output. That is what
makes a self-describing, label-driven extraction tractable:

| Observation | Consequence for us |
|---|---|
| All **1618/1618** cells sit exactly on the 0.46 µm site grid (x-residual = single bucket at 0.0) | Absolute instance origins are exact; no drift correction |
| Only **every other row** is populated (52 rows, pitch 5.44 µm) | No vertical cell overlap → no geometric ambiguity between neighbours |
| **Zero** 90°/270° placements; only 0°/180° ± mirror | The instance transform is two independent sign flips — no rotation matrix needed anywhere (and no trap from KLayout's 90°-unit `Trans.angle`) |
| **Zero** AREFs, 9875 explicit placements | Instance enumeration is a flat list; no array semantics |
| Every cell master is present in the GDS with its **pin-label text intact** (32 distinct label strings seen) | Δ2 is directly feasible |
| Nine `VIA_*` masters exist as real cells with real geometry | Δ1 is directly feasible |
| Interface timing is exactly 3 reset / 1 idle / 121 feed | The testbench is deterministic and byte-comparable |

---

## 5. Architecture

Package lives under `tools/` so it is importable as `tools.puzzle.*` from the repo root.

```
tools/
  __init__.py                 # makes `tools` a package (new)
  inventory.py  vcd_probe.py  gds_dump.py  kl_recon.py  render.py  target.py   (existing)
  puzzle/
    __init__.py
    cli.py            # single entry point: python -m tools.puzzle <stage>
    gds.py            # exact-integer GDS access layer (DBU ints only, never floats)
    layers.py         # A1/A2  self-derived layer roles + via layer-pairs
    pins.py           # A3/A4  master pin names + master-local pin geometry
    instances.py      # B1     placement table + exact affine transforms
    connect.py        # B2/B3  connectivity extraction (KLayout engine, Δ3)
    netlist.py        # B4/B5  net<->(instance,pin) model + integrity checks
    emit.py           # B6     structural Verilog emitter
    cells.py          # B6     OUR behavioural cell models (Δ2-grounded semantics)
    analyse.py        # C3     behavioural decomposition (amended 2026-09-12)
    verdict.py        # C4/C5/E1  the simulation-free evaluator, the candidate partition, the
                      #        rejected boards, the winning vector (added 2026-09-13, R20)
    solve.py          # D1/D2  our constraint solver + independent enumerator (Δ4)
    simulate.py       # C1/E    iverilog harness
  checks/
    check_step1.py  check_step2.py  check_step3.py  ...  check_stepN.py
recon/derived/      # every machine-derived artifact (JSON), regenerable
build/              # generated Verilog + simulation outputs
docs/               # 01_problem, 02_known_solution, 03_our_plan, spikes/, steps/
```

Conventions, enforced by the gates:

1. **All geometry is integer DBU.** No floats in any geometry path. Conversions happen once, at
   read time, with a single documented rounding direction. (This pre-empts traps T7/T11.)
2. **Every derived artifact is JSON in `recon/derived/`**, regenerable by one command, and is
   checked in so diffs are visible.
3. **Every stage is idempotent and stage-addressable**: `python -m tools.puzzle <stage>`.
4. **No stage reads from the PDK**, and a gate asserts that no path under the repo references it.

---

## 6. Key algorithm decisions (fixed now, so no step has to improvise)

### 6.1 Layer roles from the artifact (A1)
Classify each of the 33 `(layer, datatype)` pairs by structural evidence only:
- **cut/via layers**: shapes that appear in `VIA_*` masters and are small and square-ish;
- **conductor layers**: layers carrying large or elongated shapes that appear in cell masters
  (`li1`, `metN`) or in the top cell as routing;
- **label layers**: layers whose elements are `TEXT` records (datatype 5 family), giving pin/net names;
- **non-electrical / decoration**: anything only present outside the prBndry, or with no via coupling.

### 6.2 Via layer-pairs from via masters (A2)
For each `VIA_*` master, the set of conductor layers its geometry touches defines a layer pair.
That table *is* our connectivity rule set — no hand-written map. Cross-checked by counting
instances per pair and confirming every via instance is explainable.

### 6.3 Pin model (A3/A4)
For each of the 69 masters, in master-local integer DBU:
- gather `TEXT` elements on label layers → `(pin_name, layer, x, y)`;
- for each label, find the conductor shapes of the corresponding drawing layer that contain the
  label point, and **union all of them** (handles split/comb pins structurally rather than by
  iterating RECT lists — the job trap T9 did by hand);
- a pin's geometry is therefore a set of rectangles, and its layer set is closed under the via
  table so a pin that reaches met1 can be matched against met1 routing.
- **Direction** (input/output) is *not* inferred from names. It is derived later from the
  netlist: a pin that is the unique driver of its net is an output; pins on a net with a driver
  are inputs. Ground truth for validation comes from the warm-up, whose correct netlist we hold.

### 6.4 Instance transforms (B1)
Only two sign flips exist. The convention is fixed **empirically**, not assumed: for every
instance, we require `transform(master_bbox) == instance_global_bbox` (the global bboxes were
already measured in Step 1). The convention that satisfies this for all 1618 instances is the
correct one. Any residual mismatch is a hard failure of the step.

### 6.5 Connectivity (B2/B3)
Primary engine: KLayout `db.LayoutToNetlist`, configured with the A2 layer pairs as connections
and the A1 label layers for pin/net naming. Terminals come from the A4 pin model.

**Amendment (from A5 + the verification audit).** Terminals may **not** be matched by exact
overlap on the pin's own layers. A5 established that no pin in any master carries geometry on
met2–met5; that li1 carries all 489 routed pins and met1 only 20; and A1 established that li1
carries no top-level routing at all while the interconnect is met1–met5. A cell pin and the wire
it belongs to are therefore **not on the same layer** — they are joined by a *via instance*
(2696 `VIA_L1M1_PR_MR`s). The via instances are **part of the net**, not decoration between
layers, and an extractor that looked for a same-layer pin-to-wire overlap would find almost
nothing. This is why the A2 rule set is applied globally rather than per layer.

**Kill criterion / fallback (B2′):** if the engine cannot reproduce the warm-up netlist
(§7 B7), we fall back to a shape-graph extractor of our own: nodes are individual shapes,
same-layer touching is resolved with a uniform-grid bucket index, cross-layer by via overlap.
No shapely, no merged-polygon union. The fallback is written **only if** B2 fails.

### 6.6 Region decode (C4) — **method replaced 2026-09-13 after measurement; the original is recorded below as refuted**
**What the original said:** locate the region-select logic, reduce the select cone to a boolean function
of the index bits, evaluate it for indices `0..120`, no simulation and no stimulus.

**Why it cannot be done:** there is no region-select cone to reduce. R9/R10 found no one-hot region
enable and no exact cover among the netlist's nets; R11–R15 found no per-region counter; R18 measured
that the eleven counters are the eleven **column** counters; R16 refuted the stream/window rule; R17
found no picture in the GDS. A method whose target does not exist cannot be satisfied by looking
harder, so the method is replaced rather than the target.

**What is done instead** (C4 R19/R20, `tools/puzzle/verdict.py::stage_region_map`):

1. sweep a single star through all 121 positions and record, for every latch, the **trigger set** —
   the cells at which that latch ends high. 47 latches have a non-empty set;
2. **find** (do not assume) every sub-collection of trigger sets that partitions the 121 cells, as an
   exact cover driven by the lowest uncovered cell;
3. keep the covers that are *capacity-2*: eleven classes, each holding exactly two of the accepted
   board's stars. Two exist — the ragged partition (class sizes 4…28) and the eleven columns, which
   is the visible two-per-column rule;
4. for each, run the tests and, crucially, **the controls that bound the tests**:
   * the partition + the visible rules pins the accepted board as the **unique** solution (exhaustive,
     715 877 nodes) — while the visible rules alone admit thousands;
   * the C5 rejection set discriminates at **6%** (188 of 200 same-shape look-alike partitions also
     reject every board), so "the design rejects these boards" is not evidence for a partition;
   * **2 of 40** same-shape look-alike partitions also pin the answer, so uniqueness is a ~5%
     property, not a fingerprint.

**Validation:** it is a partition; exactly 11 classes; exactly 2 stars of capacity per class; the map
renders as an 11×11 picture (it does not read as "JS", which R20 records rather than explains away);
and the classes reproduce the "eight columns plus three regions over columns 4, 5, 6" shape R16 had
recovered from a different probe. C5's cross-check role is unchanged, and is now known to be
**corroborative only** — the verdict route cannot identify a map at all (R16 §3, R20 control 1).

### 6.7 Solver (D1/D2)
Search space: choose 2 cells per row from 11 columns.
- Per-row candidate patterns: all `C(11,2) = 55` pairs, minus the 10 horizontally adjacent pairs
  → **45** patterns per row.
- DFS over rows, maintaining per-column counts (≤2) and per-region counts (≤2), plus a
  **consecutive-row compatibility** test that forbids vertical/diagonal adjacency.
- Propagation: prune any partial assignment where a column or region can no longer reach exactly 2.
- Exhaustive: the search is run to completion to **count** solutions and prove uniqueness.
- Independent second implementation: a bitmask-per-row enumerator written differently, required to
  agree on both the unique solution and the total count. No z3, no SAT library, no SymbiSolver.

### 6.8 Simulation (C1/E)
`iverilog` + `vvp` (already on PATH). Generated `cells.v` contains our behavioural models, written
from the pin names and semantics we extracted. `puzzle.v` is structural. The testbench reproduces
the exact 3/1/121 schedule and dumps `O` and `success`; a Python comparator diffs it against the
VCD-derived reference byte for byte.

---

## 7. The step sequence

Each step has: **goal · inputs · method · artifact · verification · if it fails.**
Bold *verify* lines are the gate that must be pasted as evidence before moving on.

### Phase 0 — Foundations

**S0.1 — Package restructure.**
*Goal:* make `tools` a package so stages are runnable and importable.
*Method:* add `tools/__init__.py`; create `tools/puzzle/` skeleton; add `tools/puzzle/cli.py`
dispatching `python -m tools.puzzle <stage>`; update `check_step1.py` / `check_step2.py` to
`from tools import ...` and remove their `sys.path` hacks.
*Artifact:* package skeleton, updated gates.
***Verify:*** Step 1 gate and Step 2 gate both still pass, unchanged output; `python -m
tools.puzzle --list` prints the stage table.
*If it fails:* revert the import change; keep loose modules and a plain `tools/puzzle/cli.py`.

**S0.2 — Dependency manifest and "no PDK" assertion.**
*Goal:* declare exactly what we depend on, and prove we do not depend on the PDK.
*Method:* write `requirements.txt` pinning the installed versions; `docs/deps.md` explaining each
package's role; add a gate check that no tracked file references `skywater-pdk`, `libs.ref`,
`.tlef`, `primitives.v`, `symbiyosys`, `sby`, or `bitwuzla`, and that `shapely` is imported only by
legacy recon tools, never by `tools/puzzle/*`.
*Artifact:* `requirements.txt`, `docs/deps.md`, gate additions.
***Verify:*** gate passes; the forbidden-token scan returns zero hits in `tools/puzzle/**` and `docs/03_our_plan.md`.
*If it fails:* fix the offending reference — this check is not negotiable.

### Phase A — The chip describes itself

**A1 — Layer census and role classification.**
*Goal:* turn 33 raw `(layer, datatype)` pairs into a role table.
*Method:* for each pair compute element kinds (`BOUNDARY`/`PATH`/`TEXT`), counts, shape-count in
via masters vs cell masters vs top cell, and bbox statistics. Apply the §6.1 rules. Note explicitly
that the sky130 layer→name mapping is treated as a **hypothesis to confirm**, never assumed.
*Artifact:* `recon/derived/layers.json`.
***Verify:*** every one of the 33 pairs is assigned a role; the role assignment independently
rediscovers the Step 1 facts (13 port labels on a label layer; the decoration layer outside the
die; the prBndry layer); conductor count is plausible for a 4-metal stack; no pair unclassified.
*If it fails:* print the raw statistics per pair and classify manually **into the JSON** with a
comment recording that it was hand-classified — never silently.

**A2 — Via layer-pair table.**
*Goal:* the connectivity rule set, from the via masters.
*Method:* for each `VIA_*` master, collect the conductor layers its geometry touches; emit pairs.
*Artifact:* `recon/derived/via_pairs.json`.
***Verify:*** each of the 9 via masters yields a well-formed pair (2 distinct conductors); the
union of pairs forms a connected graph over the conductor layers (i.e. there is a path from the
lowest to the highest metal — otherwise some net could never be traced); every one of the 8221 via
instances maps onto a known pair.
*If it fails:* dump the per-master layer touches and inspect; a via whose geometry spans three
conductors may indicate a stacked via — handle by emitting all pairs it bridges.

**A3 — Master pin names from in-master labels.**
*Goal:* pin names per master, from the artifact.
*Method:* gather `TEXT` elements on label layers inside each master; group by name; record layer
and local position.
*Artifact:* `recon/derived/pin_names.json`.
***Verify:*** all 69 masters yield at least one label; spot-checks that are *derived, not hardcoded*:
the set of masters with a `RESET_B` label has the same size as the set whose name starts `dfr`;
`SET_B` ↔ `dfs`; every `mux2_1` has `S`; `CLK` appears only on sequential cells. Population counts
are reported for the record.
*If it fails:* the label layer list from A1 is wrong — return to A1, do not hardcode.

**A4 — Master pin geometry (master-local).**
*Goal:* for each master and pin, the conductor rectangles that constitute it.
*Method:* §6.3. For each label, find same-number drawing-layer shapes containing the label point,
then close the set under the via-pair table so a pin reaching met1 also carries li1/met1 geometry.
*Artifact:* `recon/derived/pinmodel.json`.
***Verify (warm-up calibration — the important one):*** build the warm-up's pin model from
`warmup/04_final.gds` and compare its **pin names per cell type** against `warmup/01_netlist.v`
(the file we are allowed to read because it is ground truth, not a method). It must match. Also:
every pin rectangle lies within its master's bbox; DBU values are all integers; no pin has zero
rectangles.
*If it fails:* isolate to a single master, dump its labels and the candidate shapes, fix, re-run.
Do not proceed to Phase B with a failing pin model — every later stage inherits its errors.

**A5 — Pin-model coverage report.**
*Goal:* state exactly how complete the model is before trusting it.
*Method:* report, per master, the number of pins with geometry vs pins named; list any pin with no
geometry and any label with no shape.
*Artifact:* section appended to `recon/derived/pinmodel.json` + a human table in `docs/steps/A5.md`.
***Verify:*** zero pins named-but-geometryless, or a complete explanation for each exception.

### Phase B — Instances, connectivity, netlist

**B1 — Instance table with exact transforms.**
*Method:* §6.4.
*Artifact:* `recon/derived/instances.json`.
***Verify:*** 1618 standard cells; counts per master match `inventory.json`; 100 % on the site grid;
transform set is exactly `{rot000, rot000_mirror, rot180, rot180_mirror}`; and the decisive test —
**`transform(master_bbox) == instance_global_bbox` for all 1618**, which empirically fixes the
transform convention.
*If it fails:* the convention is wrong; try the other sign combinations for one known instance and
report which one satisfies all 1618.

***Verify, additionally (executed):*** the chosen matrix must be the **only** one that fits each kind
(a convention that is one of several fits is not a convention); the **warm-up DEF** must corroborate
it — 230 placements with explicit coordinates and orientation tokens, matched by cell and exact
lower-left corner, with the token → kind map a bijection (the DEF states the cell's lower-left while a
GDS anchor is corner-dependent, so the comparison is made on the *footprint* corner); and no two
instances' cell footprints may overlap (the `236/0` marker A1 identified gives the cell boundary, so
this is checkable rather than assumed). All four hold — see `docs/steps/B1.md`.

***Recorded for B4:*** a GDS placement point is an **anchor, not a lower-left corner** — a kind with
`sx = -1` anchors at the cell's right edge and one with `sy = -1` at its top edge. 1327 anchors carry
the 1618 placements, 275 of them holding two cells that occupy adjacent space. **`bbox_dbu` /
`footprint_dbu` is the unique position key; the anchor is not.** Downstream steps must not key on it.

**B2 — Connectivity engine spike on the warm-up (SPIKE, may fail).**
*Goal:* prove the engine works before pointing it at 1.4 MB of puzzle.
*Method:* drive `db.LayoutToNetlist` with the warm-up's A2/A3/A4 outputs; extract a netlist.
*Artifact:* `docs/spikes/connectivity_engine.md` + working code path in `tools/puzzle/connect.py`.
***Verify:*** the extracted warm-up netlist's **connectivity graph** is equivalent to
`01_netlist.v`'s — same instance count, and the same partition of pins into nets after canonical
renaming (compare as a graph isomorphism on the bipartite instance-pin/net structure, not by name).
*If it fails:* **document the failure with real output**, then implement fallback **B2′** (shape-graph
extractor, §6.5) and repeat this same verification. Record the outcome either way — this is exactly
the question the published work left open, so "it doesn't work, and here is why" is a result.

***Outcome (executed): IT WORKS — B2′ is NOT needed.*** The engine, configured *only* from A1's
conductor roles and A2's proved rule set, with terminals taken from A4's pin model, reproduces
`01_netlist.v` exactly: 230 instances mapped, 285 pins probed with 0 unconnected and 0 probe
errors, **84 nets and 285 terminals on both sides, 0 differing net signatures, 100 % of the
reference compared**. The two partitions are equal as sets of terminal-sets, so no net renaming
is even required. Full record: `docs/spikes/connectivity_engine.md`; gate
`check_stepB2.py` → 24/24.

Two things the spike established that the plan should carry forward:

* **Probe points must come from pin *label* positions, never from the centre of A4's `rect`.** A4
  stores the *bounding box* of each shape, and some pins are combs: `clkbuf_16.X` is a 68-point
  polygon whose bounding-rect centre lies inside no polygon, so a centre probe silently finds
  nothing (32 pins were lost this way before the fix).
* **A partition comparison must assert coverage, not just equality.** With instances
  mis-identified, this comparison ran over 4 nets and 6 terminals and reported PASS. Every
  comparison in this project now carries an explicit coverage floor — the third instance of the
  same class of bug (see `docs/verification.md` §4).

**B3 — Full connectivity on `puzzle.gds`.**
*Method:* same path, full design, using the derived layer stack and pin model.
*Artifact:* `recon/derived/nets.json`.
***Verify:*** runtime and net/shape counts reported; every conductor shape belongs to exactly one
net (no orphans); the largest few nets are identified as power/ground **by pin name** (`VPWR`/`VGND`
from A3), and that identification is consistent — this is a different, stronger method than
"drop the two biggest".
*If it fails:* bisect by region of the die; report the count of unassigned shapes.

***Outcome (executed): PASS — re-verified after a later fix.*** All 40 360 conductor shapes
probed individually, every one in exactly one net, 0 orphans, 0 unprobeable. Supply identified
by pin name as specified: the two largest nets carry only supply names (`VGND`+`VNB`, `VPWR`),
and the 972 supply pins the engine cannot place are **exactly** A5's two predicted gap classes
(942 `VPB` well ties + 30 diode supplies, 0 unexplained). Gate `check_stepB3.py` → **36/36**;
see `docs/steps/B3.md`.

**Revised by B4.** The first run of this step reported 729 nets / 9839 subcircuits, and a later
step found that its net **key was invalid**: in the hierarchical netlist `probe_net(point)`
returns the net of the *cell that owns the shape*, and `cluster_id` is unique only *within one
circuit* — 70 different nets, in 70 different circuits, all carried `cluster_id == 2`. This
stage's supply identification keyed nets on that number, so it was reasoned unsoundly even
though its answers happen to be right. The layout is now **flattened before extraction**, which
makes `cluster_id` a real net identity: **2626 nets, 2626 distinct cluster ids**, ranked by
polygon count (with no subcircuits, `subcircuit_pin_count()` is 0 for every net). The supply
identification survives unchanged — the same two clusters, the same pin counts, now an order of
magnitude bigger than the next net — and the gate asserts net-identity uniqueness explicitly so
a regression fails loudly. Lost in the change: the hierarchical run's independent evidence that
9839 subcircuits = 1618 cells + 8221 via placements, i.e. that the hierarchy is exactly one
level deep. That measurement stands in this stage's output but no gate can re-derive it.
See `docs/steps/B4.md` §2–3 and `docs/verification.md`.

Three things this step settled, worth carrying forward:

* **The per-net polygon shortcut does not reconcile** — `shapes_of_net` returns per-net *merged*
  regions while a layer's region is not, so 13 029 vs 16 869 on 67/20 is merge bookkeeping, not
  3 840 orphans. A set-difference variant across the hierarchy invented 2 315 phantom orphans.
  Probe every shape instead; it costs 1.5 s.
* **A recursive shape's geometry is in its own cell's coordinates.** Without
  `poly.transformed(it.trans())` the probe reported 31 844 orphans, all at sub-micron local
  coordinates. Fourth coordinate-system bug of the project, and the second that produced a
  plausible wrong number instead of an exception.
* **An artifact may not contain a clock.** Storing `runtime_s` in `nets.json` broke
  byte-identical regeneration on the first gate run; runtimes are now printed and measured by the
  gate, never committed.
* **A net key must be unique, and hierarchically it is not.** `cluster_id` is unique only within
  one circuit, and `probe_net` returns the owning cell's net. 70 nets in 70 circuits shared one
  id. `build_engine` therefore flattens before extracting. This is the fourth instance of the
  project's recurring bug class — keying on something not unique — and the only one that
  produced a confident wrong answer instead of an error.

**B4 — Net ↔ (instance, pin) mapping.**
*Method:* transform A4 pin rectangles by B1 transforms; assign each pin to a net by following its
geometry into the routing stack through the via instance that lifts it there (§6.5 amendment) —
not by same-layer overlap, which A5 ruled out.
*Artifact:* `recon/derived/pin_net.json`.
***Verify:*** every functional pin of every instance is assigned exactly one net; count of
unassigned pins is reported (and must be 0 for functional pins; a non-zero count with an
explanation is a failure, not a footnote).

***Outcome (executed): PASS.*** **2777 of 2777 functional pins** across all 1618 placements are on
exactly one net, 0 conflicts, 0 probe errors. Unassigned is 972 and is **exactly** A5's two
predicted classes (942 `VPB` well ties + 30 antenna diode supplies), 0 unexplained. The two
largest nets are supply-only by pin name, matching B3 (`VGND`+`VNB`, and `VPWR`). Gate
`check_stepB4.py` → **61/61**; see `docs/steps/B4.md`.

Two things this step established beyond its own deliverable:

* **The net key was wrong and is now fixed** — see the B3 amendment above. The first run reported
  a power net carrying 15 `clkbuf_4` outputs, which would have been a short; it was an artifact of
  `cluster_id` colliding across 70 circuits. Resolved by flattening, and confirmed two independent
  ways (a ±100 µm clipped window and a flattened whole-die extraction both give X=683, VPWR=27).
* **The terminals are trustworthy for the first time.** B5's single-driver and floating checks can
  now be stated against nets whose identity is unique; before this fix they would have been
  computed over merged nets and would have appeared to pass.

**B5 — Netlist integrity checks.**
*Method:* build the netlist object and run the checks.
***Verify:*** single-driver rule holds (each net has ≤1 driver, drivers inferred structurally);
no floating inputs; **single-terminal nets are an enumerated, fully classified set rather than
zero** — B4 measured 30, and every one must be either an unused cell output or an input driven by
a top-level port; the 13 port nets match the Step 1 label set exactly; power/ground carry only
VPWR/VGND pins; total instance count and total pin count are reported against expectation.
*If it fails:* the failing check names the offending net/pin — debug that, not the whole design.

*Amended before execution (2026-09-12).* The clause originally read "every net has ≥2 terminals
except the 13 top-level port nets". B4 measured 30 single-terminal nets and all 30 are legitimate:
15 unused `clkbuf_4` outputs, 8 unused `and3_2` outputs, 6 `conb_1` constant outputs (`HI`/`LO`,
which drive nothing by construction), and the clock root's `A`, which is driven by the top-level
port so it carries one instance pin by definition. An "unused output" is not a floating input —
the distinction is exactly what the direction inference below exists to make — so the check is
restated as a classification that must be complete, not as a count that must be zero. Writing it
as zero would have forced the gate to be loosened later on correct data, which is the failure
mode this project has now hit four times.

**Direction comes from the labels; structure audits it.** *Amended after execution (2026-09-12) —
this clause originally read "direction is derived, not declared", solving a linear system instead
of reading pin names: one unknown `x` per `(master, pin)` class, one equation `Σx = 1` per net, on
the argument that a well-formed net has exactly one driver.* That argument is sound, but its
enforcement was not: the propagation rules that satisfy `Σx = 1` can also **manufacture** a driver
on a net that has none, and a manufactured driver is indistinguishable from a real one in the
result. It produced one, and the fabrication then certified its own consistency check. The amended
clause is:

* the **verdict** is the output-pin label vocabulary the chip itself carries, read in A3 — a pin
  labelled `X`, `Y`, `Q`, `HI` or `LO` is an output, every other non-supply pin is an input. This
  is artifact evidence of the same kind Δ2 already sanctions, and it cannot be fabricated;
* the **structural solver is kept as an auditor**, not as the authority. It decides what it can
  from the equations, and where it disagrees with the labels, or cannot decide without inventing,
  that is recorded as a finding;
* a net with **no driver is reported and enumerated** — never asserted absent. "No net is
  undriven" is a claim this layout falsifies, and it is precisely the claim that forced the
  fabrication.

***Outcome (executed): PASS, then corrected.*** All 13 documented ports were found by their `70/5`
labels, positions agreeing with Step 1 to <0.05 µm, each resolving to exactly one net — and the
interface confirms them independently: **every input port carries zero output terminals and every
output port exactly one.** All **286 pin classes** now carry a direction (67 outputs, 219 inputs).

That last figure is a correction. The first run derived direction from structure alone and reported
284 of 286 with 2 undetermined and 0 undriven nets. **Both of those numbers were wrong**, and the
way they were wrong is the project's signature bug class arriving in the one place least able to
see it: the structural solver's "a net needs a driver" rule is enforced by mechanisms that can
*manufacture* a driver. On net 806 — `{a31oi_2.A1, a311o_2.A1}`, two inputs and nothing else — one
fired and declared `a31oi_2.A1` an output, which its own family name forbids (`a31oi` outputs `Y`).
The fabrication then **certified its own check**: once it had made every class on the net decided,
"every net whose classes are all decided has exactly one driver" passed *because of* it. The 2
"undetermined" classes were collateral — with `a31oi_2`'s output already spent on net 806,
`a31oi_2.Y` could no longer be recognised as net 766's driver. Net 766 was never ambiguous.

B6 found it, and only because B6's cell models are generated from family names and therefore
**refused to build** against an impossible direction. Direction now comes from the one piece of
evidence that cannot be fabricated — the pin labels the chip carries — with the structural solver
kept as an **auditor**: it decides 284 classes, is recorded as *contradicting the labels on exactly
one* (the fabricated `a31oi_2.A1`), and leaves exactly the two it could not decide honestly.

**One net in this design has no driver.** Net 806 is `{a31oi_2.A1, a311o_2.A1}`, both inputs,
entirely on li1. It is reported with its terminals and asserted by enumeration — never by a "no net
is undriven" claim, which could only ever have been met by inventing a driver. Feasibility is
otherwise total: 0 infeasible nets, 0 nets wanting two drivers, 0 output pins on a supply net. The
30 single-terminal nets are each classified — 21 unused outputs (15 `clkbuf_4.X`, 5 `conb_1.HI`,
1 `conb_1.LO`), 8 output-port terminals (the `O[0..7]` bits, each driven by an `and3_2.X`), 1
input-port terminal (the clock root's `A`). Gate `check_stepB5.py` → **54/54** (up from 42, the
new ones re-deriving the convention from A4 rather than reading B5's totals); see
`docs/steps/B5.md`.

Two things this step is worth remembering for:

* **A fifth instance of the project's bug class, this time inside a check.** Several checks were
  first written in the gate's `(label, actual, expected)` style when the stage's helper is
  `(name, passed, detail)`, which made `bool(13)` and `bool([])` decide the verdict — one check
  could never fail and three failed on correct data. All were converted and re-verified.
* **Direction is now available for B6** — from the pin labels, with the structural solver as an
  auditor rather than the authority. What B6 must *not* assume is that every net has a driver:
  net 806 has none, and it is enumerated rather than explained away.

**B6 — Emit structural Verilog + behavioural models.**
*Method:* `emit.py` writes `build/puzzle.v` (structural, our own net naming); `cells.py` writes
`build/cells.v`, our behavioural models for the 69 cell types, with semantics grounded in the label
names extracted in A3 (`RESET_B`/`SET_B` polarity read from the artifact, not from documentation).
*Artifact:* `build/puzzle.v`, `build/cells.v`.
***Verify:*** `iverilog -Wall -t null` compiles both with **no diagnostics** (and, as a control,
rejects a deliberately broken file with the same invocation); instance count in the Verilog equals
B1's; every cell type used has a model whose interface is that master's own pin set; and the emitted
text **round-trips** — parsed back and compared to B4's map entry by entry.
***Amended after execution (2026-09-12):*** this clause originally read "no implicit nets, **no
undriven wires**". This layout has exactly one undriven net (net 806), and demanding "none" is how
B5 came to invent a driver for it. The clause is now: *exactly one wire is undriven, it is named,
and it is the one the artifact enumerates.*
*If it fails:* compile errors localise themselves; a missing model is a `cells.py` omission.

***Outcome (executed): PASS.*** `build/puzzle.v` — 1618 instances, 7897 pins, 741 nets carrying a
pin, 726 plain signal wires, 972 pins emitted as explicit empty connections. `build/cells.v` — 69
modules (62 combinational, 3 sequential, 1 tie, 1 protection, 2 layout-only), each deriving its
function from its master's own function-family name and pin labels, with every master's parse
asserted against A3's actual pin set. Gate `check_stepB6.py` → **37/37**; `iverilog -Wall -t null`
exit 0 with empty stderr.

Two things this step is worth remembering for:

* **B6 found a defect in B5.** Generating models from family names meant `cells.py` refused to build
  a model whose family says `Y` while B5's table said `A1` — the fabricated driver on net 806 (see
  §B5 above and `docs/steps/B5.md`). A cross-step *interface* assertion caught what the upstream
  step's own gate had certified; B6 now runs the same check in the other direction.
* **A clean compile is necessary but not sufficient.** `iverilog` accepted all three flip-flop models
  with `Q` declared as an *input* and a stray `reg None;` — exit 0, `-Wall`, no diagnostics. The gate
  caught it by re-deriving each port's direction from A3 and B5 rather than trusting the generated
  text, and it compiles a deliberately broken file with the same invocation to prove the compiler
  can fail.

**B7 — Warm-up end-to-end regression (the Phase B gate).**
*Goal:* our whole pipeline must reproduce a design whose correct netlist we possess.
*Method:* run the whole chain on `warmup/04_final.gds` and compare against `warmup/01_netlist.v`.
***Amended before execution (2026-09-12):*** the clause originally read "run S0→B6", which in
practice would have meant B2's separate warm-up spike — a *different* implementation from the one
B3–B6 actually run, and a second implementation agreeing with the first would prove nothing. It now
requires the parts to be **shared**: the flattened engine of B3/B4 (`connect.build_engine`), B4's
prober (`netlist.probe_placements`) and B6's renderer (`emit.render_body`), with the placements the
only input that differs. Those three were factored out of the chip-specific stages for this step,
and the B3–B6 gates prove the factoring changed no behaviour.
*Artifacts:* `build/warmup.v` (our netlist for the warm-up), `recon/derived/warmup_b7.json` (the
comparison report), `docs/steps/B7.md`.
***Verify:*** same instance count; **same bipartite connectivity graph up to net renaming**, by
B2's exact canonical method and with B2's coverage guard, so B2 and B7 agree *by construction*; the
reference's own module header supplies the port list, and each port is located on our side by
terminal-set identity; the emitted netlist compiles against the same `build/cells.v`, so the models
are exercised on a design they were not generated for.
*A third comparison, added during execution:* `02_netlist_with_power_rails.v` connects VPB/VNB and
our extraction assigns them nothing. A5 predicted exactly that class (well ties have no routeable
geometry); B7 is the first time it is checked against an independent document rather than against
our own reasoning.
*If it fails:* **stop the phase.** Do not proceed to Phase C. Debug here — this is the single
highest-value gate in the project, because every later stage inherits this answer.

***Outcome (executed): PASS.*** The warm-up reproduces exactly: **84 nets and 285 terminals on both
sides, 0 signatures unique to either side, 100% coverage** — the same numbers B2 reported, which
they must, because it is B2's comparison run through the machinery B3–B6 use. 230/230 instances
mapped to our placements; 285/285 functional pins assigned; 0 probe conflicts or errors; the
flattened engine gives one circuit with 335 nets and 335 distinct cluster ids; all 6 ports located by
terminal-set identity (A=226, B=96, S=236, clk=237, en=11, rst_n=62); `build/warmup.v` compiles
against the unchanged `build/cells.v`, so the models are exercised on a design they were not
generated for. Gate `check_stepB7.py` → **39/39**; 18/18 gates.

Three things worth carrying forward:

* **The third comparison earns its place.** The reference connects 137 `VPB` pins and the GDS has no
  routeable geometry for them, so we assign none — A5's class, checked against an *independent
  document* for the first time instead of against our own reasoning. `VNB` is the control that makes
  it an argument rather than an assertion: a body pin too, 137 in the reference, and we recover all
  137. `VGND`/`VPWR` 230/230 each.
* **A compile is not a verification — twice over.** My first B7 emit declared every wire twice
  (`iverilog`: ~90 `already been declared` errors) and leaked the scratch path into the report, so
  the report could not regenerate byte-identically. The stage had reported PASS on all six of its own
  checks; only the gate's compile and regeneration checks saw either. Same lesson B6 learned, in a
  different costume: **generate, then re-read**.
* **Phase B is closed.** Every step in it is now verified against something outside itself — the
  A-phase numbers by `check_recompute.py`, B1–B5 by their gates, B6 by a parse-back round trip, B7 by
  an independent netlist for a design we did not author. `net 806` remains the chip's one undriven
  net and the warm-up has none, so it is a property of this GDS rather than of the engine; C1 reads
  it as **`z`** (corrected from "X": 4-state simulation gives an undriven `wire` high impedance,
  where `x` is what an unwritten `reg` reads), shows it cannot move an interface output under that
  waveform, and *measures* which models it cannot vouch for — 29 of 66. C2 covers those.

### Phase C — Understand the chip

**C1 — VCD replay regression.**
*Method:* `simulate.py` runs `iverilog` on `build/` with a testbench reproducing 3 reset / 1 idle /
121 feed / stream-out, twice, per the VCD; compare to the Step 1-derived reference.
***Verify:*** `O` byte stream matches `TRY AGAIN`, `NUL`, `TRY AGAIN`, `NUL` exactly, at the same
cycles (125…134, 281…290); `success` stays low for all 312 cycles.
*If it fails:* this is the second oracle. A mismatch here with B7 passing means the *models* are
wrong, not the netlist — bisect by comparing our `O[]` driver cones against the VCD's observed
`O` transitions.

***Outcome (executed): PASS.*** The reference is 625 clock edges at a uniform 5000 ps half period,
312 cycles, with every change to `rst_n`/`enable`/`I` on a falling edge — so cycle *k* is *t* = 5000
+ 10000*k* ps: the register state after that rising edge, with the stimulus held across the cycle.
The harness is **generated** from the file's own change list (95 timed lines), so it cannot drift
from the waveform; the only thing asserted rather than transcribed is the clock. Driven by the real
stimulus, our recovered netlist reproduces the real chip at every sampled instant: **312 × 9 = 2808
output-bit comparisons, 0 mismatches**, plus 936 stimulus bits, with no `x`/`z` in our outputs. The
waveform resolves to two 121-bit feeds (cycles 4…124, 160…280) that are *different vectors*, each
answered nine cycles later by `TRY AGAIN` and then held at `0x00` (125…133, 281…289); `success`
stays low on all 312 cycles. `recon/vcd_cycles.csv` — the Step-1 dossier's own table, written long
before C1 and by different code — agrees with a fresh parse of the raw file on all 1872 fields.
Gate `check_stepC1.py` → **83/83**; 19/19 gates.

Three things this step settles, and one it deliberately does not:

* **"Byte-exact" is defined as semantic equality, and said out loud.** No two simulators produce the
  same VCD bytes — this reference emits one `$scope` block *per signal*, which no simulator we know
  of does — so the target is every value of every signal at every sampled instant, `x` included. The
  gate re-derives the table sampling at a *different* instant than the stage, which pins the
  convention instead of restating it, and carries a **negative control**: the same harness with the
  stimulus moved one cycle must fail the comparison, and does.
* **`net 806` is characterised, narrowly and by experiment.** It reads `z` on all 312 cycles, and
  forcing it to 0 and then to 1 both leave every interface output identical. Nothing observable
  rests on it *under this stimulus* — and the claim stops there on purpose: its two consumers
  (`a311o_2`, `a31oi_2`) mask it state-dependently, so **E1 re-probes it on the winning vector**.
* **C1's own power is measured, because a large comparison count is not strength.** The reference is
  294 idle cycles around 18 cycles of one constant message, so C1 constrains *timing* tightly and
  *message content* weakly. `model-power` negates each cell model in a scratch rebuild of
  `build/cells.v` and re-runs the replay: **37 of 66 caught, 29 silently pass, 3 physical-only**.
  `or2_2` — F1's break, which passed all 18 gates in the Phase B review — moves 140 output cycles
  starting at cycle 3. Those 29 models are C2's job, and the list is why C2 must be exhaustive
  rather than sampled.

*Not settled here:* *why* the net is undriven (only that it changes nothing on this waveform), and
whether the models are right for the winning input. C2 and E1 close that.

**C2 — Warm-up functional equivalence (validates the cell models independently).**
*Method:* simulate our extracted warm-up netlist with our models, applying `warmup/00_source.v`
semantics: shift A and B in, then check `S` high iff `A + B == 496`.
***Verify:*** for an exhaustive or randomised sweep of `(A, B)` pairs, our netlist's `S` equals
`A + B == 496`. This is the check that makes our *models* trustworthy rather than name-guessed.
*If it fails:* the failing cell type is identified by which bit pattern breaks; fix that model.

***Outcome (executed): PASS.*** Exhaustive, not sampled — C1's measurement settled that argument:
8 serial bits per operand puts all **65536 pairs** in one simulation of milliseconds, so sampling an
oracle that can be exhausted is a choice to accept known coverage loss for no gain. **`S` agrees
between our extracted netlist and Jane Street's RTL on all 65536 pairs**, on three channels: the
reference implements `a + b == 496` (0 mismatches), our netlist matches the reference (0), our
netlist implements the function (0); both assert on exactly the 15 equating pairs. The comparison is
against the RTL — both designs instantiated in one testbench, one stimulus — not against a formula
restated in Python, and the protocol (width, target, serial order, top module) is **read out of**
`00_source.v` rather than typed in. Gate `check_stepC2.py` → **43/43**; 20/20 gates.

**A real defect in B7's emitter, found the first time anything instantiated the netlist.** First run:
`error: port ``VGND'' is not a port of dut_ours`. `build/warmup.v` put its five reference ports in
the header and declared `inout VGND;` / `inout VPWR;` in the *body* — which is not a port at all, so
the file advertised an interface it did not have and could not be instantiated the way its sibling
can. Jane Street's own `02_netlist_with_power_rails.v` lists `VPWR`/`VGND` in the header, and so does
B6's `build/puzzle.v`; only B7's differed. Fixed (the emitted ports are the reference's, then the
supplies, in the list), regenerated, and **`check_stepB7.py` grew 39 → 40 checks**: a caller is
generated that connects every port the netlist is *documented* to have, and it must compile. Falsified
rather than assumed — the old header makes exactly three checks fail, the new one among them.

**The limit this step measured.** C2's power measurement negates each model the design instantiates
(16 of 18) and re-runs the sweep: **15 caught**, 1 silent (`clkbuf_16`). Of the models C1 is blind
to, **the warm-up instantiates 3 and C2 catches all 3** — the second oracle earning its place. But
the same analysis found, and this is the part worth carrying forward, that **26 of the 66 models are
reached by neither behavioural oracle**: they are the ones C1 cannot see *and* the warm-up never
places, so no stimulus in either design excites them. Union coverage: **C1 37 + C2 3 = 40 of 66**,
named in `recon/derived/c2_power.json`. For the other 26 the evidence stops at B6's truth tables,
which check our Verilog against our *reading of the family names* rather than against the silicon.
**E1 is where that can change** — the same two stages can be re-run against the winning vector, which
is the cheapest remaining coverage win in the project and needs no new machinery.

**C3 — Decomposition.**
*Method* — **amended 2026-09-12 after recon; the acceptance criteria are unchanged.** The original
method ("analyse the netlist graph … a counter is a register whose next-state is an increment of its
own value") was **measured to be unavailable on this design, not merely hard**. Recon result:
connectivity yields **no partition at all** — `0` flops whose `D` is another flop's `Q` directly,
and the transitive cone of every one of the 92 flops reaches **90 of the other 91**, so the
flop-dependency graph is **one component**. Synthesis inserted logic on every flop-to-flop edge, so
none of it can be read off graph shape.
*Amended method:* **behavioural identification on the real stimulus**, with spatial clustering as the
independent cross-check. Instrument the B6 netlist, dump every flop's `Q` across C1's 312-cycle
`example_inputs.vcd` replay, and classify each flop by its temporal signature — a counter bit toggles
with period 2^(k+1); a shift-register bit moves one position per enabled cycle; a comparator bit is
high exactly when its input bundle equals the constant. Blocks are then *named from the waveforms
they produce*. This is strictly better evidence than the original: it is the chip's **own behaviour
on the real waveform**, and it reuses C1's harness (`simulate.py`) instead of new machinery. The
plan's original cross-check (spatial clustering) becomes the corroborating axis — measured, the 92
flops form **8 x-clusters**: 5, 1, 3, 24, 27, 17, 14, 1.
*Recon findings carried into the step:* the design is **single-clock** — the 16 nets that look like
clock phases are all leaves of one distribution tree (`n695` → `clkbuf_16` → 16 × `clkbuf_8`); the 92
flops are 84 `dfrtp_2` + 4 `dfstp_2` + 4 `dfxtp_2`; `rst_n` reaches 88 of them (absent on the 4
`dfxtp_2`); **6 `conb_1` constant cells** each feed a comparison gate (`a22o_2`/`a221o_2`/`or3_2`),
consistent with "a total-ones counter comparing against 22".
*Artifact:* `recon/derived/blocks.json` + `docs/steps/C3.md`.
***Verify:*** all 92 flops are assigned to a named block; a 121-period counter, an 11-period
counter, a total-ones counter comparing against 22, and a shift register are each *found* (not
asserted); the block regions agree with Step 1 and with `layout.png`'s "output generator" box.
*Note on that last clause:* the phrase "the Step 1 spatial clustering" has **no numbered artifact
behind it** — Step 1's dossier records the geometry (173 sites/row, every other row used, cells over
x 10.1–190 µm / y 10.9–288.3 µm) and the `layout.png` hatched box labelled "output generator", but no
clustering. The cross-check is therefore against those recorded facts plus the measured x-clusters;
the plan is amended to say so rather than inventing an artifact to match a phrase.
*If it fails:* report which structural expectation is unmet rather than forcing a label.

*Outcome (2026-09-13):* **PASS**, with one expectation refuted rather than met. All 92 flops are
assigned to exactly one block; the shift register (12 stages, depths 0..11, exact against the input
under both stimuli), the total-ones counter against 22 (6 bits, and it *takes the value 22* during the
winning feed), and an **11-period** counter (`i0855` is bit 0 of `p mod 11`) are all found and
verified. The "**121-period counter**" is **refuted**: the design does not count to 121 in one
register — it tracks the column with a mod-11 counter and reaches 121 = 11 × 11 by the column
wrapping. Two stimuli were required (the reference waveform alone leaves 21 flops with no behaviour
at all); 78 of 92 flops are classified and the remaining 14 are reported as a class with their
measured signatures rather than given a function. Artifact `recon/derived/blocks.json`, gate 23/23.

**C4 — Symbolic region-map decode (Δ5, the headline differentiator).**
*Method:* §6.6. Identify the region-select cone; reduce to a boolean function of the index bits;
evaluate for index `0..120`.
*Artifact:* `recon/derived/c4_partition.json` — the step named `regions.json` in this plan and wrote the
partition under its own name; the two are the same artifact.
> **Deviation (2026-09-13, user instruction).** C4 was executed with the documented **published
> method** — stimulus probing of per-cell counters (`docs/02_known_solution.md`, Stage 8) — instead of
> the symbolic cone reduction Δ5 specifies. Outcome: **no region map**; the per-cell counters are the
> columns and the globals. See `docs/steps/C4.md` R12. Δ5 is therefore **not satisfied** as written,
> and AC6's "recovered independently" needs re-scoping or the map's provenance stated explicitly.
>
> **Outcome (executed 2026-09-13, R19/R20 — this closes the step).** The partition **is** recovered,
> by a third route: sweep a single star through all 121 positions, record every latch's trigger set
> (47 non-empty), and **find** the exact covers of the grid among them. Exactly two covers are
> capacity-2 (eleven classes, two answer stars each): the ragged partition (sizes 4…28) and the eleven
> columns, which is the visible two-per-column rule and adds nothing. The ragged one **pins the
> accepted board as the unique solution** of the visible rules (exhaustive, 715 877 nodes; the visible
> rules alone admit thousands), and it reproduces the "eight columns plus three regions over columns
> 4, 5, 6" shape R16 had found by a different probe.
>
> **And the controls are part of the result, because the obvious test is worthless.** Against C5's
> rejection set the partition rejects 40 of 40 boards — until the control shows that **100% of
> same-shape look-alike partitions also reject every board** (class sizes run to 28 cells, so a valid
> board overloads a big class by construction): the rejection test discriminates 6%. The uniqueness
> test is the only one that bites, and **2 of 40** look-alikes pass it too — a ~5% property, not a
> fingerprint. Together with R16 §3 (the accepted input is provably unique, so no second positive
> example exists), the honest statement is: **a candidate that satisfies every test available to us,
> with the power of each test measured** — not "the map, confirmed".
>
> **Corrected here, not silently:** R19 (15:53–15:59) found the partition but was left unrecorded and
> untracked; it was written up and validated in R20. Its claim that the eleven class flops are R13's
> region counters is withdrawn — they are a different eleven from R18's column counters.
>
> **Gate:** `tools/checks/check_stepC4.py` → **25/25**, and the measurements are machine-made:
> `python -m tools.puzzle region-map` → `recon/derived/c4_partition.json`. Step record: `docs/steps/C4.md`
> R19–R20.

***Verify:*** exactly 11 distinct regions; the map is a **partition** (every position in exactly one
region); exactly 2 stars of capacity per region; the map rendered as an 11×11 ASCII picture reads
**"JS"**; and the function is *total* — no index value leaves a region undetermined or doubled.
*If it fails:* the cone was mis-identified; fall back to reporting the region-select signal's
bit-level truth table and decode from that, still symbolically.

**C5 — Independent corroboration of the region map.**
*Method:* re-derive the map by a second, different route — read the region-select signals during a
single full simulation pass and tabulate by cycle.
***Verify:*** the two maps are identical. Explicitly recorded as *corroboration only*; the method
was C4.
*If it fails:* report the disagreement; it means either C4's cone is wrong or the index→position
mapping is (which is also an AC1-relevant fact).

***Outcome (executed 2026-09-13): PARTIAL, and the part it did is now the load-bearing one.***
C4 had no map for C5 to corroborate, so C5 tested the premise instead and established independence of
it: **39 of 39** boards that satisfy every visible rule are rejected (C5's own probe: 40 of 40), at
message level, and the winning pattern is still accepted as the reference. That settles "a hidden
constraint exists" without knowing anything about the map.

*Carried into the close-out:* C5's boards are now generated by `tools/puzzle/verdict.py`
(`python -m tools.puzzle rejections` → `recon/derived/c5_rejections.json`) because the original probe's
generator depended on Python set iteration order and did not survive being copied — its boards are
re-derived rather than inherited, and the same conclusion holds on our own (39 usable, 0 accepted).
*And its limits are asserted:* R20 measured that 188 of 200 same-shape look-alike partitions reject
every one of those boards too, so C5's evidence supports "a hidden constraint exists" and **nothing**
about which partition it is; `check_stepC5.py` (12/12) fails if that power is ever recorded as
anything stronger.

### Phase D — Solve

**D1 — Constraint model + our solver.**
*Method:* §6.7. Constraints: 22 total, 2 per row, 2 per column, no 8-neighbour adjacency,
2 per region from C4.
*Artifact:* `solve/solutions.json`, `tools/puzzle/solve.py`.
***Verify:*** at least one solution found; every returned grid re-checked by an **independent**
validator that recomputes all five constraints from the grid, and the validator must agree.
*If it fails:* dump the partial assignment where the search exhausted and inspect which constraint
made it unsolvable — an unsatisfiable formulation means C4 or the constraint transcription is wrong.

**D2 — Uniqueness by exhaustive enumeration.**
*Method:* run the search to completion; count.
***Verify:*** **exactly one** solution. Then the second, independently-written enumerator (§6.7)
must agree on both the solution and the total count.
*If it fails:* report the count. More than one solution contradicts AC1's uniqueness and means a
constraint was transcribed wrongly.

**D3 — Load-bearing analysis (contribution, not required for the answer).**
*Method:* enumerate the solutions using only the four mechanical constraints (bounded, with a
reported cap), to quantify how much work the hidden region constraint actually does.
*Artifact:* `recon/derived/solutions.json` — D closed with its record in this file's outcome block and in
`docs/verification.md` §15, not as a separate `docs/steps/D3.md`.
***Verify:*** the count is large and reported honestly with the bound used; the single solution is a
member of that set.
*If it fails:* report the bound hit. This step is analysis; it cannot block the answer.

**D4 — Produce the answer in both bit orders.**
***Verify:*** our solver's output equals `target.FEED_ORDER`; and its reverse equals
`target.WITNESS_AS_PRINTED` — the Step 2 ordering trap, asserted explicitly so it can never be a
false negative.

***Outcome (executed 2026-09-13): D1–D4 PASS — the answer is now derived by us, not reproduced.***
One stage, `python -m tools.puzzle solve` → `recon/derived/solutions.json` (~20 s), because the four
steps share one search and one artifact; the four stage names in the table all run it and print their
section. *(Amendment: the plan named `solve/solutions.json` as D1's artifact; every other derived
artifact lives in `recon/derived/`, which is where the gates and the fault-injection harness look, so
the path is `recon/derived/solutions.json`.)*

The result, for the five constraints (§6.7 plus C4's partition):

| | |
|---|---|
| D1 solver (rows / column pairs / recursion) | **exactly 1 solution**, search complete, 715 877 nodes |
| D2 enumerator (11-bit masks / explicit stack) | **exactly 1 solution**, same board, 32 214 420 nodes |
| the two agree | on the board **and** on the count |
| validation, recomputed from the grid | `tools/target.py`'s own checker: 22 stars, two per row, two per column, 0 adjacent pairs, plus class loads **all 2** |
| D4 | derived vector **== `target.FEED_ORDER`**; its reverse **== `target.WITNESS_AS_PRINTED`**; the board **== the published grid** |
| D3 (bounded) | without the region constraint: **≥100 000 solutions** (cap hit at 1 027 376 nodes) |
| **the discriminator** | with the *column* cover instead: **≥2 solutions** — the visible rule adds nothing, so the published uniqueness result rules that cover out as the hidden rule |

That last row is the sharpest statement the project has about C4's two candidate covers: of the two
capacity-2 covers the netlist's own latches produce, one (the columns) cannot be the hidden rule,
and the other (the ragged partition) is consistent with it — it is the constraint that makes the
answer unique. Uniqueness is asserted **relative to that partition**, and the artifact records which
partition and where it came from, because the partition is a candidate (C4 R20), not a certainty.

*Gate:* `tools/checks/check_stepD.py` → **22/22**, re-running both enumerators live rather than
reading their counts, validating through the Step-1 checker, and asserting both bit orders plus the
bound in D3.

### Phase E — Confirm the answer

**E1 — Simulate the solved vector.**
***Verify:*** `success` is high at cycle **126** (and not before); the `O` stream is
`(`,`*`,` `,`T`,`W`,`O`,` `,`S`,`T`,`A`,`R`,`S`,` `,`)`, i.e. `(* TWO STARS *)`.
*If it fails:* the netlist or solver is wrong — return to Phase B7/C4 respectively.

***Outcome (executed 2026-09-13): 4 of 5 classes, then E2 closed the fifth.*** `success` rises at
cycle 126 on the reference netlist and nowhere else, and the four wrong-input messages reproduce —
including the correction that the constructed "2 per row/column with adjacency" vector answers
`TRY AGAIN`, not `TWO NOT TOUCH`, because it breaks the *hidden* constraint too. **E2 then built the
missing class once C4 s partition existed; see its outcome below.***

*How it was measured:* `success` rises at **cycle 126** with the published `(* TWO STARS *)`, and only
at the measured feed offset 4 of 4..8 (every other offset shifts the grid and correctly says
`TRY AGAIN`) — AC2 and AC3 met. `all_zeros` → `EMPTY SKY`, `all_ones` → `BIG BANG`, another wrong
vector → `TRY AGAIN`, all with `success` low. The fifth class,
`two_per_row_col_but_adjacent` → `TWO NOT TOUCH`, was **not reproducible without the region map**: the
adjacent vector also violates the hidden constraint, and the design answers `TRY AGAIN`. That was
recorded as a named open sub-item rather than worked around, and it is why E2's vector set has to come
from C4's partition.

***

***Outcome (executed 2026-09-13; the step itself is defined below, with the rest of Phase E): all four
classes reproduced — and the step became the map s independent test, which the map passed.*** One
computation, `python -m tools.puzzle confirm` →
`recon/derived/e2_messages.json` (~40 s), gated by `check_stepE2` (20 checks), fault-injected (5
mutations, focused sweep PASS).

The construction is a **two-switch family** of the accepted board: two rows exchange the columns of
their stars, which preserves two-per-row and two-per-column by construction, so what is left to ask
is only whether the swap created an adjacent pair and whether the class caps still hold. Of 189
distinct such boards, **23 are adjacent with every class within capacity** — the E2 inputs — and 156
are adjacent but push a class over capacity, which makes them the control group. A from-scratch
enumeration of the same set was tried first and abandoned: **3 000 000 nodes, zero complete boards**,
because with the class caps in place the space is tight and the pruning fails late.

| input | the design's answer |
|---|---|
| accepted board | `(* TWO STARS *)`, success at cycle 126 |
| **23 boards: adjacent, every class within capacity** | **`TWO NOT TOUCH` — all 23** |
| 8 control boards: adjacent **and** a class over capacity | `TRY AGAIN` (none spells it) |
| E1's hand-built adjacent vector (classes overloaded) | `TRY AGAIN` |
| all-zeros · all-ones · other wrong | `EMPTY SKY` · `BIG BANG` · `TRY AGAIN` |

**Why this is evidence about the map and not a re-run of the closed route.** R16 closed the
accept/reject channel: the accepted input is unique, so there is no second positive example and
look-alike partitions behave identically. This asks through the **message** instead — `TRY AGAIN`
means "something other than adjacency is wrong", `TWO NOT TOUCH` means the reverse — and the design
answers exactly as the partition requires: on 23 boards built to satisfy it, and on 8 built to break
it. Neither direction had ever been observed before: **AC4's fourth message class had never been
reproduced by anyone in this project until the partition existed.**

**The caveat is part of the result, not a footnote.** Every reading is
`TWO?NOT TOUC???` — 11 of the 13 characters of `TWO NOT TOUCH` exact, with **one character
indeterminate**, and it follows **net 806**, the single net B5 found structurally undriven
(`a311o_2.A1`, `a31oi_2.A1`). Forcing that net decides the character — `806=0` gives
`TWO"NOT TOUCH`, `806=1` gives `TWO NOT TOUCJ..` — and **neither tie reproduces the published string
byte-for-byte**, so either the layout ties that net somewhere our extraction missed, or the printed
character is genuinely indeterminate. Recorded as its own finding and asserted by the gate, which
*requires* that both ties differ from the contract string: the awkward part cannot be tidied away.

*(Amendment, 2026-09-13, after F6 — this sentence stated a disjunction that F6 closed.)* The first
branch is **refuted**: no cut over the wire could merge (the only cut that overlaps it and belongs
elsewhere sits on a layer pair the wire does not occupy there), no constant cell is nearer than
9 966 dbu, and the net is recovered by probing its own pin coordinates on a rebuilt engine. The
character is therefore genuinely indeterminate **because the layout leaves the net floating** — the
sentence above is kept as what was known when E2 ran, and `check_stepF6.py` is what keeps the
refutation from quietly ceasing to be true.

*Amendment, recorded:* the plan listed the E2 stage under `simulate` (an iverilog harness); it is
implemented in `tools/puzzle/confirm.py` on the Python instrument instead, for the same reason E1 is,
and the stage table was repointed accordingly. The artifact path is `recon/derived/e2_messages.json`,
not the planned `sim/`, so all derived JSON stays in one place.

*Now machine-checked:* `check_stepE1.py` (**18/18**) re-derives the offset table and the message table
from the netlist through `tools/puzzle/verdict.py` — the numbers originally came from `iverilog` and
now also come from the simulation-free evaluator, identically. `python -m tools.puzzle winning` →
`recon/derived/e1_messages.json`. One claim in the E1 record is **withdrawn**: the "~24 flops make
exactly 2 changes, which revives the per-region-counter reading" reading does not survive R18 (the
counters are the column counters); the measurement stands, the interpretation does not.

**E2 — Wrong-input classes.**
***Verify:*** all-zeros → `EMPTY SKY`; all-ones → `BIG BANG`; a hand-built 2-per-row/col but
adjacent grid → `TWO NOT TOUCH`; a random wrong grid → `TRY AGAIN`; and `success` low in all four.
*If it fails:* the failing class identifies which detector is mis-extracted; report it.
*(Executed 2026-09-13: all four classes reproduced. The construction, the contrast and the one
undriven-net caveat are recorded in the outcome block above, in Phase E's execution notes.)*

**E3 — Acceptance matrix.**
*Method:* one script asserting AC1–AC6 from `tools/target.py` against our own artifacts, producing
a single PASS/FAIL table.
***Verify:*** every criterion accounted for with the evidence quoted in the report.
*(Amended 2026-09-13, after the matrix was built: the clause said "6/6 PASS", which would have forced
AC6 to be called PASS or FAIL when it is neither. The matrix therefore has three states, `PASS` /
`PARTIAL` / `FAIL`, and the agreed state is **5 PASS, 1 declared PARTIAL (AC6), 0 FAIL**. A criterion
may only be `PARTIAL` with its unmet reasons written down, and the gate fails if AC6 is upgraded or if
its unmet list is deleted.)*

***Outcome (executed 2026-09-13): 5 PASS, 1 PARTIAL, 0 FAIL.*** `python -m tools.puzzle acceptance` →
`recon/derived/acceptance.json`, gated by `check_stepE3` (**21 checks**), fault-injected.

| | criterion | status |
|---|---|---|
| AC1 | the exact 121-bit vector | **PASS** — derived by our own search, both enumerators agreeing, both bit orders equal |
| AC2 | `success` at cycle 126 | **PASS** — at offset 4 and at no other offset |
| AC3 | `(* TWO STARS *)` | **PASS** |
| AC4 | the four wrong-input messages | **PASS** — the fourth via E2's 23 boards, and not on the 8 controls |
| AC5 | byte-exact replay of `example_inputs.vcd` | **PASS** — 2808 output bits, 0 mismatches, 0 `x`/`z`, cross-check clean |
| AC6 | region partition, "JS" | **PARTIAL** — recovered and corroborated; not confirmed; "JS" not reproduced |

Two design rules kept the matrix honest. **The yardstick is re-verified inside the report**
(`tools/target.py`: grid ↔ bit vector in both orders, the order distinction non-vacuous, the four
mechanical constraints) so a broken target cannot flatter us; and **the matrix reads artifacts, not the
netlist** — the per-step gates guard the artifacts, this step reports on them, and where a criterion is
not met it says so rather than being reworded until it passes.

**AC6 is the row the gate polices.** It must stay `PARTIAL`, must keep its unmet reasons (the "JS"
reading is not reproduced; the verdict channel cannot distinguish the partition from a look-alike; one
character of the corroborating message follows the design's undriven net), and the report must keep a
non-empty list of what is *not* claimed. Two checks exist purely to fail if that row is ever improved.

**Fault injection found a real hole in this gate, which is the point of running it.** On the first
sweep, 1 of 5 mutations **escaped**: `acceptance: the replay comparison made vacuous` zeroed AC5's
quoted bit count and nothing noticed, because the gate re-derived AC5 from `vcd_replay.json` while
never comparing the matrix's *own* quoted numbers. Four checks were added requiring every number the
matrix displays to equal its source artifact's; the escaped mutation then fired `stepE3`, and the
sweep is clean.

*The one nuance the matrix states rather than hides:* the contract's `two_per_row_col_but_adjacent` row
expects `TWO NOT TOUCH`, and E1's single vector for it answers `TRY AGAIN` — hand-built before the map
existed, it also violates the hidden constraint. The class is reproduced by E2's 23 boards, the
mismatch is explained in the AC4 row, and the gate fails if that explanation is removed.

**E4 — End-to-end reproduction.**
*Method:* a single script that runs S0→E3 from scratch on a clean checkout and prints the result.
***Verify:*** it completes and prints the acceptance table; runtime reported.

***Outcome (executed 2026-09-13): the cold claim holds — 31 of 31 artifacts rebuilt byte-identically in
396 s, acceptance PASS.*** `python -m tools.puzzle reproduce [--plan|--check|--cold]` →
`recon/derived/reproduction.json`, gated by `check_stepE4` (**11 checks**).

**What it does.** The stage list is derived from `cli.STAGES` (29 stages), never duplicated. `--cold`
deletes every **tracked** derived artifact — git is the undo, so only tracked files are touched — runs
the pipeline, then compares each rebuilt file's bytes against `git show HEAD:<path>`. The artifact
records the plan, the per-stage exit codes and the byte comparison, and **no timings**: B3's rule is
that a derived artifact must regenerate byte-identically, and a clock in a file guarantees it will not.
Timings go to stdout (the run log is scratch).

**The run:** 29 stages · 396.2 s · acceptance PASS · **31/31 byte-identical** · one new file (this
artifact itself).

**It found two real defects, and both were fixed rather than documented around.**

1. **Two pipeline inputs were not regenerable.** `recon/inventory.json` (consumed by B1/B2/B5) and
   `recon/vcd_cycles.csv` (C1's independent cross-check, "the Step-1 dossier's own decoding") are
   committed and consumed by the pipeline, and **no stage produced them** — the cold rebuild died at
   B1 with `missing input recon/inventory.json`. A fresh clone could still run (they are committed),
   but the protocol's Step-5 claim is a *reproduction* path, and a pipeline that cannot rebuild its own
   inputs fails it. Fixed with two thin S0.1 stages that call the Step-1 tools' own `main()`, so
   serialisation stays the tools' business; both now regenerate byte-identically.
2. **A hidden dependency plus a silent skip.** C4's controls are measured *against C5's rejection set*,
   but the table ran C4 before C5, and the C4 stage loaded the missing file with `if exists else None`
   — so on a cold tree it produced an artifact **without its `rejection_test` and
   `lookalike_uniqueness` fields**, silently, which is why the first cold run reported
   `c4_partition.json` as the one differing file. Fixed twice over: the table is now ordered by
   *dataflow* (C5 before C4, with a comment explaining that the plan's steps stay C4-then-C5), and the
   missing input is a **hard error** naming the command to run instead of a silent downgrade.

One defect in my own comparison code was found the same way: `git show HEAD:<windows-backslash-path>`
fails with empty output, and the empty hash was then reported as "differs from HEAD too" — a false
diagnosis. Paths are normalised to forward slashes before any git call.

**So the reproduction path is now the real thing:** delete every derived artifact we track, rebuild
from the upstream layout and the puzzle alone, and get the same bytes — with the acceptance matrix
re-printed at the end of the same run.

### Phase F — Hardening (protocol Step 5)

**F1 — Dead code and scratch removal.** Remove unused helpers, superseded scripts and any
`recon/hints`/`recon/renders` leftovers; keep only what the pipeline and its gates need. Drop
`shapely` from the dependency list if §5's assertion holds.
***Verify:*** a search for unused modules and unreferenced functions returns nothing; all gates still pass.

**F2 — Reproduction documentation.** `README.md` with the exact command sequence, expected
output, and runtime; `docs/deps.md` finalised.
***Verify:*** following the README from a clean clone reproduces E4's output.

**F3 — Consistency and style sweep.** One naming convention, docstrings on every public function,
no commented-out code, no TODOs.
***Verify:*** a scan for `TODO`/`FIXME`/`XXX`/commented-out blocks is clean.

**F4 — Final full gate run and freeze.** All gates, in order, from clean.
***Verify:*** every gate PASS; git tag `step5-complete`; working tree clean.

***Outcome (executed 2026-09-13): F1–F4 all green; the suite was 32 gates at that point (its live size
is the README's number, which `check_stepF2` keeps honest); the state was frozen as `step5-complete`,
and the tag has since moved forward with F5 and F6.***

| step | result | what it cost |
|---|---|---|
| F1 dead code | **5/5** | found and removed **two** dead functions; the F1 sweep's own first run **failed** |
| F2 documentation | **9/9** | `README.md` written; two gate bugs and one arithmetic error of mine fixed |
| F3 conventions | **6/6** | four gate bugs fixed; **22** undocumented functions documented |
| F4 freeze | **7/7** | the tag, and the machinery's internal consistency |

**Fault injection: 8 mutations across the three gates, 0 escaped, 0 hermeticity violations, each firing
only its owner** (`--only "^(dead code|readme|deps|style):"`, `recon/scratch/fault_f.txt`). **The first
run failed**, and that is the phase's most useful result: both dead-code mutations **escaped**, because
F1 counted references by *text* and the mutator's own source contains the injected name **as a string
literal** — so the mutation counted as a use of itself. A gate whose entire subject is dead code was
blind to dead code, for the same reason any docstring mention would look like a call. Counting now walks
the AST; the stricter count then found **a second dead function** (`um` in `tools/kl_recon.py`).

**What F1 removed.** The inline duplicate of `capacity2_cover()` in C4's stage (the function was never
called) — re-running C4 afterwards regenerates `c4_partition.json` **byte-identically**, so the refactor
is provably behaviour-preserving — and `um()`. The dependency floor F1 asks for is now measured rather
than asserted: `shapely`, `scipy`, `networkx` and `pandas` were uninstalled (`numpy` stays, because
`matplotlib` requires it), `recon/inventory.json` records the new environment, and `check_step1` — which
used to assert *"shapely available"* from the Step-1 dossier — now asserts **those packages are absent**
(58/58). The dossier's historical record is untouched; what changed is what has to hold now.

**What F2 fixed.** The README it wrote is *executably* checked: every command must be a real stage, the
gate count must equal what the suite discovers (32), the stage count must equal the pipeline (29),
`docs/deps.md` and `requirements.txt` must agree both ways, and no document may be orphaned. Two bugs in
the gate: it compared 29 stages against a table of 30 (which includes `reproduce` itself) and matched
document *file names* instead of stems. One error in the README: the per-phase runtime table summed to
389 s against a 396 s measured total (phase C was understated by 10 s). One judgement call, decided rather
than asked — `docs/C4_region_map_issue_for_review.md` was cited nowhere, and is **linked** rather than
deleted, because deleting a written record is the irreversible option.

**What F3 settled.** The convention is stated as a rule that cannot generate boilerplate: **classes, and
any function over 25 lines, carry a docstring**; the `main`/`check` scaffolding is exempt by name. Four
bugs in the gate first: it banned markers including in its own source, its snake_case rule rejected
`check_stepC4.py`, its "public" test matched mere mentions (~20 innocent helpers flagged), and
`check_recompute.py` was the one gate with a non-uniform verdict line. The rule then found **22**
substantial undocumented functions, all now documented from their actual bodies: **233 documented, 218
short helpers exempt**.

**F4 is deliberately absent from the fault grid** — the one deviation from this plan's own
"every step registers its artifacts" rule, and it is stated rather than quietly skipped: F4's subject is
the *frozen* repository, and fault injection dirties the tree by design, so registering it would make it
fire on every mutation and drown the signal. It has no artifact of its own; the freeze it guards is
declared by running the whole suite.

**Step 5 is closed.** The recovered truth matches the known answer on every criterion we agreed (AC6
declared `PARTIAL`, with its reasons), the repository carries no dead code and no scratch, the
dependencies are exactly what `requirements.txt` says, and the reproduction path is one command that
deletes 31 tracked artifacts, rebuilds them from the upstream layout, and gets the same bytes back.
What remains is Step 6: the writeup.

---

**F5 — Post-review remediation.** The whole project reviewed read-only after the freeze; every finding
either fixed, or accepted with the reason written down.
*Method:* audit the repository, the history, the upstream tree, every artifact's hashes, the pipeline,
the gates and the documentation's numbers; fix each actionable finding with a check behind it so the
same defect cannot return silently.
***Verify:*** every finding has a fix or a recorded decision; the suite passes; the freeze gate accepts
the new state.

***Outcome (executed 2026-09-13): eight findings — five fixed, three accepted with reasons; suite
green; the freeze tag moved onto this commit.***
- **Fixed, and made a check where a check was possible:** the `build/cells.v` provenance stamp (it
  printed puzzle.gds's hash twice and labelled both as artifact hashes — `check_stepF2` now tests every
  hash in `build/*.v` against the file it names); `solutions.json` and `acceptance.json` now carry the
  hashes of what they read (8 hashes, verified current); `recon/vcd_cycles.csv` — a committed derivation
  that **no gate checked at all** — is regenerated byte-for-byte inside `check_stepC1` and has its own
  mutation; `IDEA.md` is linked rather than deleted; the verification log's title and scope line are
  current.
- **Where the review was half wrong, the half it missed mattered:** `S0.1`'s inventory *is* gated
  (`check_step1` regenerates it bit-identically, three mutations), but the cycle table was not — which is
  the fix above, and the reason the review's own claim is corrected here rather than trusted.
- **Accepted with reasons:** uneven per-step documentation depth (the evidence is identical, only the
  shape differs); upstream integrity pinned by commit id plus the hashes inside the artifacts that read
  those files (only three upstream files are read); the ignored scratch tree's size.
- **Fault injection:** 3 new mutations, 0 escaped, each firing its owner — and the `cells.v` one also
  fires `stepB6`, by design: B6 owns "*cells.v* is what the writer emits", F2 owns "the stamp is true".
- The freeze tag moved onto this commit, which the commit message states.

---

**F6 — The undriven net, packaged.** *(Added 2026-09-13, after Step 5 was closed and reviewed.)*
*Goal:* net 806 was the one claim in this repository with no gate behind it. It lived in
`docs/net806.md`, its scripts in the ignored `recon/scratch/` tree, and its decisive experiment was not
even listed in the note's own reproduction block — so a fresh clone could not re-run it, and nothing
would have failed if it stopped being true. F6 promotes the experiment into the pipeline. **The
conclusion does not change; the packaging does**, which is the whole point: a finding that cannot be
re-derived is a paragraph, not a result.
*Method:* `tools/puzzle/net806.py` (stage `net806`) rebuilds the extraction engine from A1/A2, recovers
the net by probing the two pin coordinates B4 recorded rather than trusting the id, files
**every shape on the die** by the net the engine assigns it, enumerates every *cut* overlapping the wire
— a missed merge is the only extraction defect that could hide a driver, so each cut is tested against
the conductors **its own A2 rule joins**, and a cut lying on layers the wire does not occupy is a
legal underlap rather than a merge opportunity — measures the nearest `conb` output pin, counts the
terminals that the chip's own label vocabulary calls drivers, and re-asks the message tie through the
E2 instrument. `check_stepF6.py` re-derives all of it: the geometry by a *second* method (a window
census around the pins instead of the whole-die sweep), the refutation and the constant-cell distance
live, and both ties by forcing net 806 on a re-derived E2 board.
*Artifact:* `recon/derived/net806.json`; stage `net806` in `cli.STAGES` (placed after `confirm`,
because it reads E2's artifact, and before `acceptance`, which E4 requires to close the plan).
***Verify:*** `python -m tools.puzzle net806` writes the artifact byte-identically on repeated runs;
`tools/checks/check_stepF6.py` passes with every number re-derived rather than read; the fault grid shows
both net806 mutations caught by `stepF6` and nothing else; the full suite is green; and
`python -m tools.puzzle reproduce --cold` rebuilds the artifact from the upstream layout at the new
stage count.

***Outcome (executed 2026-09-13): PASS — 21/21 gate checks, the artifact tracked and byte-reproducible,
three mutations across two gates each firing only its owner, and the finding itself unchanged.*** The
stage reproduces `docs/net806.md` exactly: **17 shapes on five `(layer, datatype)` pairs** (met1/met2/met3
and two cut types — the note's "four layers" was a miscount, now stated correctly), bbox
`(174895, 90450)–(179330, 91875)`, four of its own vias, **one cut overlapping the wire that belongs to
another net** (68/44 → net 766) whose rule joins 68/20+69/20 while the wire is on met1 there, so
**zero cuts could merge** and no missed merge hides a driver; the nearest `conb` output is
**9 966 dbu** away; the die census re-measures B3's **40 360 conductor shapes / 2 626 nets**; and the
message tie is re-asked live — `TWO?NOT TOUC???`, `806=0` → `TWO"NOT TOUCH`, `806=1` →
`TWO NOT TOUCJ..`, **neither equal to the published string**. Full record: `docs/verification.md` §24.

**The most valuable thing F6 did was break E4's evidence.** Running the documented command was the point
of the step, and on its first honest run it printed `32 of 33 regenerated byte-identically / DIFFERS:
recon/derived/reproduction.json (not regenerated)` — because the report is written *after* the cold
comparison, so any run that deletes it can never rebuild it. `check_stepE4`'s `differences == []` had
been passing on a report **older than its own subject being tracked** (it records `tracked_before: 31`,
an empty difference list, and lists `reproduction.json` among `untracked_new_files`): a gate certifying
the reproduction claim with evidence a fresh run of that claim cannot produce. Fixed at the source —
`repro.derived_files()` excludes the report with the measurement in its docstring, the run prints the
exclusion, the artifact records it as `cold_check.report_excluded`, and `check_stepE4` now asserts both,
with the mutation `reproduction: the report s own exclusion dropped` behind it.

**The documented command, re-run after the fix** (`recon/scratch/cold_f6.txt`): `removed 32 tracked
derived files`; `32 of 32 regenerated byte-identically`; `30 stages, 420.7s total, acceptance PASS`;
`REPRODUCTION: PASS`. **Fault injection** (`--only "net806:|report s own exclusion"`,
`recon/scratch/fault_f6.txt`): **3 of 129 mutations, 0 escaped, 0 hermeticity violations**, each firing
only its owner — `stepF6` for both net806 mutations, `stepE4` for the report one — baseline all-zero,
`artifacts restored: True`. **Full suite: 33 gates; `stepF4` fails only while the step is uncommitted
(it asserts a clean tree at HEAD), which is why the freeze is re-declared at the end of the step.**

## 8. Risk register with kill criteria

| # | Risk | Impact | Detection | Mitigation / fallback |
|---|---|---|---|---|
| R-1 | KLayout's connectivity engine cannot be driven to a usable netlist from the pip wheel | Blocks Phase B | B2 spike | Fallback B2′ (own shape-graph extractor). Either outcome is a documented result; the published work left this question open, so we answer it either way |
| R-2 | In-master pin labels are insufficient (a pin with no label, or a label with no geometry) | Blocks Δ2 | A4/A5 | Report precisely which pins; supplement only for those, using the same geometry logic seeded from the cell's *name-derived* expected pin set — and record the exception |
| R-3 | Our behavioural models are subtly wrong (polarity, edge) | C1 catches 37 of 66 by measurement; the other 29 pass silently | C2 warm-up equivalence, exhaustive over every input vector | C1 quantifies why C2 exists rather than asserting it; D/E also cross-check |
| R-4 | Region-select cone is not a pure function of the index (depends on state) | C4 fails | C4 "function is total" check | Decode as a function of (index, state-bits) and report the extra dependence; still symbolic |
| R-5 | The region constraint is not "2 per region" as published | D1 unsatisfiable or D2 count ≠ 1 | D1/D2 | Re-derive the constraint from our own C4 map and report **our** count — if our map says something different from the published prose, that is itself a finding |
| R-6 | Simulation too slow in `iverilog` | E slow | E1 timing | 121-cycle runs are small; escalate to Verilator-in-WSL only if measured slow, and only as a simulator change |
| R-7 | Net-naming nondeterminism | B2/B7 comparisons flaky | gate flakiness | Canonicalise nets by structure (sorted terminal sets), never by name, in every comparison |

**Global kill criterion:** if Phase B7 cannot be passed, nothing downstream is trustworthy and we
stop and report rather than proceeding on an unvalidated netlist.

---

## 9. Gate design

- `tools/checks/check_step<StepId>.py` for each executed step; exit 0 iff all its checks pass.
- Every gate **re-runs all earlier gates** (protocol requirement), so drift is caught immediately.
- Gates assert **values**, not absence of errors: counts, exact constants, specific identities.
- A gate that cannot run must **fail or SKIP loudly** — never pass silently.
- Every gate prints evidence suitable for pasting into the step report.
- **A gate re-derives what it asserts, or says it is reading an artifact.** Added 2026-09-13 after
  C4/C5/E1 were found to have no gates at all: `check_stepC4/C5/E1` re-run the measurements they
  assert (the evaluator against the VCD, the cone census, the two differing flops, the trigger sets at
  sampled cells, the exhaustive counts, the controls), re-generate the step's inputs, and compare the
  **committed artifact** against what they just derived — so a stale artifact fails rather than
  certifying itself. The expensive sweeps (~100 s) stay in the stage; the gate re-derives a
  deterministic sample of them and says so in its output.
- **Each gate must state its own power.** Added 2026-09-13 (C1 §"model-power", C4 R20): a comparison
  count is not strength, and a test can pass on a look-alike. Where a check *could* pass vacuously,
  the gate prints the control that bounds it (e.g. "188 of 200 look-alike partitions also pass").

**check_step3.py** (this plan's own gate) verifies the plan is internally sound:
1. every `S?`/`A?`/`B?`/`C?`/`D?`/`E?`/`F?` step ID in §7 appears exactly once and has a
   *Verify* clause;
2. the prohibited list in §2 matches `docs/02_known_solution.md` §8 exactly;
3. each declared differentiator Δ1–Δ6 names at least one step that exists;
4. the traceability matrix in §10 covers all six acceptance criteria;
5. the dependency policy is consistent with `requirements.txt`;
6. **no planned artifact path lies inside `asic-puzzle-2026/`**.

---

## 10. Traceability — acceptance criterion to steps

| Criterion | Steps responsible | Gate |
|---|---|---|
| AC1 exact 121-bit vector | C4 → D1 → D2 → D4 (**derived**, 2026-09-13) | `check_stepD` (22) — two enumerators agree, and both bit orders match the contract |
| AC2 `success` at cycle 126 | B5, B6, C1 → E1 → **E3 (matrix)** | `check_stepE1` (20), `check_stepE3` (21) |
| AC3 `(* TWO STARS *)` | B5, B6, C1 → E1 → **E3 (matrix)** | `check_stepE1` (20), `check_stepE3` (21) |
| AC4 four wrong-input messages | B5, B6 → E1 → **E2 (all four, 2026-09-13)** | `check_stepE1` (20), `check_stepE2` (20) — `TWO NOT TOUCH` reproduced on 23 constructed inputs, and *not* on the 8 controls |
| AC5 byte-exact VCD replay (semantic equality at every sampled instant, `x` included) | B7, C1 → **E3 (matrix)** | `check_stepC1`, `check_stepE3` (21) |
| AC6 region partition, "JS" | C4 (R19/R20), C5 as corroboration, E2 (message) | `check_stepC4` (25), `check_stepC5` (12), `check_stepE2` (20), `check_stepE3` (21) — **PARTIAL by design**: recovered + corroborated, not confirmed, "JS" not reproduced |
| Netlist correctness (unstated but load-bearing) | A1–A5, B1–B7 | `check_stepA`, `check_stepB` |

---

## 11. What we will not claim

Recorded now so the writeup in Step 6 cannot drift into overreach:

1. We do **not** claim our approach is faster than the published one. It is more *self-contained*
   (no PDK) and more *explanatory* (the region logic is read, not bypassed). Speed is not the claim.
2. We do **not** claim to have solved the puzzle competitively — submissions closed 2026-09-04.
3. We do **not** claim our netlist is transistor-level. It is gate-level, and the GDS preserves
   master names; that is an honest description of the problem, not a shortcut we invented.
4. We do **not** claim the two Step 2 contradictions prove the published work wrong overall — they
   are two specific claims that do not reproduce, and we say exactly that.
5. We will report the fallback if B2 fails, and we will not present a fallback path as if it were
   the planned one.
6. **We do not claim the region map is confirmed; we do claim the chip corroborates it.** We claim a
   partition recovered from the design's own latches, with every test we can put to it passed and the
   *power of each test measured*. Three limits, all stated: the chip's **verdicts** cannot distinguish
   it from a look-alike partition (R20 control 1: 188 of 200 look-alikes behave identically; R16 §3:
   the accepted input is unique, so there is no second positive example); the **message** channel does
   corroborate it (E2: `TWO NOT TOUCH` on all 23 boards built to satisfy the partition, and not on the
   8 built to break it) but it is a sample near the answer, not the whole space, so a look-alike that
   agrees on those 31 boards is not excluded; and the one character the design's undriven net leaves
   open is reported rather than resolved — F6 closed the *question about that net* (it is genuinely
   undriven; three hypotheses tested and refuted) while leaving the character undecided, because a
   design cannot depend on a node the layout does not drive. "Recovered by our own method,
   corroborated by the chip's message, with its limits measured" is the claim; "the map, proven" is
   not. *(Rewritten 2026-09-13 after E2; the earlier wording said the chip could not corroborate it at
   all, and the clause about the undriven net was extended after F6.)*
7. **We do not claim Δ5 as written was achieved.** The symbolic cone reduction was refuted, not
   abandoned: there is no region-select cone. We do claim the replacement, and we say in the writeup
   that the original differentiator failed and why. *(Added 2026-09-13.)*
8. **We do not claim the published work is wrong about the puzzle.** We claim two specific things
   about its description of this netlist: it states per-region counters and a counter wrapping at 121,
   and this netlist contains the eleven **column** counters and a mod-11 counter that reaches 121 by
   wrapping; and its Stage-8 method (probe one cell at a time, watch the region counter fire) targets
   objects that are not present here, which is why running it faithfully produced columns and not
   regions. *(Added 2026-09-13.)*
