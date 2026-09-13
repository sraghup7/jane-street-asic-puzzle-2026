# Verification audit — Phases A, B and C

**Date:** 2026-09-12 · **Scope:** every step executed up to and including C2
**Question asked:** are the executed steps solid enough to build the remaining steps on?
**Answer:** yes — after fixing three defects found in the Phase-A audit, a fourth found in B5, and
the Phase-B review's findings (§10), which are coverage gaps rather than wrong data.

§1–§7 are the Phase-A audit as first written. §8, §9 and §10 were added as later steps turned up
further findings; §4's header now reflects the current matrix size while its table stays frozen as
the Phase-A run.

---

## 1. Method

Re-running the gates would only have re-asserted the gates' own opinion, so the audit was
adversarial. Five independent lines:

| # | Test | What it establishes |
|---|---|---|
| 1 | Wipe every derived artifact, rebuild from raw, compare sha256 | the artifacts are reproducible from the artifacts we hold |
| 2 | Inject one wrong-but-well-formed value at a time; run every gate | each gate actually bites |
| 3 | Same, but one gate at a time with a pristine restore between | per-gate sensitivity, not masked by ordering |
| 4 | Re-derive the load-bearing numbers with from-scratch code | the artifacts are not merely self-consistent |
| 5 | Assert that running a gate does not change any artifact | a gate may not destroy the evidence it found |

---

## 2. Reproducibility — PASS

All five derived artifacts and both Step-1 artifacts, deleted and rebuilt from `puzzle.gds`
alone, reproduce **byte-identically** (sha256 before/after equal). Whole phase-A rebuild:
**3.5 s**. Working tree clean afterwards; `asic-puzzle-2026/` untouched throughout.

An independent corroboration turned up along the way: A1 derives the site pitch as **0.46 µm**
from `puzzle.gds`'s own drawn cell widths, and the warm-up's DEF states its rows independently
as `DO 173 BY 1 STEP 460` — i.e. 0.46 µm. Two unrelated sources, same number. No gate asserted
this before; `check_recompute.py` now does.

---

## 3. Findings

### F1 — `roles` and `summary` in `layers.json` were unchecked (high)

Moving layer `67/20` out of `roles.local_wire` into `roles.non_electrical` fired **no gate**.
Neither did corrupting `summary.by_role`. Both are derived data written into the artifact, and
the gate checked neither against the thing they derive from: the table was only checked to be a
*partition* (still true after the edit), and the summary was not checked at all.

Impact: `roles` is the block a consumer reads. It could disagree with `pairs[].role`, or with
reality, and every gate would still pass. Fixed by 6 new A1 checks (role-table ↔ pair bijection
both ways; summary pair count, histogram, review list and per-pair review flag).

### F2 — the phase-A gates were not hermetic (high, and it was hiding F1)

A3/A4/A5 proved "the artifact is what the code produces" by capturing the artifact, **re-running
the stage over it**, and comparing. The regenerability check was real, but it rewrote the file
while doing so.

Measured consequence: corrupt `pin_names.json`, run the whole suite, and only `stepA3` fails.
Run `check_stepA4` **alone** on the same corruption and it fails too — 38 passed, 1 failed. The
A3 gate had already rewritten the file before A4 looked. Because `run_all.py` runs gates in
dependency order, every downstream gate was blind by construction, and a failing gate repaired
the artifact instead of leaving it for inspection.

Fixed by `tools/checks/_regen.py`: the stage runs with its output path redirected to scratch, so
the gate compares produced bytes to committed bytes **without touching the tree**. Each gate now
also asserts the tree was untouched, so hermeticity is itself regression-protected.

### F3 — platform-dependent artifacts (low, but it invalidated a stated convention)

`.gitattributes` pins the repo to LF, and an earlier fixup commit applied that to `layers.json`
— but `tools/inventory.py` and `tools/vcd_probe.py` still wrote CRLF. `recon/inventory.json` had
969 CRLF line endings and `recon/vcd_cycles.csv` 313. Git's clean filter hid it from `git diff`,
so the on-disk bytes differed from the blob by platform. Both writers now pin LF; both artifacts
were regenerated.

Also fixed: `tools/puzzle/cli.py`'s `--gates` table listed 4 gates while `run_all.py` discovered
10. The table is now *derived* from `run_all.gate_paths()`, so it cannot drift again, with
one-line notes in a dict that prints `-` for anything unlisted.

---

## 4. The fault matrix

The table below is this audit's original run — **21 mutations × 11 gates** — kept as the Phase-A
record. **The current matrix is 84 mutations × 21 gates**, re-run after B7 and after fixing the
fault-injector bug in §10 F2: 0 misses, 0 hermeticity violations, `artifacts restored: True`.
Every cell is an isolated measurement with a pristine restore between probes, and the "fired"
column is the real output of `tools/checks/fault_inject.py`:

