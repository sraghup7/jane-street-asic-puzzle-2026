# Verification audit — Phase A (and everything before it)

**Date:** 2026-09-12 · **Scope:** every step executed up to and including A5
**Question asked:** are the executed steps solid enough to build the remaining steps on?
**Answer:** yes, after fixing three defects this audit found. Two were in the gates themselves.

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

21 mutations × 11 gates, each cell an isolated measurement with a pristine restore between
every probe. The "fired" column is the real output of `tools/checks/fault_inject.py`:

> The matrix grows as steps land: after B2 it is **33 mutations × 13 gates**, still with 0
> misses and 0 hermeticity violations (B1's six fire `stepB1`, B2's six fire `stepB2`). The table below
> is this audit's run; run the tool for the current one.

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
2. **No end-to-end functional test exists yet.** Everything above is structural. The first
   functional oracle is B7 (reproduce the warm-up netlist) and the second is C1 (replay the VCD).
   Nothing here substitutes for them.
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
3. **51 of 69 cell types have no independent functional oracle.** The warm-up exercises 18. C2
   validates those 18 against `A + B == 496`; the other 51 are only covered indirectly by C1's
   whole-chip VCD replay, where a single wrong model is hard to localise. This is the largest
   residual risk in Phase C and should be planned for, not discovered.
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

Gate `check_stepB5.py` went 42 → **52 checks**, the additions re-deriving the convention from A4's
pin lists rather than reading B5's totals, so a regression to structural inference fails the gate.

**Pitfall for reuse.** Never let a solver satisfy an invariant by *choosing a value for the thing
the invariant is about*. Say which inputs are evidence, which are derived, and which are asserted;
and when an invariant has no evidence behind it, report the violation rather than resolving it. A
check is only a check if it can fail — assert the absolute property ("no net has two drivers")
instead of the condition your own solver has just arranged to hold.
