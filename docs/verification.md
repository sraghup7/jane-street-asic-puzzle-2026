# Verification audit — Phases A to F

**Date:** 2026-09-12, extended 2026-09-13 · **Scope:** every step executed, Phases A to F
**Question asked:** are the executed steps solid enough to build the remaining steps on?
**Reading the numbers:** a gate count in any section is the suite's size *on that step's date* — the
suite grows as steps land. The live count is the README's, and `check_stepF2` fails if the two disagree.
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

**The focused sweep's verdict: 6 of 84 mutations, 0 misses, 0 hermeticity violations,
`artifacts restored: True`, and every one of the six fired `stepC3` and only `stepC3`** — the same
shape as C1's and C2's verdicts, and the evidence that this gate owns its three artifacts and that no
older gate is covering for them. The run printed the labels it selected, so the covered set is read
from the log rather than inferred from a count. Two of the six are mutations that leave
well-formed JSON (a flop dropped from a block list, the reported peak inflated); both are caught,
and they are caught by the checks that exist *because* the first version of this artifact had exactly
those defects.

---

## 14. C4/C5/E1 close-out — three steps that had no gate (2026-09-13)

**Why this section exists.** Auditing the plan against the working tree found two things at once:
three executed steps (C4, C5, E1) whose evidence lived **only in `recon/scratch/`** — which is
gitignored, so a fresh clone could not reproduce any of it — and one round of C4 work (15:53–15:59,
the trigger-set partition) that was on disk but in no document and no commit. Neither is a wrong
result; both are results that could not survive the machine they were made on. `AGENTS.md` rule 5
("verification is mandatory and executable") was not met for the project's headline negative finding,
which is the finding the writeup will lean on hardest.

### What was built

| Piece | What it is |
|---|---|
| `tools/puzzle/verdict.py` | the **simulation-free evaluator** promoted out of `recon/scratch/symb.py`, plus the board generators, the exact-cover search, the solution counter and the three stages |
| `python -m tools.puzzle region-map` | C4 → `recon/derived/c4_partition.json` (~100 s) |
| `python -m tools.puzzle rejections` | C5 → `recon/derived/c5_rejections.json` (~30 s) |
| `python -m tools.puzzle winning` | E1 → `recon/derived/e1_messages.json` (~8 s) |
| `check_stepC4.py` 31/31 · `check_stepC5.py` 14/14 · `check_stepE1.py` 20/20 | the gates; discovered by `run_all.py` like every other one — **24 gates total, all PASS** |

The instrument is not taken on trust: **every one of the three gates re-validates it against
`example_inputs.vcd` before doing anything else** (312 cycles, 2808 output comparisons, 0 mismatches,
0 unknown bits). Every negative claim in C4 rests on "the evaluator's silence about a signal is
evidence", so that premise is re-established on each run rather than cited.

### What the gates re-derive, rather than read

The project's rule for gates is that they assert values; the rule added here (plan §9) is that a gate
says whether it is **re-deriving** a number or **reading** it. What is re-derived on every run:

* C4: the cone of `i1594.D` — **104 nets / 57 flops**, with the block census 27 `column_counter_bit` /
  18 `winning_only_bit` / 6 `ones_counter` / 5 `unclassified` / 1 `message_counter`;
* C4: `success` is flop `i1594`'s own Q output (the port net *is* that net — the first version of the
  check looked for a gate driver and failed, which is exactly the kind of assumption a gate is for);
* C4: between the accepted board and 20 boards that satisfy every visible rule, **exactly two** flops
  in that cone differ at the decision cycle — `i0455` (1 → 0) and `i0798` (0 → 1) — the hidden
  condition isolated to two latches;
* C4: the candidate partition is a **cover** of all 121 cells by 11 disjoint classes, two of the
  answer's stars each, re-derived live at 13 sampled cells (a stale artifact fails);
* C4: the exhaustive counts — the partition + visible rules = **1 solution, the accepted board**
  (715 877 nodes); visible rules alone = thousands (cap hit);
* C4: **both controls**, including control A re-derived live (200 look-alike partitions × 39 boards);
* C5: the boards are **regenerated** and the design asked about each one through its own message —
  **39 boards, 0 accepted**, every board validated against the four visible rules first, plus equality
  with the committed artifact;
* E1: the offset table (only **4** of 4..8 accepts, at **cycle 126**) and the message table (4 of 5
  classes), with the fifth asserted to be the *measured* `TRY AGAIN` and named as blocked on the map.

### The controls are part of the result, not a footnote

This is the finding of the close-out. The obvious test of a candidate map — "does it reject the boards
the chip rejects?" — **has almost no power**: 188 of 200 random same-shape look-alike partitions also
reject every board (class sizes run to 28 cells, so a valid board overloads a big class by
construction). The test that does bite is uniqueness — the partition plus the visible rules has
exactly one solution, the accepted board — and **2 of 40** look-alikes pass that too, so it is strong
evidence and not a fingerprint. Recorded in the artifact, re-measured by the gate, and written into
plan §11 so the eventual writeup cannot upgrade "candidate, every test passed, each test's power
measured" into "the map".

### Defects found while closing out (all fixed)

1. **A generator that depended on set iteration order.** `probe_c5b.py` chose which star to move with
   `next(...)` over a **set**; copied into the pipeline with the same cells in a copied set, the same
   code produced **0 usable boards instead of 39** (`set()` reorders). C5's boards are now generated
   deterministically and re-measured, so C5's conclusion is established on our own boards.
2. **Order-sensitive and transposed comparisons**, twice: the column reference table was built as
   *rows*, and covers were compared by `sorted(frozenset(...))` — which is not a sort, because
   `frozenset` is only partially ordered, so it returns the input order whenever nothing is
   comparable. Together they announced a second candidate map that was the visible column rule. Both
   now go through one canonical predicate (`verdict.is_columns_cover`), and regenerating the artifact
   after the fix reproduced it **byte for byte** (sha256 unchanged), so the classification was right
   and the test around it was wrong.