> The table below is frozen at the Phase-A run; the tool is the authority for the current grid.
> It grew 21 × 11 → 33 × 13 at B2 → 54 × 17 at B6 → 60 × 18 at B7, and every increase has held at
> 0 misses and 0 hermeticity violations.

| injected fault | gates that fired |
|---|---|
| `layers`: role table moved li1 → non_elec | stepA1 |
| `layers`: a pair's own role field flipped | stepA1, stepA2, stepA4, stepA5 |
| `layers`: site pitch 0.46 → 0.55 | stepA1, recompute |
| `layers`: pin datatype 16 → 20 | stepA1 |
| `layers`: summary role histogram wrong | stepA1 |
| `layers`: summary review list emptied | stepA1 |
| `via_pairs`: rule bridges li1 → met2 | stepA2, stepA4, recompute |
| `via_pairs`: via instance total +1 | stepA2 |
| `pin_names`: nand2 loses pin A | stepA3, stepA4, stepA5 |
| `pin_names`: pin label total +1 | stepA3, stepA5 |
| `pin_names`: per-layer label count +1 | stepA3, recompute |
| `pinmodel`: nand2 pin A loses a rect | stepA4 |
| `pinmodel`: nand2 bbox widened | stepA4 |
| `pinmodel`: nand2 loses pin B | stepA4, stepA5 |
| `pinmodel`: local rule instance count +1 | stepA4 |
| `pin_coverage`: routed 489 → 490 | stepA5 |
| `pin_coverage`: instance pct 87.69 → 90 | stepA5 |
| `inventory`: std cell instance count +1 | step1, stepA2, recompute |
| `inventory`: structure count 81 → 82 | step1, stepA1, recompute |
| `inventory`: warmup DEF site width 0.46 → 0.47 | step1 |
| `inventory`: GDS source hash blanked | step1 |

```
mutations with no gate firing : 0
hermeticity violations        : 0
gates that never fired        : ['target', 'hygiene', 'step2', 'step3']
artifacts restored            : True
FAULT INJECTION: PASS -- every injected fault was caught, nothing rewrote it
```

Four gates never fire on artifact mutations, and that is correct for all four: `target`
validates the acceptance contract, `hygiene` scans source code, `step2` checks a narrow set of
inventory facts about the known-solution study, and `step3` validates the plan text. Every gate
that owns a derived artifact fires on at least one mutation of it. Reproduce with:

```
.venv/Scripts/python tools/checks/fault_inject.py
```

Note the ordering effect this matrix documents: the row *`pin_names: nand2 loses pin A`* fires
A3, A4 and A5 when each is measured in isolation, but only A3 when the suite is run in order —
which is precisely why per-gate isolation is the measurement that matters.

---

## 5. Independent recomputation — PASS (41/41)

`tools/checks/check_recompute.py` re-derives the load-bearing facts from the raw GDS with its own
code, importing nothing from `tools/puzzle` and reusing none of the check helpers. It is the only
gate in the suite that can catch a pipeline that is *self-consistently wrong*. It confirms,
independently: 41 `(layer, datatype)` pairs with the 33 + 8 label split; 1618 / 8221 / 36
placements; 69 masters; all 1618 placements on the site and row pitches; the 9 via masters, their
cut by least-area (with the one genuine tie), the enclosure invariant by exact polygon
intersection, and the derived rule set matching the committed one; 803 pin labels + 73 cell-name
labels with the per-layer split; the 68-of-69 `236/0` marker; and the warm-up.

### Two facts it validates that Phase B depends on

1. **The warm-up's masters are geometrically identical to the puzzle's** (same polygon signature,
   layer and vertex count, per master) — including the via masters. So A4's pin model transfers to
   the warm-up without re-derivation, which is what makes B7 a fair regression.
2. **The placement transforms recovered from the GDS match the warm-up DEF's own orientation
   tokens count for count** (`N`/`FS`/`FN`/`S`). B1's transform convention is therefore already
   partly validated against an independent file — the DEF is a ground-truth placement oracle that
   `docs/03_our_plan.md` §6.4 does not currently use, and B1 should.

### And one convention trap, found by this gate failing

**gdstk returns coordinates in microns (floats), not database units.** The first draft of this
gate divided by `dbu_per_um` and got 198 distinct "site-grid residues"; the artifacts were right
(`site_residual_histogram` is `{"0.0": 1618}`). A4's artifacts store `*_um` fields for the same
reason. B1 must convert **once at read time** (`round(v * dbu_per_um)`) and stay integral after,
per `docs/03_our_plan.md` §5 convention 1. This is exactly the trap that convention was written
to pre-empt, and it is now asserted rather than described.

