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
| AC6 | region map recovered independently, spelling "JS" | 11 regions, capacity 2 each |

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
| **Δ5** | **The hidden region map is decoded by analysing the netlist symbolically** — the region-select cone is reduced to a function of the grid index and evaluated, no stimulus | C4 | Their self-declared limit #3/#4: the `magic_index` LUT was *never decoded* and the map was recovered by probing (P5). We read the logic. This converts "a solver found a satisfying assignment" into "we know what the chip computes" — the difference the blog itself asks for. |
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
    analyse.py        # C3     structural decomposition
    regions.py        # C4     symbolic region-LUT decode (Δ5)
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

### 6.6 Region decode (C4)
Locate the region-select logic, then reduce it symbolically: take the select cone, express it as a
boolean function of the index register's bits, and evaluate that function for each index constant
`0..120`. Output: position → region id. **No simulation and no stimulus** is used to derive it.
Validation: it must be a partition, produce exactly 11 regions, and assign exactly 2 stars' worth
of capacity per region. C5 re-derives it once by an independent route purely as corroboration.

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
*If it fails:* revert the import change; keep loose modules and a plain `tools/puzzle_cli.py`.

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

**B4 — Net ↔ (instance, pin) mapping.**
*Method:* transform A4 pin rectangles by B1 transforms; assign each pin to a net by following its
geometry into the routing stack through the via instance that lifts it there (§6.5 amendment) —
not by same-layer overlap, which A5 ruled out.
*Artifact:* `recon/derived/pin_net.json`.
***Verify:*** every functional pin of every instance is assigned exactly one net; count of
unassigned pins is reported (and must be 0 for functional pins; a non-zero count with an
explanation is a failure, not a footnote).

**B5 — Netlist integrity checks.**
*Method:* build the netlist object and run the checks.
***Verify:*** single-driver rule holds (each net has ≤1 driver, drivers inferred structurally);
no floating inputs; every net has ≥2 terminals except the 13 top-level port nets; the 13 port nets
match the Step 1 label set exactly; power/ground carry only VPWR/VGND pins; total instance count
and total pin count are reported against expectation.
*If it fails:* the failing check names the offending net/pin — debug that, not the whole design.

**B6 — Emit structural Verilog + behavioural models.**
*Method:* `emit.py` writes `build/puzzle.v` (structural, our own net naming); `cells.py` writes
`build/cells.v`, our behavioural models for the 69 cell types, with semantics grounded in the label
names extracted in A3 (`RESET_B`/`SET_B` polarity read from the artifact, not from documentation).
*Artifact:* `build/puzzle.v`, `build/cells.v`.
***Verify:*** `iverilog -t null` compiles both cleanly (no implicit nets, no undriven wires);
instance count in the Verilog equals B5's; every cell type used has a model.
*If it fails:* compile errors localise themselves; a missing model is a `cells.py` omission.

**B7 — Warm-up end-to-end regression (the Phase B gate).**
*Goal:* our whole pipeline must reproduce a design whose correct netlist we possess.
*Method:* run S0→B6 on `warmup/04_final.gds`; compare against `warmup/01_netlist.v`.
*Artifact:* `docs/steps/B7.md` with the comparison report.
***Verify:*** same instance count; **same bipartite connectivity graph up to net renaming**; pin
names match. This must be byte-for-byte the same comparison method as B2, so B2 and B7 agree.
*If it fails:* **stop the phase.** Do not proceed to Phase C. Debug here — this is the single
highest-value gate in the project, because every later stage inherits this answer.

### Phase C — Understand the chip

**C1 — VCD replay regression.**
*Method:* `simulate.py` runs `iverilog` on `build/` with a testbench reproducing 3 reset / 1 idle /
121 feed / stream-out, twice, per the VCD; compare to the Step 1-derived reference.
***Verify:*** `O` byte stream matches `TRY AGAIN`, `NUL`, `TRY AGAIN`, `NUL` exactly, at the same
cycles (125…134, 281…290); `success` stays low for all 312 cycles.
*If it fails:* this is the second oracle. A mismatch here with B7 passing means the *models* are
wrong, not the netlist — bisect by comparing our `O[]` driver cones against the VCD's observed
`O` transitions.

**C2 — Warm-up functional equivalence (validates the cell models independently).**
*Method:* simulate our extracted warm-up netlist with our models, applying `warmup/00_source.v`
semantics: shift A and B in, then check `S` high iff `A + B == 496`.
***Verify:*** for an exhaustive or randomised sweep of `(A, B)` pairs, our netlist's `S` equals
`A + B == 496`. This is the check that makes our *models* trustworthy rather than name-guessed.
*If it fails:* the failing cell type is identified by which bit pattern breaks; fix that model.

**C3 — Structural decomposition.**
*Method:* analyse the netlist graph — sequential elements, their clocks/resets, feedback cycles,
comparators against constants, and the ROM block. Label blocks by function using evidence (e.g. a
counter is a register whose next-state is an increment of its own value).
*Artifact:* `recon/derived/blocks.json` + `docs/steps/C3.md`.
***Verify:*** all 92 flops are assigned to a named block; a 121-period counter, an 11-period
counter, a total-ones counter comparing against 22, and a shift register are each *found* (not
asserted); the block regions agree with the Step 1 spatial clustering and with `layout.png`'s
"output generator" box.
*If it fails:* report which structural expectation is unmet rather than forcing a label.