3. **A gate check that assumed a gate driver** for `success` (it is the flop's Q net directly).
4. **The artifact may not contain a clock** (B3's lesson) — the stages print runtimes, never store
   them.

### The reproduction gap this closed, and what remains

* Closed: C4/C5/E1 now have gates, committed artifacts and documented commands
  (`docs/steps/C4.md` R19–R20, `C5.md`, `E1.md`).
* Still open, and stated plainly: **the ~40 probe scripts under `recon/scratch/` are untracked.** They
  are the working record (R1–R20 are written up in `docs/steps/C4.md` from them, with their commands
  and raw outputs named), but a fresh clone cannot re-run them. The gates are the durable replacement
  for the three steps that matter; the exploratory probes remain scratch by design.
* One acceptance sub-item is open by measurement, not by omission: `TWO NOT TOUCH` cannot be
  constructed without the region map, so E1 reproduces **4 of 5** message classes and says so.

### Fault injection

The rule from C1–C3 is that a new step's artifacts get mutations, so that "the gate fires" is
evidence rather than an assumption. Ten were added: four for `c4_partition.json` (a class loses a
cell; the solution count faked; **the rejection test called powerful**, which is the claim R20 exists
to prevent; a trigger set dropped from the total), three for `c5_rejections.json` (a board claimed
accepted; a board dropped; the recorded replay faulted) and three for `e1_messages.json` (the rising
cycle moved; a decoded message changed; **the open item deleted**). Focused sweep:

```
.venv/Scripts/python tools/checks/fault_inject.py --only "(c4_partition|c5_rejections|e1_messages):"
```

RESULT (`recon/scratch/fault_c4c5e1.txt`): **10 of 94 mutations in the focused sweep, 0 escaped,
0 hermeticity violations, `artifacts restored: True`, PASS.**

```
injected fault                                     | ... stepC4  stepC5  stepE1
c4_partition: a class loses a cell                 | ...   FAIL       .       .   -> stepC4
c4_partition: the solution count faked             | ...   FAIL       .       .   -> stepC4
c4_partition: the rejection test called powerful   | ...   FAIL    FAIL       .   -> stepC4,stepC5
c4_partition: a trigger set dropped from the total | ...   FAIL       .       .   -> stepC4
c5_rejections: a board claimed accepted            | ...   FAIL    FAIL       .   -> stepC4,stepC5
c5_rejections: a board dropped                     | ...      .    FAIL       .   -> stepC5
c5_rejections: the recorded replay faulted         | ...      .    FAIL       .   -> stepC5
e1_messages: the rising cycle moved                | ...      .       .    FAIL   -> stepE1
e1_messages: a decoded message changed             | ...      .       .    FAIL   -> stepE1
e1_messages: the open item deleted                 | ...      .       .    FAIL   -> stepE1
```

Read the way C1–C3's sweeps are read: every fault was caught, none by an older gate covering for a
new one, and no gate rewrote the tree. Two mutations fire **two** gates, and that is the intended
shape rather than a leak: `c4_partition: the rejection test called powerful` and `c5_rejections: a
board claimed accepted` both touch the *claim C5's gate exists to police* — that a rejection is
evidence for a map — so `check_stepC5` reads the C4 artifact's recorded controls and fails if that
power is overstated, and `check_stepC4` reads C5's board list for the same control. The dependency is
deliberate and now measured: neither gate can be softened without the other noticing.

The 21 other gates that never fired in this sweep are the ones whose artifacts these mutations do
not touch — the same list the earlier focused sweeps produced, and the reason `--only` is legitimate
for a step being closed out.

---

## 15. Phase D — the answer, derived rather than reproduced (2026-09-13)

**Why this section exists.** Until now AC1 read honestly as *"our netlist reproduces the published
vector"*: `tools/target.py` supplied the 121 bits, E1 drove them through the chip and the chip said
`(* TWO STARS *)`. That is a strong statement about the *netlist* and no statement at all about the
*puzzle* — the vector came from the answer key. D closes that: the constraint system we recovered is
handed to our own search, and the vector falls out of it.

### What was built

`tools/puzzle/solve.py` — one stage (`python -m tools.puzzle solve`, ~20 s) covering D1–D4, writing
`recon/derived/solutions.json`:

* **two enumerators, written separately, both run to completion.** `solve_rows` works row by row over
  the 45 legal column pairs with cell-set pruning; `enumerate_bitmask` works over 11-bit row masks with
  an explicit stack, where the eight-neighbour rule between adjacent rows is one bitwise expression
  (`next & (mask | mask << 1 | mask >> 1)`). Agreement between them is agreement between two programs,
  not a re-run of one.
* **the validator is not the solver.** All five constraints are recomputed from the finished grid by
  `tools/target.py::check_constraints` — written in Step 1, before any solver existed — plus the
  class loads counted from C4's partition.
* **the region constraint is an input, not an assumption.** It is loaded from
  `recon/derived/c4_partition.json`; `region_partition()` refuses to run if the artifact is missing or
  if the number of non-column capacity-2 covers is anything but one, because an arbitrary choice there
  would silently change the problem being solved.

### The result

| | |
|---|---|
| D1 solver | **exactly 1 solution**, search complete, 715 877 nodes |
| D2 independent enumerator | **exactly 1 solution**, the same board, 32 214 420 nodes |
| agreement | on the board **and** on the count |
| recomputed validation | 22 stars · two per row · two per column · 0 adjacent pairs · class loads all 2 |
| D4 | **== `target.FEED_ORDER`**, its reverse **== `target.WITNESS_AS_PRINTED`**, board **== published grid** |
| D3 (bounded, honestly) | without the region constraint: **≥100 000** solutions, cap hit at 1 027 376 nodes |
| the discriminator | with the **column** cover instead: **≥2** solutions |

That last row is worth more than the uniqueness result itself. C4 produced *two* capacity-2 covers
from the netlist's own latches: the ragged partition and the columns. The columns cover is the
visible two-per-column rule, so as a "region constraint" it constrains nothing — and the count says
so. Since the published design accepts exactly one input, **the column cover cannot be the hidden
rule**, and the ragged partition is the one that is consistent with it. This does not prove the
ragged partition is the rule (its own evidence is bounded in §14 — 2 of 40 look-alikes pin the answer
too, and the chip's verdicts cannot separate them), but it eliminates the only competing candidate
the netlist offers.

### The honest limit, asserted rather than glossed

Uniqueness is **relative to C4's candidate partition**. The artifact records which partition was used
and where it came from; `check_stepD.py` (22/22) re-runs both enumerators live, validates through the
Step-1 checker, asserts both bit orders, requires D3's bound to be recorded as a bound, and requires
the column-cover discriminator to hold. The claim this supports is "the answer is derived by our own
search over a constraint system we recovered, one of whose five constraints is a candidate" — which
is strictly more than "reproduced", and strictly less than "solved the puzzle from first principles".

### Defects found in this step (both mine, both fixed before the gate passed)

1. A coverage check that assumed all classes are the same size (`sorted(class_of) == list(range(n))
   * (121 // n)`) — it only holds when every class is 11 cells, which is exactly the *column* cover
   and not the ragged one. Replaced with a multiplicity check against the recorded class sizes.
2. Dead code left in the first draft of `stage_solve` (a `... if False else ...` grid conversion and
   two discarded `solve_rows` calls, one of which would have re-run a half-million-solution search).
   The gate did not exist yet; reading the file caught it.

### Fault injection

```
.venv/Scripts/python tools/checks/fault_inject.py --only "^solutions:"
```

RESULT (`recon/scratch/fault_d.txt`): **5 of 99 mutations in the focused sweep, 0 escaped,
0 hermeticity violations, `artifacts restored: True`, PASS** — and every one fired **`stepD` and only
`stepD`**:

```
injected fault                                 | ... stepC4  stepC5  stepD  stepE1
solutions: a second solution claimed           | ...      .       .   FAIL       .   -> stepD
solutions: one bit of the vector flipped       | ...      .       .   FAIL       .   -> stepD
solutions: a star moved in the board           | ...      .       .   FAIL       .   -> stepD
solutions: the enumerators made to disagree    | ...      .       .   FAIL       .   -> stepD
solutions: a class loaded beyond capacity      | ...      .       .   FAIL       .   -> stepD
```

One bit of the 121 is enough to fail the gate, which is the point: AC1 is a byte-exact contract, and
the mutation that flips exactly one bit — the smallest possible corruption of the answer — is caught
by the check that compares it to `target.FEED_ORDER` rather than by anything downstream.


## 16. Phase E, step E2 — the map tested by the chip's own message (2026-09-13)

`check_stepE2.py` (**20 checks**), `tools/puzzle/confirm.py`, artifact
`recon/derived/e2_messages.json`, fault-injected:

RESULT (`recon/scratch/fault_e2.txt`): **5 of 104 mutations in the focused sweep, 0 escaped, 0
hermeticity violations, `artifacts restored: True`, PASS** — each firing **`stepE2` and only
`stepE2`**:

```
injected fault                                  | ... stepD  stepE1  stepE2
e2_messages: only a few boards claimed to spell it | ...   .       .   FAIL   -> stepE2
e2_messages: the control group silenced          | ...   .       .   FAIL   -> stepE2
e2_messages: the undriven-net finding smoothed   | ...   .       .   FAIL   -> stepE2
e2_messages: a tested board moved                | ...   .       .   FAIL   -> stepE2
e2_messages: another message class corrupted     | ...   .       .   FAIL   -> stepE2
```

**What is re-derived on every run:** the two-switch family (189 boards), the 23 that are adjacent with
every class within capacity and the 156 that are adjacent but overload a class, the four visible rules
on every board *before* it is fed, the chip's reading of a live sample, the control group's readings,
E1's partition-violating vector, the four other message classes, and the character positions in which
the undriven-net tie changes the answer. The gate also requires that every board recorded in the
artifact is a member of the re-derived family — without that check a board mutated inside the artifact
fell out of the sampled intersection and the gate passed on the rest.

**Result.** The design spells `TWO NOT TOUCH` on **all 23** boards whose only fault is an adjacent
pair, and does **not** on the 8 controls that also break a class cap, nor on E1's overloaded vector.
Both directions are new: AC4's fourth message class had never been reproduced before the partition
existed, and the contrast is what makes the agreement evidence rather than noise. The step was designed
as a *test of C4's partition* — an input satisfying the partition while violating the chip's real rule
would come back `TRY AGAIN` — and it passed.

**The finding that comes with it.** Every reading is `TWO?NOT TOUC???`: 11 of 13 characters of
`TWO NOT TOUCH` exact, with **one character indeterminate**, following **net 806** — the one net B5
found structurally undriven (`a311o_2.A1`, `a31oi_2.A1`). Forcing it 0 gives `TWO"NOT TOUCH`, forcing
it 1 gives `TWO NOT TOUCJ..`, and **neither tie reproduces the published string byte-for-byte**. The
gate asserts that both ties differ from the contract, so the discrepancy cannot be edited away.
Whether the layout ties that net somewhere our extraction missed is an open question, recorded here and
not chased.

**Defects found while building it** (four, all mine, all caught before the gate went green): a
generator that appended a lazy generator instead of a list, so every collected board captured the
mutated board list; `int(byte, 2)` on a byte containing `?`, which crashed instead of reporting an
indeterminate answer; an alignment that compared the counter's trailing bytes against the message and
so reported a mismatch — this one briefly inverted the result — and a dict keyed by swapped-row pair,
which silently dropped all but two control boards.

**Reproduce:**

```
./.venv/Scripts/python.exe -m tools.puzzle confirm           # ~40 s -> recon/derived/e2_messages.json
./.venv/Scripts/python.exe tools/checks/check_stepE2.py      # 20/20
./.venv/Scripts/python.exe tools/checks/fault_inject.py --only "^e2_messages:"
```


## 17. Phase E, step E3 — the acceptance matrix (2026-09-13)

`check_stepE3.py` (**21 checks**), `tools/puzzle/accept.py`, artifact
`recon/derived/acceptance.json`, fault-injected:

RESULT (`recon/scratch/fault_e3.txt`): **5 of 109 mutations, 0 escaped, 0 hermeticity violations,
`artifacts restored: True`, PASS** — each firing `stepE3` and only `stepE3`. **The first run of this
sweep failed**, and the failure is the most useful thing in this section: see the hole below.

**What the matrix is.** One table over AC1–AC6, every row read from a committed artifact, compared
against `tools/target.py`. Three rules shape it:

1. **The yardstick is re-verified first.** `tools/target.py` is checked inside the report — grid
   consistent with the bit vector in both orders, the feed-order/as-printed distinction non-vacuous,
   and the four mechanical constraints holding — so a broken yardstick cannot flatter the matrix.
2. **Artifacts only.** The matrix does not re-derive anything from the netlist; the per-step gates
   guard the artifacts, and this step reports on them. Where a criterion is not met it says so rather
   than being reworded until it passes.
3. **Three states, not two.** `PASS`, `FAIL`, and `PARTIAL` — because AC6 is neither.

**The result: 5 PASS, 1 PARTIAL, 0 FAIL, overall PASS (the partial declared).**

| | criterion | status |
|---|---|---|
| AC1 | the exact 121-bit vector | PASS — derived by our own search, both enumerators agreeing, both bit orders equal |
| AC2 | `success` at cycle 126 | PASS — asserted at offset 4 and nowhere else |
| AC3 | `(* TWO STARS *)` | PASS |
| AC4 | the four wrong-input messages | PASS — the fourth via E2's 23 boards, and not on the 8 controls |
| AC5 | byte-exact replay of `example_inputs.vcd` | PASS — 2808 output bits, 0 mismatches, 0 `x`/`z`, cross-check clean |
| AC6 | region partition, "JS" | **PARTIAL** — recovered and corroborated; not confirmed; "JS" not reproduced |

**AC6 is the honest row, and the gate enforces it.** At the time of this section (2026-09-13), the
matrix kept AC6 at `PARTIAL` and required its unmet reasons and a non-empty "not claimed" list, failing
if the row was ever improved without the evidence changing.

**Superseded 2026-09-16 (fix pass, Task 10; see §25).** The "JS" reading was wrong, not the status: the
recovered classes do read "JS" (`accept.letter_classes()`, cropping each class to its own bounding
box), so AC6 is now `PASS`, computed from its evidence (`accept.ac6_ok()`) rather than pinned. What
remains genuinely unproven — the verdict channel cannot *confirm* the partition over an unseen
look-alike, and one character of the corroborating message follows net 806 — is carried as notes on a
`PASS` row instead of reasons for a `PARTIAL` one. The gate's protection moved with it: it now fails if
AC6's status stops matching what `ac6_ok()` computes, in either direction.

**One nuance the matrix states rather than hides.** The contract's `two_per_row_col_but_adjacent` row
expects `TWO NOT TOUCH`, and E1's single vector for that class answers `TRY AGAIN` — because it was
hand-built before the map existed and also violates the hidden constraint. The class is reproduced
(E2: 23 boards), the mismatch is explained in the AC4 row, and the gate fails if that explanation is
removed.

**Reproduce:**

```
./.venv/Scripts/python.exe -m tools.puzzle acceptance        # -> recon/derived/acceptance.json
./.venv/Scripts/python.exe tools/checks/check_stepE3.py      # 17/17
./.venv/Scripts/python.exe tools/checks/fault_inject.py --only "^acceptance:"
```


## 18. Phase E, step E4 — the whole pipeline from nothing (2026-09-13)

`check_stepE4.py` (**11 checks**), `tools/puzzle/repro.py`, artifact
`recon/derived/reproduction.json`, fault-injected:

RESULT (`recon/scratch/fault_e4.txt`): **6 of 115 mutations, 0 escaped, 0 hermeticity violations,
`artifacts restored: True`, PASS** — each firing `stepE4` and only `stepE4`. The six are the claims
this step makes: a stage dropped from the plan, a stage recorded as failing, a timing put back into the
artifact, acceptance moved off the end, a cold difference smoothed over, and a **warm run presented as
the evidence** — that last one matters because E4's claim is specifically the cold one.

**The claim being tested** is the protocol's Step 5 claim: *fresh clone → commands → same result*. E4
tests the strongest form available here: **delete every derived artifact the repo tracks, rebuild from
the upstream layout and the puzzle alone, and compare the rebuilt bytes to what was committed.** Git is
the undo — only tracked files are deleted — so the experiment is safe to repeat.

**The run:** 29 stages · **396.2 s** · acceptance PASS · **31 of 31 regenerated byte-identically** · one
new file (the report itself). Timings are printed and deliberately not stored: B3's rule is that a
derived artifact must regenerate byte-identically, and a clock in a file guarantees it will not.

**It found two real defects, both fixed.** This is the first step whose value was almost entirely in
what it exposed rather than what it confirmed.

1. **Two pipeline inputs could not be regenerated.** `recon/inventory.json` (B1/B2/B5) and
   `recon/vcd_cycles.csv` (C1's independent cross-check) are committed and consumed by the pipeline,
   and **no stage produced them** — the cold rebuild died at B1 with `missing input
   recon/inventory.json`. They are Step-1 dossier outputs, so a fresh clone could still run; but the
   pipeline could not rebuild its own inputs, which fails the reproduction claim. Fixed with two thin
   S0.1 stages (`tools/puzzle/inventory.py`, `tools/puzzle/vcd_table.py`) that call the Step-1 tools'
   own `main()` so serialisation stays the tools' business. Both regenerate byte-identically.
2. **A hidden dependency, plus a silent skip that hid it.** C4's controls are measured *against C5's
   rejection set*, but the table ran C4 before C5, and the C4 stage loaded that file with
   `if exists else None`. On a cold tree it therefore wrote an artifact **without its `rejection_test`
   and `lookalike_uniqueness` fields** — silently. That is why the first cold run reported exactly one
   file as differing, and why it was `c4_partition.json`. Fixed twice over: the stage table is ordered
   by **dataflow** (`rejections` before `region-map`, commented — the plan's steps still read C4 then
   C5), and a missing input is now a hard error naming the command to run.

**And one defect in the checking code itself**, found the same way: `git show HEAD:<path>` with a
Windows backslash path fails and prints nothing, and the empty hash was then reported as "differs from
HEAD too" — a false diagnosis of a real difference. Paths are normalised before any git call. Worth
recording because it is the same species as the escapes the fault-injection sweeps keep finding: the
comparison, not the subject, was wrong.

**Reproduce:**

```
./.venv/Scripts/python.exe -m tools.puzzle reproduce --check   # prerequisites
./.venv/Scripts/python.exe -m tools.puzzle reproduce --plan    # 29 stages, in order
./.venv/Scripts/python.exe -m tools.puzzle reproduce --cold    # ~6.5 min; the full claim
./.venv/Scripts/python.exe tools/checks/check_stepE4.py        # 11/11
```


## 19. Phase F, step F1 — nothing shipped is dead (2026-09-13)

`check_stepF1.py` (**5 checks**), fault-injected:

RESULT (`recon/scratch/fault_f.txt`): **8 of 123 mutations in the focused sweep, 0 escaped, 0
hermeticity violations, `artifacts restored: True`, PASS** — the two F1 mutations firing **`stepF1` and
only `stepF1`**. **The first run of this sweep failed**, and what it found is the most useful result in
the F phase:

> Both dead-code mutations **escaped**. The gate counted references by **raw text**, and the mutation
> appends a helper whose name appears in `fault_inject.py`'s own source *as a string literal* — so the
> mutator's own text counted as a use of the function it was injecting. A gate whose subject is dead
> code was blind to dead code, and the cause was the same one that makes any docstring mention look
> like a call.
>
> Fixed at the root: reference counting is now **AST-based** — names, attributes, imports and
> arguments, never text. The stricter counting then immediately found **a second dead function**,
> `um(v, dbu)` in `tools/kl_recon.py`, which the text version had been hiding behind a mention.
> So the fix to the gate found more dead code than the bug had concealed.

**What it checks.** Every module-level function and class in shipped code must be referenced at least
once somewhere in the repo besides its own `def`; every module must be imported, a pipeline stage, or
*cited as an instrument*; no forbidden dependency may be imported or listed; and the throwaway
directories (`recon/scratch`, `hints`, `renders`, `sources`) must be gitignored **and** hold nothing
tracked. Scanned this run: **451 names, 64 modules, 125 text files**.

**It found a real piece of dead code.** `capacity2_cover()` in `tools/puzzle/verdict.py` was **never
called**: C4's stage had an inline copy of its body instead. Removing the copy and calling the real
function had to keep the artifact identical, and it does — `c4_partition.json` regenerates
**byte-identically** (verified by running the stage and diffing against `HEAD`), so the refactor is
provably behaviour-preserving rather than probably. The function now takes the covers the caller
already computed; the reason it was bypassed is that it used to repeat the expensive exact-cover
search, which is exactly the kind of pressure that produces a silent duplicate.

**The dependency floor F1 asks for is now measured, not asserted.** `shapely`, `scipy`, `networkx` and
`pandas` were **uninstalled** — shipped code imported none of them, and `requirements.txt` never listed
them; `numpy` stays because `matplotlib` requires it. `recon/inventory.json` was regenerated to record
the new environment (three probe lines), and `check_step1`, which used to assert *"shapely available"*
because that is what the Step-1 dossier recorded at the time, now asserts the stronger and current
claim: **the packages we deliberately do not depend on are absent** (58/58). The dossier's historical
record is untouched in `docs/01_problem.md`; what changed is what has to hold now.

**One bug in the gate itself:** it demanded a citation for every shipped module and therefore flagged
its own four `check_*.py` files as unlisted instruments. A gate is referenced by the suite's
*discovery* — `run_all` walks `tools/checks/check_*.py` — so being found is what makes it live; the
check now says so instead of asking for a citation that would never exist.


## 20. Phase F, step F2 — the documentation is true (2026-09-13)

`check_stepF2.py` (**9 checks**), fault-injected:

RESULT (`recon/scratch/fault_f.txt`): **8 of 123 mutations, 0 escaped, 0 hermeticity violations, PASS**
— the three F2 mutations firing **`stepF2` and only `stepF2`**: a gate count one short of the truth, a
pipeline command that does not exist, and a deleted package row.

**What it checks.** Nobody can clone-and-run inside a gate, but almost everything that makes a README
*true* is checkable, and a README goes false in exactly the ways it drifts: every command it prints
must be a real stage or a gate on disk; its gate count must equal the number the suite discovers
(**32**); its pipeline stage count must equal the pipeline (**29**); the prerequisites it names must be
the real ones (upstream repo, `requirements.txt`, `iverilog`, `git`); the output it shows must be the
output we get (the acceptance summary, the reproduction summary, a runtime); `docs/deps.md` and
`requirements.txt` must agree in **both** directions; no document may be orphaned; and the layout it
describes must exist.

**Two bugs in the gate, found by running it.** It compared the README's "29 pipeline stages" against
the stage *table*, which has 30 entries because it includes `reproduce` itself — a check that would
have failed forever on a correct README. And its orphan check matched document *file names*, so
`docs/steps/A1.md` counted as unreachable even though the plan cites `A1` throughout; it now matches
stems, because the point is reachability, not spelling.

**And one arithmetic error in the README itself:** the per-phase runtime table summed to 389 s while
the run reported 396 s. The phase numbers were wrong (C was written as 218 s; the stage times sum to
227.8 s). Corrected, and the table now states the measured total and the reason they differ (the
runner's own overhead).

**One judgement call, decided rather than asked:** `docs/C4_region_map_issue_for_review.md` was cited
nowhere. Deleting a written record is the irreversible option, so it is **linked from the README** with
a note on what it is — the region-map question as it stood while C4 was open, kept as the record of how
it was closed.


## 21. Phase F, step F3 — one convention (2026-09-13)

`check_stepF3.py` (**6 checks**), fault-injected:

RESULT (`recon/scratch/fault_f.txt`): **8 of 123 mutations, 0 escaped, 0 hermeticity violations, PASS**
— the three F3 mutations firing **`stepF3` and only `stepF3`**: a parked-work marker, a commented-out
line of code, and a long function stripped of its docstring. Each mutation keeps the file **valid
Python**, because the fault under test is the convention, not a syntax error that would fire every gate
importing the module.

**What it checks.** No parked-work markers in shipped code or the README; a module docstring in every
module; a docstring on every definition a reader cannot hold in their head; no commented-out code; the
file-naming convention; and the same verdict shape from every gate (`GATE: PASS` / `GATE: FAIL`, and a
non-zero exit on failure).

**Four bugs in the gate, all found by running it against a codebase that was already consistent.**

1. It banned four all-caps markers **including in its own source**, so it flagged itself. The list is
   now built from concatenated halves: a gate that cannot name what it bans without tripping itself
   would end up with an exemption, which is a hole.
2. Its "snake_case" rule rejected `check_stepC4.py` — the repo's actual, sensible convention for gates.
3. `check_recompute.py` was the one gate whose verdict read `INDEPENDENT RECOMPUTATION:`; renamed to
   `RECOMPUTE GATE:` so "the gate passed" means one thing everywhere.
4. Its "public" test (a name mentioned in another file) matched *mentions* — printed words, docstrings,
   comments — and flagged about twenty innocent helpers. Replaced with a rule that cannot produce
   boilerplate: **classes, and any function over 25 lines**, must be documented.

**What that rule then found:** 22 substantial undocumented functions, up to 276 lines
(`stage_instances`, `stage_vcd_replay`, `stage_warmup_regression`, `stage_layers`, `build_netlist`,
`target.check_constraints`, and the rest). All 22 now carry docstrings written from their actual
bodies, stating what they do and why they exist — e.g. C2's says it is the check C1 structurally cannot
make. Final state: **233 documented, 218 short helpers exempt**, the scaffolding (`main`, `check`) named
explicitly as the exemption with the reason: thirty copies of one sentence is noise, not documentation.


## 22. Phase F, step F4 — the freeze (2026-09-13)

`check_stepF4.py` (**7 checks**). **This step registers no artifact in the fault grid, and that is
deliberate** — the one place this project deviates from its own "every step registers its artifacts"
rule, for the reason given below.

**What it checks.** The working tree is clean; the tag `step5-complete` exists **and points at HEAD**;
nothing was committed after the tag; every artifact the fault grid registers exists on disk; every
mutation targets a registered artifact; every gate the grid names exists on disk; and the suite
discovers at least the number of gates the README claims.

**Why it is not in the fault harness.** Its subject is the *frozen* repository — a clean tree and a tag
at HEAD — and fault injection dirties the tree by design. Registered as a gate in the sweep, it would
fail on every mutation (the tree is mutated!) and drown the one signal that table exists to give: which
gate owns which claim. It has no artifact of its own to mutate, and the freeze it guards is declared by
running the whole suite, which is where it belongs.

**Reproduce:**

```
./.venv/Scripts/python.exe tools/checks/check_stepF1.py      # 5/5
./.venv/Scripts/python.exe tools/checks/check_stepF2.py      # 9/9
./.venv/Scripts/python.exe tools/checks/check_stepF3.py      # 6/6
./.venv/Scripts/python.exe tools/checks/check_stepF4.py      # 7/7 once the tag exists
./.venv/Scripts/python.exe tools/checks/run_all.py           # every gate, in order
```


## 23. Phase F, step F5 — the post-review remediation (2026-09-13)

After the freeze, the whole project was reviewed read-only: repository and history, upstream integrity,
every artifact's hashes, the pipeline, the gates, and the documentation's numbers. The review changed
nothing; this step is the follow-up. Eight findings, and what each became:

| # | finding | disposition |
|---|---|---|
| 1 | `build/cells.v`'s provenance stamp named `pin_names.json` and `netlist_check.json` but printed **puzzle.gds's hash for both** — the same hash twice, and neither file's real hash | **fixed + made an invariant.** The writer read each artifact's `['source']['sha256']` (the hash of the GDS *it* read) where it meant the artifact's own hash. `check_stepF2` now reads every hash in `build/*.v`, pairs it with the path on the same or preceding line, and tests it against that file: 7 hashes in 7 files, every claim holds. Mutation: `stamp: cells.v names an artifact that does not exist`. |
| 2 | `IDEA.md` — the one-line seed note — was tracked and referenced by nothing | **kept and linked** from the README. Deleting a written record is the irreversible option, and it documents the original intent including the blog. |
| 3 | 12 of 23 artifacts recorded no input hash | **two fixed, the rest a stated rule.** `solutions.json` and `acceptance.json` — the artifacts that state the answer and the verdict — now carry path→sha256 for what they read (8 hashes, verified current by `check_stepF2`). For the other ten the fault grid is the control, and the gate says so in its own comment: each is registered, and its mutation must fire its owning gate. |
| 4 | `docs/verification.md`'s title said "Phases A, B and C" while the document covers A–F | retitled. |
| 5 | `S0.1` (the two Step-1 stages) had no gate of its own | **the review was half right, and the half it got wrong is the half that mattered.** `recon/inventory.json` is gated: `check_step1` regenerates it bit-identically and three mutations target it. But `recon/vcd_cycles.csv` — a committed derivation the C1 stage reads as its cross-check oracle — was checked by **no gate and no mutation at all**. `check_stepC1` now regenerates it through `tools/vcd_probe.py` into a temporary directory and compares bytes, and the table is registered in the fault grid. |
| 6 | documentation depth is uneven (A–E1 have per-step docs; D–F have plan outcome blocks and these sections) | **accepted.** The evidence is the same; the shape differs. Recorded rather than re-shaped. |
| 7 | upstream integrity rests on the commit id plus artifact hashes, not per-file hashes | **accepted as adequate:** only three upstream files are read (GDS, VCD, the warm-up netlist), and each is hash-pinned inside the artifact that read it. |
| 8 | workspace bulk (`.venv` 205 MB, `recon/` 137 MB of ignored scratch) | noted; the scratch tree is ignored by design and the tracked footprint is small. |

**A note on the new check, because it was wrong twice.** The stamp check first paired a path from the
*preceding* line with the hash on the current one — which made it indict correct files — and then
matched `.v` inside `.vcd`. Both were parser bugs of mine, and both were caught by the check failing on
subjects that were provably right. It is now the gate that would catch finding 1's recurrence, and it
exists because the earlier version's own output had to be argued with first.

FAULT RESULT (`recon/scratch/fault_f5.txt`): **3 of 126 mutations, 0 escaped, 0 hermeticity violations,
`artifacts restored: True`, PASS.** `stepF2` owns both stamp findings and `stepC1` owns the cycle
table; the first mutation also fires `stepB6` — by design, not by leakage: B6 owns the claim *"`cells.v`
is what the writer emits"*, so any edit to that file fails it, while F2 owns the separate claim that the
stamp is true. Two gates, two different claims, and the row shows both columns set.

The freeze tag `step5-complete` moves forward onto this commit, which the commit message states: the
freeze state is the one that includes the remediation.


## 24. Phase F, step F6 — the undriven net, packaged (2026-09-13)

Net 806 was the last claim in this repository with nothing behind it. It lived in `docs/net806.md`, its
scripts in the ignored `recon/scratch/` tree, and its **decisive** experiment was not even listed in the
note's own reproduction block — so a fresh clone could not re-run it, and no gate would have failed if it
stopped being true. F6 moves the experiment into the pipeline. **Nothing about the finding changed**;
what changed is that its numbers are now produced by a stage, asserted by a gate, and re-derived on
every suite run.

| # | What F6 establishes | How it is re-derived | Result |
|---|---|---|---|
| 1 | the net is the same net | `tools/puzzle/net806.py` rebuilds the engine from A1/A2 and probes the two pin coordinates B4 recorded | both probe to cluster **806**; the artifact's terminals equal B4's exactly — 2 pins, both `A1`, both inputs by the label vocabulary, both on 67/20 |
| 2 | its geometry | every shape on the die is filed by the net the engine assigns it; the gate re-measures by a *second* method (a window census around the pins) | **17 shapes on five `(layer, datatype)` pairs** (met1/met2/met3 + two cut types), bbox `(174895, 90450)–(179330, 91875)`, 4 of its own vias — **the note's "four layers" was a miscount, corrected** |
| 3 | no missed merge can hide a driver | every cut overlapping the wire, filed by net, tested against the conductors **its own A2 rule joins** | 4 same-net, 1 elsewhere (68/44 → net **766**, rule joins 68/20+69/20 while the wire is on met1 there): **0 could merge** |
| 4 | it is not tied to a constant | nearest `conb` output, pin-to-pin in DBU | **i0484.LO at 9 966 dbu** (~10 µm) |
| 5 | the message tie | `confirm.ask` on the first usable board of the re-derived E2 family (189 candidates, 23 usable), net 806 forced to 0 and 1 | `TWO?NOT TOUC???` unknown at **[3, 12]**; 806=0 → `TWO"NOT TOUCH`, 806=1 → `TWO NOT TOUCJ..`; **neither equals `TWO NOT TOUCH`** |
| 6 | the artifact regenerates | `check_stepF6` runs the stage into scratch (`_regen`) and compares bytes | committed 6 533 bytes, regenerated 6 533, **byte-identical**; two consecutive runs give the same sha256 |
| 7 | the census is not a second opinion about B3 | conductor shapes and net count compared with `nets.json` | **40 360 / 2 626**, equal |

**Gate:** `tools/checks/check_stepF6.py` → **21/21**, ~11 s. It is not a cheap gate, and deliberately so:
it rebuilds the extraction engine (~7 s) rather than reading the artifact's own numbers back, which is
what makes it a re-derivation instead of a checksum.

**Two bugs in the gate's first drafts, both worth recording** because both would have produced a green
gate on a false reading. The first framed the decisive cut test against *every* wire shape regardless of
layer — which would have counted the legal 68/44 underlap as a merge and reported a driver-hiding defect
that does not exist; the filter to the cut's own A2 rule conductors **is** the check. The second built
its overlap identities with `tuple(sorted(o.items()))`, which raised `TypeError: unhashable type: 'list'`
— caught immediately, but it is the same class of mistake the project keeps finding: keying a comparison
on something that is not a key.

**What F6 changed beyond packaging.** The plan's E2 outcome block had stated a live disjunction —
*"either the layout ties that net somewhere our extraction missed, or the printed character is genuinely
indeterminate"* — and the first branch is now refuted; the plan carries that amendment where the
sentence still stands, rather than silently replacing it. The README's row and bullet for the net are
rewritten from "the one experiment still owed" to what the experiment found, and `docs/net806.md` now
reproduces through the stage with the scratch probes marked superseded.

### F6's cold run found a defect in E4's own evidence

Running the documented command was the point of the step, and it failed in a way no gate could see:

```
32 of 33 regenerated byte-identically
DIFFERS: recon/derived/reproduction.json (not regenerated)
```

**Cause.** `reproduce --cold` deletes every *tracked* derived artifact and rebuilds them, then compares
bytes — but `recon/derived/reproduction.json` is written by the same run, **after** the comparison. So
once that file became tracked, every real cold run deleted it, could not regenerate it, and reported
exactly one permanent difference.

**Why nobody had noticed.** `check_stepE4` asserts `cold['differences'] == []` — and it passed, because
the report it was reading was older than its own subject being tracked. The committed report records
`tracked_before: 31`, an empty difference list, and lists `recon/derived/reproduction.json` among
`untracked_new_files`: it is the run that *created* the file, before git tracked it. The gate was
therefore passing on evidence that a fresh run of the same command cannot produce — the failure mode the
F phase exists to catch, arriving in the gate that certifies the reproduction claim.

**Fix (with the check behind it, as F rules require):**

* `repro.derived_files()` excludes the run's own report, with the measurement above written into its
  docstring rather than left as folklore;
* the run prints the exclusion, and the report carries it as `cold_check.report_excluded`, so the claim
  is checkable from the artifact;
* `check_stepE4` asserts both (that the report is not in the cold set **and** that the report says so),
  so the exclusion cannot be quietly undone;
* mutation `reproduction: the report s own exclusion dropped` covers that check.

The corrected run is the one quoted in the README (30 stages, 32 of 32 byte-identical, no differences).

### F6's runs, as evidence

**The documented command, re-run after the fix** (`recon/scratch/cold_f6.txt`):

```
cold run: removed 32 tracked derived files; git can restore them all
  (recon/derived/reproduction.json is written after this comparison and is therefore not in that set)
  ...
  32 of 32 regenerated byte-identically

30 stages, 420.7s total, acceptance PASS
REPRODUCTION: PASS
```

**Fault injection** (`--only "net806:|report s own exclusion"`, `recon/scratch/fault_f6.txt`): **3 of 129
mutations, 0 escaped, 0 hermeticity violations**, each firing only its owner — `stepF6` for both net806
mutations, `stepE4` for the report one — baseline all-zero, `artifacts restored: True`:

```
injected fault                                    | ... stepE4 ... stepF6
net806: a tie made to print the published string  | .              FAIL  -> stepF6
net806: a cut over the wire claimed able to merge | .              FAIL  -> stepF6
reproduction: the report s own exclusion dropped  | FAIL           .     -> stepE4
```

**Full suite:** 33 gates. `stepF6` passes (21/21, 10.6 s) and `stepE4` passes with its new check;
`stepF4` fails only while the step is uncommitted — it asserts a clean tree with the tag at HEAD — so the
freeze is re-declared at the end of the step, which is also how F5 handled it.

## 25. Fix pass — the verification machinery says what it does (2026-09-16)

`fault_inject.py`'s gate list (`GATES`) was a hand-maintained literal, and it had drifted: `stepG1`
(added in Task 6 of the fix pass, region-map re-derivation under a nulled `target.py`) was never added
to it, so the meta-gate could not tell whether corrupting `c4_partition.json` would be caught by the one
gate built to catch exactly that. `GATES` is now derived from `tools.checks.run_all.gate_paths()` —
the same discovery `run_all.py` itself uses — filtered to exclude `stepF4` (whose subject is the frozen
tree itself; a mutation dirties the tree by design and would fire on everything). Re-running
`fault_inject.py --only "^acceptance:"` after the change: **5 of 5 mutations caught, 0 hermeticity
violations** (Task 10's own verification, above).

**The tool now refuses to run outside a scratch clone.** `is_scratch_clone()` checks
`git config --get remote.origin.url`: a clone made with `git clone . <dir>` has an `origin` pointing at
a local path; the primary working repository has no remote at all (checked 2026-09-15: `git remote -v`
is empty). Without `--in-place`, `main()` refuses before touching anything. This formalizes what the
tool's own docstring already asked for ("run it alone", i.e. in isolation) rather than trusting the
operator to remember.

### Code-level model faults (`tools/checks/model_faults.py`, ported from the review's `r7_modelfault.py`)

A companion diagnostic, not a gate: `fault_inject.py` measures whether the gates notice a corrupted
*artifact*; this measures whether the *experiments the gates run* would notice a wrong *model* — one of
the evaluator's own standard-cell logic functions computing the wrong thing. `c1_power.json` already
flags which combinational cell models are C1-silent (complementing their output leaves the 312-cycle
reference replay unchanged): 29 families, 27 of them combinational and present in this design (`conb`
and `dfstp` are not combinational). For each, this script complements every instance of that model in
the evaluator and re-runs R (the replay), E1 (the winning vector + four wrong classes), E2g (the E2
gate's live stratified sample, now 53 boards post-Task-8), E2s (all 189 boards of the swap family), C5
(the C5 gate's regenerated boards) and C4g/C4f (sampled / all 121 single-star trigger sets), comparing
against the unmutated baseline. It reads the derived board and derived feed
(`verdict.derived_board()`/`derived_feed()`), never `tools.target` — answer-free, like the pipeline it
measures.

Run as 4 shards in the working tree (2026-09-16; read-only, mutates only in-memory `Machine.nl.comb`
copies, so no clone was needed) — about 44 minutes wall time across the 4 parallel shards:

| Family | Instances | Gate-detected | Family | Instances | Gate-detected |
|---|---|---|---|---|---|
| `a2111oi` | 1 | yes (E1) | `o21a` | 31 | yes |
| `a21bo` | 2 | yes | `o21ai` | 6 | yes |
| `a221o` | 6 | yes | `o21bai` | 1 | yes (E2g/E2s only) |
| `a22o` | 23 | yes | `o211ai` | 1 | yes |
| `a22oi` | 1 | yes (E1) | `o221a` | 3 | yes |
| `a311o` | 2 | yes | `o22ai` | 2 | yes |
| `a31oi` | 1 | yes (E2g/E2s only) | `o2bb2a` | 1 | yes |
| `and2` | 17 | yes | `o31ai` | 2 | yes |
| `and3b` | 4 | yes | `o32a` | 4 | yes |
| `buf` | 1 | **yes (E2g/E2s only)** | `o32ai` | 1 | yes (E2g/E2s only) |
| `clkbuf` | 32 | **no** | `or3b` | 1 | yes (E1 only) |
| `nand2b` | 24 | yes | `or4` | 10 | yes |
| `nor4` | 2 | yes | `or4b` | 9 | yes |
| — | — | — | `or4bb` | 1 | **no** |

**25 of 27 caught by gate experiments; 2 missed: `clkbuf`, `or4bb`.** This is an improvement on the
original review's finding (R7, 2026-09-15: 24 of 27, with `buf` also missed) — `buf` is now caught,
via E2g, exactly because Task 8 changed the E2 gate to ask about all 23 within-cap boards instead of a
sample of 8, closing that gap without this script being touched. `clkbuf`'s 32 instances gate the design's
flops directly; this project's evaluator treats the clock as ideal (`Machine._settle` steps every flop on
its own control edge without modelling clock-tree logic), so a wrong `clkbuf` model is not meaningful
here regardless of what any gate could do — recorded as a limit of the evaluator, not a gap in the
gates. `or4bb`'s single instance (an unused-output variant of `or4`, which model-power itself found
C1-silent) is a genuine miss: nothing in this project's experiment set exercises it. Neither miss is new
information — both were already disclosed by R7 — but re-measuring after Tasks 4–10 confirms the fix
pass closed the one gap (`buf`) it could close and did not silently regress the rest.

**`check_stepF4.py`** now checks the tag `review-fixes-complete` (previously `step5-complete`, which
marked the pre-review state on 2026-09-12 and is never moved again — it is history, not the freeze this
gate declares). **`check_step3.py`** gained a check that the plan discloses the region map's actual
method (single-star stimulus probing, prohibited method 5) rather than only listing it as prohibited;
`docs/03_our_plan.md` gained the disclosure sentence next to the P5 table row, and its §9 correctly
states `run_all.py` runs every gate independently (gates do not re-run each other, which they never
did — the old wording was aspirational, not descriptive, since the pipeline's first version).