### A geometry subtlety worth recording

8 of the 9 via masters name their cut by least area. The 9th, `VIA_L1M1_PR_MR`, **ties**: its li1
landing pad and its mcon cut are drawn to the identical 0.17 µm square. A per-master heuristic
needs a tie-break; A1's global role derivation needs none. That is evidence for A1's method, and
it is why the rule set is derived from the whole file rather than master by master.

---

## 6. What this audit does *not* establish

1. **Most A3 "cross-checks" are the stage's own self-report.** The gate asserts that the stage
   reported `passed: true` for its 15 internal consistency checks. `check_recompute.py` now
   independently re-derives A3's per-layer label counts and the 803/73 split, but A3's 15
   internal checks themselves remain self-reported. Acceptable, and now partly backstopped —
   worth knowing when reading A3's gate output.
2. **No end-to-end functional test exists yet.** Everything above is structural. B7 reproduces
   the warm-up netlist and compiles the models against it, but it does **not** simulate: it
   establishes structure and interfaces, not behaviour — and the earlier wording here, calling B7
   "the first functional oracle", was wrong (§10 F1). The functional oracles are C1 (byte-exact
   VCD replay) and C2 (the warm-up adder), and nothing here substitutes for them.
3. **Phase B's hard problem is untouched by this audit.** Whether connectivity can be extracted
   at all is B2's spike, not something Phase A's gates speak to.

## 7. Carried into Phase B

1. **The vias are part of the net.** No pin in any master has geometry on met2–met5; li1 carries
   all 489 routed pins and met1 only 20. Since li1 carries no top-level routing, a cell pin and its
   wire are not on the same layer — they are joined by one of 2696 via instances. `§6.5`'s
   "matched to net geometry by exact overlap on the pin's own layers" is inconsistent with that
   finding and should be amended before B3/B4.
2. **B1 should use the warm-up DEF as a placement oracle.** 230 components with explicit
   orientations, agreeing with the GDS 230 and the netlist's 230 instances.
3. **51 of 69 cell types have no independent functional oracle — partly addressed since.** The
   warm-up exercises 18, so C2 validates those 18 against the adder's own arithmetic; the other 51
   are otherwise covered only indirectly by C1's whole-chip VCD replay, where a single wrong model
   is hard to localise. The Phase-B review narrowed this (§10): a truth-table oracle now checks
   **all 62 combinational models over every input vector** and all 3 sequential ones by directed
   test, against a reference derived independently from the family names. That establishes the
   *Verilog* says what we intended; it does not establish that our reading of a family name
   matches real silicon, which stays C1's business.
4. **`recon/scratch/` is not committed** (gitignored): it holds the audit's scratch output. F1
   removes it at the end like every other scratch directory.

---

## 8. Post-audit finding: `cluster_id` is not a global net key (found during B4)

Recorded here because it is the same bug class as §4 and it invalidated reasoning in two steps.

**What happened.** B4's first run reported the `VPWR` net carrying 15 functional pins, all of
them `clkbuf_4.X` — a power net driving nothing, i.e. a short. It was not a short.

**Why.** In the hierarchical netlist, `probe_net(point)` returns the net of the **cell that owns
the shape**, and `cluster_id` is unique only **within one circuit**. Measured on the full die:

```
X    pin point -> cluster_id=2  circuit='sky130_fd_sc_hd__clkbuf_4'  0 subcircuit pins
VPWR pin point -> cluster_id=2  circuit='puzzle'                 2590 subcircuit pins
nets sharing cluster_id == 2, across all circuits: 70
```

Any map keyed on `cluster_id` from hierarchical probes therefore merges unrelated nets.

**How it was confirmed.** Two independent measurements agree once the layout is flattened
(one circuit): X = 683 and VPWR = 27, two different nets. The same pair of numbers comes from a
±100 µm clipped-window extraction — which is how the collision was caught, because a purely local
result kept contradicting the whole-die one.

**Fix.** `connect.build_engine` flattens before extracting (`ly.flatten(top, -1)`). One circuit ⇒
2626 nets with 2626 distinct cluster ids. Side effects: `subcircuit_pin_count()` is 0 for every
net under flattening, so B3 ranks nets by polygon count instead; and the hierarchical run's
evidence that 9839 subcircuits = 1618 cells + 8221 vias is no longer reproducible by any gate.

**Two API facts worth not rediscovering.**