**C4 — Symbolic region-map decode (Δ5, the headline differentiator).**
*Method:* §6.6. Identify the region-select cone; reduce to a boolean function of the index bits;
evaluate for index `0..120`.
*Artifact:* `recon/derived/regions.json`.
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
*Artifact:* `docs/steps/D3.md`.
***Verify:*** the count is large and reported honestly with the bound used; the single solution is a
member of that set.
*If it fails:* report the bound hit. This step is analysis; it cannot block the answer.

**D4 — Produce the answer in both bit orders.**
***Verify:*** our solver's output equals `target.FEED_ORDER`; and its reverse equals
`target.WITNESS_AS_PRINTED` — the Step 2 ordering trap, asserted explicitly so it can never be a
false negative.

### Phase E — Confirm the answer

**E1 — Simulate the solved vector.**
***Verify:*** `success` is high at cycle **126** (and not before); the `O` stream is
`(`,`*`,` `,`T`,`W`,`O`,` `,`S`,`T`,`A`,`R`,`S`,` `,`)`, i.e. `(* TWO STARS *)`.
*If it fails:* the netlist or solver is wrong — return to Phase B7/C4 respectively.

**E2 — Wrong-input classes.**
***Verify:*** all-zeros → `EMPTY SKY`; all-ones → `BIG BANG`; a hand-built 2-per-row/col but
adjacent grid → `TWO NOT TOUCH`; a random wrong grid → `TRY AGAIN`; and `success` low in all four.
*If it fails:* the failing class identifies which detector is mis-extracted; report it.

**E3 — Acceptance matrix.**
*Method:* one script asserting AC1–AC6 from `tools/target.py` against our own artifacts, producing
a single PASS/FAIL table.
***Verify:*** 6/6 acceptance criteria PASS with the evidence quoted in the report.

**E4 — End-to-end reproduction.**
*Method:* a single script that runs S0→E3 from scratch on a clean checkout and prints the result.
***Verify:*** it completes and prints the acceptance table; runtime reported.

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

---

## 8. Risk register with kill criteria

| # | Risk | Impact | Detection | Mitigation / fallback |
|---|---|---|---|---|
| R-1 | KLayout's connectivity engine cannot be driven to a usable netlist from the pip wheel | Blocks Phase B | B2 spike | Fallback B2′ (own shape-graph extractor). Either outcome is a documented result; the published work left this question open, so we answer it either way |
| R-2 | In-master pin labels are insufficient (a pin with no label, or a label with no geometry) | Blocks Δ2 | A4/A5 | Report precisely which pins; supplement only for those, using the same geometry logic seeded from the cell's *name-derived* expected pin set — and record the exception |
| R-3 | Our behavioural models are subtly wrong (polarity, edge) | C1/C2 fail or silently pass | C2 warm-up equivalence over many `(A,B)` | C2 exists specifically for this; D/E also cross-check |
| R-4 | Region-select cone is not a pure function of the index (depends on state) | C4 fails | C4 "function is total" check | Decode as a function of (index, state-bits) and report the extra dependence; still symbolic |
| R-5 | The region constraint is not "2 per region" as published | D1 unsatisfiable or D2 count ≠ 1 | D1/D2 | Re-derive the constraint from our own C4 map and report **our** count — if our map says something different from the published prose, that is itself a finding |
| R-6 | Simulation too slow in `iverilog` | E slow | E1 timing | 121-cycle runs are small; escalate to Verilator-in-WSL only if measured slow, and only as a simulator change |
| R-7 | Net-naming nondeterminism | B2/B7 comparisons flaky | gate flakiness | Canonicalise nets by structure (sorted terminal sets), never by name, in every comparison |

**Global kill criterion:** if Phase B7 cannot be passed, nothing downstream is trustworthy and we
stop and report rather than proceeding on an unvalidated netlist.

---

## 9. Gate design

- `tools/checks/check_stepN.py` for each executed step; exit 0 iff all its checks pass.
- Every gate **re-runs all earlier gates** (protocol requirement), so drift is caught immediately.
- Gates assert **values**, not absence of errors: counts, exact constants, specific identities.
- A gate that cannot run must **fail or SKIP loudly** — never pass silently.
- Every gate prints evidence suitable for pasting into the step report.

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
| AC1 exact 121-bit vector | C4 → D1 → D2 → D4 | `check_stepD` (equality with `target.FEED_ORDER`) |
| AC2 `success` at cycle 126 | B5, B6, C1 → E1 | `check_stepE` |
| AC3 `(* TWO STARS *)` | B5, B6, C1 → E1 | `check_stepE` |
| AC4 four wrong-input messages | B5, B6 → E2 | `check_stepE` |
| AC5 byte-exact VCD replay | B7, C1 | `check_stepC` |
| AC6 region map, "JS" | C4, C5 | `check_stepC` |
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