1. `LayoutToNetlist` **refuses a clipped iterator** — *"The netlist extractor cannot work on
   clipped layouts"*. Window studies must build a complete, flattened clipped layout instead of
   setting `RecursiveShapeIterator.region`.
2. `probe_net` returns a net whose `.circuit()` must be inspected; two probes returning the same
   `cluster_id` are not evidence of the same net unless both are in the same circuit.

**Consequence for B3.** Its supply identification used the same pattern. It has been re-run
through the flattened engine and its conclusion survives unchanged (same two clusters, same pin
counts, now an order of magnitude above the next net), and both gates now assert net-identity
uniqueness explicitly. The answers were right; the reasoning was unsound, and the difference
matters for anything built on top of them.

**Status of B2's evidence.** B2's warm-up partition comparison — the evidence that
the extraction method is sound — was obtained on the *hierarchical* path. It passed, which is
itself informative (no collision mattered at 230 cells). It has since been **re-run through the
flattened engine** and reproduces exactly: 84 nets both sides, 285 terminals both sides, 0
signatures unique to either, with `warmup_netlist.json` rewritten byte-identically. That closes
the concern for the warm-up path specifically; B7 remains the formal gate, because it must
regress the whole S0→B6 chain rather than the connectivity stage alone.

## 9. B5: a self-certifying direction inference (found by B6, 2026-09-12)

**The bug.** B5 inferred every pin's direction from connectivity alone, via four propagation rules
over one equation per net (`Σx = 1`, since a well-formed net has exactly one driver). Rule D
enforced *"a cell with signal pins must have an output"*. The premise is true; the implementation
— "if every pin of a master is decided except one, that one is the output" — lets the solver
**manufacture a driver on a net that has none**, and a manufactured driver is indistinguishable
from a real one once written into the artifact.

**What it produced.** On net 806 — `{a31oi_2.A1, a311o_2.A1}`, two input pins and nothing else —
the tie-break fired and recorded `a31oi_2.A1` as an **output**, which that family's own name
forbids (`a31oi` outputs `Y`). So `direction.outputs` held an impossible verdict, and
`direction.undetermined` reported two classes as a "genuine structural ambiguity" that was really
collateral damage: with `a31oi_2`'s output already spent on net 806, `a31oi_2.Y` could no longer
be recognised as net 766's driver.

**Why no B5 check could see it.** The enclosing check was *"every net whose classes are all
decided has exactly one driver"*. The fabrication is what made the classes decided, so the check
passed **because of** the defect it existed to catch. That is the project's signature bug class —
keying on something that is not what it claims to be — in its purest form: a check that cannot
fail because it consumes the very fact it verifies. Its sixth occurrence.

**What caught it.** B6's cell models are generated from each master's own function-family name, so
`cells.py` refused to build a model whose family says `Y` while the direction table says `A1`:

    AssertionError: sky130_fd_sc_hd__a31oi_2: family name says output Y, B5 derived ['A1']

A cross-step *interface* assertion caught an upstream defect that the upstream step's own gate had
certified. The pattern generalises: **a check derived from a second, independent source catches
what same-source consistency checks cannot.**

**The fix.** Direction now comes from the pin labels the chip carries (a pin labelled `X`, `Y`,
`Q`, `HI` or `LO` is an output; every other non-supply pin is an input) — artifact evidence of the
kind Δ2 already sanctions, and impossible to fabricate. The structural solver remains as an
**auditor** whose disagreement is recorded rather than inherited. And the fact it was hiding is
now stated rather than papered over:

    classes 286 · resolved 286 · undetermined 0 · outputs 67 · inputs 219
    structural auditor: 284 decided, 2 undecidable honestly, 1 contradiction (a31oi_2::A1)
    undriven: 1 — net 806, {a311o_2.A1, a31oi_2.A1}, li1 only, all inputs

Gate `check_stepB5.py` went 42 → **54 checks**, the additions re-deriving the convention from A4's
pin lists rather than reading B5's totals, so a regression to structural inference fails the gate.

**Pitfall for reuse.** Never let a solver satisfy an invariant by *choosing a value for the thing
the invariant is about*. Say which inputs are evidence, which are derived, and which are asserted;
and when an invariant has no evidence behind it, report the violation rather than resolving it. A
check is only a check if it can fail — assert the absolute property ("no net has two drivers")
instead of the condition your own solver has just arranged to hold.

---

## 10. Phase B review (2026-09-12)

A requested full re-verification of Phase B. Same method as §1, plus one test this project had
never run: **simulate the emitted cell models**.

### What passed

* All 18 gates, from a clean tree. Every Phase-B artifact regenerates byte-identically, and the
  substantive claims re-derive from the raw GDS rather than from the artifact.
* **The cell models are functionally correct.** `build/cells.v` was read back and every one of the
  62 combinational models simulated over all 2^n input vectors against a reference derived
  independently from family digits and pin names (first char = group operation, last char =
  combining operation, digits = group sizes, `_N` = complemented input, trailing `i` = inverted
  output): **62/62 exact, 0 mismatches**. Plus `conb` = HI 1 / LO 0 across 32 vectors, and the three
  flops by directed testbench — edge capture, async active-low reset and set, and reset winning over
  a simultaneous clock edge.
* The output-label vocabulary `{X, Y, Q, HI, LO}` is **complete for this design**: all 69 placed
  masters agree between the label-derived outputs and an independent reading of the SkyWater naming
  convention, with no family falling through to the "no output" default. The one candidate for a
  missed output, `S`, is the select **input** of `mux2_1`.

### Findings

**F1 — nothing verified what a cell computes (high · fixed).** An `input`/`output` swap is caught
by B6's gate and by `iverilog`; a wrong boolean operator was caught by nothing. Demonstrated rather
than inferred: `or2_2` was changed to compute `A & B`, `build/cells.v` was regenerated from it, and
the **complete suite passed 18/18**. Phase B's evidence for the models was "interface + compiles",
and §6.2 of this document had wrongly called B7 a functional oracle. The oracle is now **wired into
the gate** as `check_stepB6.py` §6b — every function re-derived from the family name and pin names
alone, every combinational model simulated over all 2^n input vectors, `conb` and the flops checked
by directed test, at a cost of ~0.3 s. The same falsification run against the new check fails
exactly one check and names the models.

**F2 — the fault injector had never completed (high · fixed).** `fault_inject.py` did not import
`re`, while the `m_v_warmup_repoint` mutation added with B7 calls `re.findall`. Every run since B7
crashed at that mutation with `NameError`, so the "54 × 17" grid reported at B6 was the last one
that ever finished; the 60 × 18 figure was an expectation, not a measurement. Fixed by adding the
missing import. The re-run completes: 0 misses, 0 hermeticity violations, `artifacts restored:
True`. Two things worth keeping: the crash left the stamp file behind with all artifacts intact —
the stamp doing exactly its job — and the shell's exit code was useless as a signal, because the
`cmd; echo "EXIT=$?"` wrapper exits 0 regardless.

**F3 — an unasserted counter and an overstated claim in the artifact (medium · fixed).**
`position_layer_fallbacks = 981` was referenced by no gate and by no stage PASS condition, and B4's
own `method.position_layers` stated the containment guarantee unconditionally ("so it cannot land
on another net's wire"), which its data contradicts for those 981 probes. Re-derived event by
event: 951 are `VPB`, 30 are `diode_2` supplies, and **all 981 are on pins B4 records as
unassigned** — the path is degenerate, not a fallback (`VPB`'s only shape is on pair `64/16`, off
the conductor set, so there is nothing to probe). No assignment rests on it, so no data was wrong;
the defect was a guarantee asserted nowhere and stated too strongly. Both are fixed: the artifact's
`method.position_layers` now describes the fallback, and `check_stepB4.py` §10 re-derives the
counter from A4 + B1 + A2 and asserts that every fallback site is a pin B4 records as unassigned.
`docs/steps/B4.md` §4 and §6.1 had claimed the extra 9 positions "still resolved to a net", which
is false, and both are corrected.

**F4 — checks that could not fail (medium · fixed).** `check_stepB5.py` contained
`check('each port net exists in B4', sorted(a), sorted(a))` — it compared a list to itself, so it
passed for any input and never consulted B4, while its label claimed a property it did not test.
`check_stepB6.py` had the same shape: `check('the bus O is declared 8 bits wide',
nl['decls'].get('O'), 'output')` recorded `O`'s direction and discarded the parsed range, so the
width was never checked (compilation happens to catch that one). Both are rewritten: the B5 check
now compares against B4's actual net table, and the B6 check now parses and asserts the declared
range `[7:0]`. This is the sixth and seventh occurrence of the project's signature bug class, and
the second time one has been found inside a check written to catch it.

**F5 — `flatten` was exposed as an option (low · fixed).** `build_engine(..., flatten=True)` was
documented as a *correctness requirement* — net identity is meaningless without it — yet no caller
ever passed `False`. The parameter is gone: `build_engine` now flattens unconditionally, so there is
no branch left to take.

**F6 — a measurement with nothing behind it (low · documented, no change needed).** `9839
subcircuits = 1618 cells + 8221 via placements` is cited as evidence that the hierarchy is exactly
one level deep, but no gate can recompute it once the layout is flattened. `docs/steps/B4.md` §3
and §8 of this document already say so; it is recorded here only so the claim is not mistaken for a
gated one.

**F7 — B5's self-report was unasserted (low · fixed).** `netlist_check.json` carries 27 internal
`checks` with `passed` flags. B7's gate asserted its report's checks; B5's did not. Now it does,
with a floor so an emptied list cannot pass either.

### What this review does not establish

* **Nothing here validates a model against real silicon behaviour.** Exhaustive truth tables
  against a derived reference prove the Verilog says what we intended; whether our reading of
  `a21bo`'s family name is correct is C1's question.
* `net 806` is still the chip's one undriven net — reported, asserted as an identity, expected to
  appear as X in C1.
* `target`, `hygiene`, `step2` and `step3` still never fire on an artifact mutation, by design: no
  mutation targets their inputs.

**Status of the fixes.** All applied, as one correction commit. F1, F4, F5 and F7 are gate changes;
F3 also corrected the overstated `method` text inside `pin_net.json`, which is the **only artifact
byte that moved** — verified by regenerating the stage and confirming nothing downstream changed.
F6 needed no change (already documented). Result: B4 58 → **61** checks, B5 52 → **54**, B6
30 → **37**, the suite stays 18/18, and the 60 × 18 fault grid still passes with 0 misses.

## 11. C1 — the replay oracle, and its power measured (2026-09-12)

C1 is the project's second independent oracle: `example_inputs.vcd` is a simulation of the real
chip, so its outputs are ground truth for our recovered netlist and our 69 name-derived models.
This section records what C1 establishes, the two corrections it forced, and — because the number
is easy to misread — how much the replay can actually see.

### What it establishes

* **312 cycles × 9 output bits = 2808 comparisons, 0 mismatches**, plus 936 stimulus bits, with no
  `x` or `z` anywhere in our outputs. Our netlist, compiled with our models, reproduces the real
  design cycle for cycle on the real stimulus.
* The waveform's own facts, re-derived from the raw file: 625 clock edges at a uniform 5000 ps half
  period, 312 cycles, every stimulus change on a falling edge, two 121-bit feeds (cycles 4…124,
  160…280) that are *different* vectors, `TRY AGAIN` twice (125…133, 281…289), `success` low on all
  312 cycles.
* `recon/vcd_cycles.csv` — the Step-1 dossier's table, written long before C1 and by different code
  — agrees with a fresh parse of the raw file on all 1872 fields.

### Two corrections C1 forced

**C1-1 — `net 806` reads `z`, not `x`, and the claim about it was too broad.** B7's §6 and the plan
both predicted "X". An undriven `wire` is high impedance in 4-state simulation; `x` is what an
unwritten `reg` reads. Measured: `z` on all 312 cycles. More usefully, forcing it to 0 and then to 1
leaves every interface output identical — so nothing observable rests on it *under this waveform*,
and that is where the claim now stops, because its two consumers (`a311o_2`, `a31oi_2`) mask it
state-dependently and **E1 re-probes it on the winning vector**. Both documents are corrected.

**C1-2 — "byte-exact VCD replay" needed defining (AC5).** No two simulators emit the same VCD bytes
— this reference emits one `$scope` block *per signal*, which no simulator we know of does — so the
target is every value of every signal at every sampled instant, `x` included, not a diff of two
files. Verified to mean exactly that: the gate re-derives the table sampling at a *different*
instant than the stage (at the edge vs 1 ps after it) and requires the tables to be identical,
which pins the convention rather than restating it.

### What the replay can see — the number that should not be misread

2808 comparisons is a count, not a measure of strength: 294 of the 312 cycles are idle and the other
18 carry one constant message, so C1 constrains *timing* tightly and *message content* weakly.
`model-power` measures what it misses, by negating each cell model in a scratch rebuild of
`build/cells.v` and re-running the replay (the tree is never touched):

| | |
|---|---|
| models negated | 66 of 69 (3 physical-only: decap, diode, tapvpwrvgnd) |
| **caught** | **37** |
| **silently passed** | **29** — `a2111oi_2`, `a21bo_2`, `a221o_2`, `a22o_2`, `a22oi_2`, `a311o_2`, `a31oi_2`, `and2_2`, `and3b_2`, `buf_2`, `clkbuf_4`, `conb_1`, `dfstp_2`, `nand2b_2`, `nor4_2`, `o211ai_2`, `o21a_2`, `o21ai_2`, `o21bai_2`, `o221a_2`, `o22ai_2`, `o2bb2a_2`, `o31ai_2`, `o32a_2`, `o32ai_2`, `or3b_2`, `or4_2`, `or4b_2`, `or4bb_2` |

Two things this settles. **F1's break** (`or2_2` negated) moves **140 output cycles starting at
cycle 3** — C1 is the first oracle in the project that sees it, where the whole 18-gate suite did
not. And C1's silence on those 29 models is the *specific, measured* reason C2 must be exhaustive
rather than sampled: §10's open question ("whether our reading of `a21bo`'s family name is correct
is C1's question") is answered as **not by C1, for 29 of the 66**. All 69 masters are instantiated
in the chip, so no model is skipped as unused.

A cross-check falls out for free: **both consumers of `n806` are in the silent list**, which is
C1-1's forced-run conclusion reached from the other direction.

### The gate's own control

`check_stepC1.py` (83 checks) regenerates both C1 outputs hermetically, re-reads the waveform one
identifier at a time straight from the raw text, re-runs the simulation three times from the
committed netlist, re-measures four model negations itself (requiring the *exact* wrong-cycle
counts: 140, 0, 18, 0), and asserts the shape of the evidence rather than letting a large count
imply strength. It also carries a **negative control**, because a comparison that silently compares
nothing looks exactly like a correct one: the same harness with the stimulus moved one clock period
must fail — and does.

### What C1 does not establish

* Nothing about the **winning** vector: this waveform is the wrong-input path by the README's own
  account, so a successful attempt's message content is untouched here.
* Nothing about the **29 models the replay cannot see** — that is C2's list.
* Nothing about *why* `net 806` is undriven, only that it changes nothing on this waveform.

### Coverage as of this section

The fault grid learned C1's three new artifacts (**70 mutations × 19 gates**: `build/replay_tb.v`,
`recon/derived/vcd_replay.json`, `recon/derived/c1_power.json`, and `stepC1` as the 19th gate), so
C1's own evidence is subject to the same "can it fail?" question as everything before it.

C1's own surface was closed with a **focused sweep** rather than the full grid, because the full
grid has grown from ~16 minutes to ~90–120: `stepC1` re-simulates the design three times and
rebuilds the whole cell library 66 times to measure itself, and the grid runs every gate for every
mutation. `fault_inject.py --only REGEX` now filters mutations by label, so a step's new surface can
be closed on its own and the whole grid is spent at a **phase boundary** instead. Measured:

```
--only 'replay_tb:|vcd_replay:|c1_power:': 10 of 70 mutations
mutations with no gate firing : 0
hermeticity violations        : 0
artifacts restored            : True
FAULT INJECTION: PASS
```

Every one of the 10 fired **`stepC1` and only `stepC1`** — which is also the evidence that the new
gate owns its three artifacts, and that no older gate is silently covering for them. The full
70 × 19 grid is scheduled for the end of Phase C.

## 12. C2 — the warm-up, exhaustively, and the limit of two oracles (2026-09-12)

C2 is the second independent oracle: a design whose Verilog we hold (`warmup/00_source.v`) and whose
layout we extracted (`build/warmup.v`), compared over the **whole** input space rather than one
waveform — all **65536 `(A, B)` pairs**, because 8 serial bits per operand puts the entire space in
a single simulation of milliseconds.

### What it establishes

* **`S` agrees between our netlist and Jane Street's RTL on every one of the 65536 pairs**, on three
  channels: the reference implements `a + b == 496` (0 mismatches), our netlist matches the
  reference (0), our netlist implements the function (0). Both assert on exactly the 15 equating
  pairs, so neither `S` is stuck.
* The comparison is against the **RTL**, not a formula restated in Python: both designs are
  instantiated in one testbench and driven by one stimulus. The formula is checked too, as a third
  witness.
* Nothing about the protocol is typed in — width, target, serial order and the top module are read
  out of `00_source.v`. The gate re-derives them with its own patterns and requires agreement.

### C2-1 — a real defect in B7's emitter (fixed)

The first run failed to compile: `error: port ``VGND'' is not a port of dut_ours`. B7's emitted
netlist put its five reference ports in the header and declared `inout VGND;` / `inout VPWR;` in the
**body** — and a body-level `inout` for a name the header omits is not a port, so the file advertised
an interface it did not have and could not be instantiated the way `build/puzzle.v` can. The
reference's own `02_netlist_with_power_rails.v` lists `VPWR`/`VGND` in the header; only B7's file
differed. Fixed in `tools/puzzle/warmup.py`, artifact regenerated, and **`check_stepB7.py` 39 → 40
checks** with the assertion that would have caught it: a generated caller connects every port the
netlist is *documented* to have, and that list is derived from the source and the supply table — not
from the file's own header, or the check would agree with whatever the header says. Falsified: the
old header makes exactly three checks fail, the new one among them.

The lesson is one the project keeps re-learning in new costumes: **compiling a file proves it is well
formed, not that its interface is real.** B7's gate compared the port list against `01_netlist.v`'s
six ports, which the defective header matched exactly.

### C2-2 — the limit of the two behavioural oracles (measured, carried forward)

`warmup-power` asks C1's `model-power` question of this oracle: negate each model the design
instantiates (16 of 18) and re-run the sweep. Result: **15 caught**, 1 silent (`clkbuf_16`), and —
the point — **all 3 models C1 is blind to are caught here**. The second oracle earns its place.

But the same analysis, cross-referenced against C1's per-model measurement, yields the finding this
phase should carry:

> **26 of the 66 cell models are reached by neither behavioural oracle.** Union coverage: C1's 37 plus
> C2's 3 additions = **40 of 66**.

Those 26 are the models C1 cannot see *and* which the warm-up never places, so no stimulus in either
design excites them. For them the evidence stops at B6's truth tables — which verify our Verilog
against our *reading of the family names*, exactly the class of gap F1 opened, now quantified instead
of suspected. `recon/derived/c2_power.json` names them.

**E1 is where this can change, and it needs no new machinery**: both power stages can be re-run
against the winning 121-bit vector, which excites parts of the design neither this waveform nor this
warm-up does. Recorded here as the cheapest remaining coverage win in the project.

### Coverage as of this section

The fault grid learned C2's three artifacts (**78 mutations × 20 gates**, `stepC2` as the 20th gate;
now **84 × 21** with C3's).
C2's own surface is closed with a focused sweep, like C1's, for the reason §11 gives: `stepC2` is
another ~25 s gate, and the full sweep belongs at the phase boundary.

A note on `--only`, because it bit: the first C2 sweep was launched with
`--only 'warmup_equiv:|c2_power:'`, which **cannot match** `warmup_equiv_tb: …` — a literal colon
after `warmup_equiv` is not what those labels have. The run reported `6 of 78`, which is the harness
telling the truth about a filter that was not the one intended. The labels are the contract, so a
filter is now checked against them before it is trusted, and the sweep was re-run with
`--only 'warmup_equiv|c2_power'` to cover all eight. (The `78` is also a correction: the previous
sections said 77.)

**The re-run, definitively: 8 of 78 mutations, 0 misses, 0 hermeticity violations,
`artifacts restored: True`, and every one of the eight fired `stepC2` and only `stepC2`.**
The same shape as C1's verdict, and the evidence that this gate owns its three artifacts and that
no older gate is silently covering for them. The run prints the labels it selected, so the set is
read from the log rather than inferred from a count — which is what would have caught the first
attempt immediately.

## 13. Step C3 — decomposition

C3's method was amended before it ran (connectivity gives **one** component: no flop's `D` is
another flop's `Q`, and each of the 92 flops' transitive cones reaches 90 of the other 91), so the
decomposition is behavioural. It took **two stimuli**: the reference waveform leaves 21 flops with no
change at all, so a third of the population had no behaviour to classify, and the winning vector
from `tools/target.py` (feed offset 4, measured in E1) supplies it.

Result: **92 of 92 flops assigned to exactly one block**, gate **23/23**. The blocks are
`input_shift_register` 12 (depths 0..11, exact against the input under both stimuli),
`ones_counter` 6 (takes the value **22** during the winning feed, 22 of 23 steps +1),
`message_counter` 5, `position_counter_bit` 10, `region_counter_bit` 27 (each fires exactly twice —
two stars per region), `winning_only_bit` 18, `unclassified` 14.

One plan expectation is **refuted rather than unmet**: there is no 121-period counter — the design
tracks the column with a **mod-11 counter** (`i0855` is bit 0 of `p mod 11`, verified) and reaches
121 = 11 × 11 by wrapping. The 14 flops left unclassified have no discriminating signature under
either stimulus, and are reported as a class with their signatures rather than given a function.

Three bugs were caught by the gate during this step, all of the same species — a claim not matching
its own record: the artifact listed two flops as region bits while the assignment put them in the
ones counter (the gate's blocks-vs-assignment check found it); sorting a block's flop list reversed
the counter's bit order and turned its peak from 22 into 58 (the re-derivation found it); and the
artifact's reported peak/steps were not compared against the re-derivation at all until the gate was
extended to do so. This is why the gate re-runs the simulation rather than reading the JSON.

The fault grid learned C3's three artifacts (`build/decompose_ref_tb.v`, `build/decompose_win_tb.v`,
`recon/derived/blocks.json`) and `stepC3` as the 21st gate: **84 mutations × 21 gates**. Six new
mutations target this step (2 harness, 4 artifact).
