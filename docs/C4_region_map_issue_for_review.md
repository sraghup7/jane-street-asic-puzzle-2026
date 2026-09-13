# The C4 issue: we cannot find the hidden region map, and the evidence says the chip may not compute one per cell

## Postscript (same day) — part of §4 has been superseded

Written after §4, and recorded rather than quietly edited in, because it changes the picture:

A test that does **not** use single-star stimuli — remove one star at a time from the *accepted* board
and compare each flop's full state trajectory (`probe_c4aq.py`) — recovers a **perfect matching of the
22 stars into 11 pairs**, and it is exactly the grouping this project derived earlier and then withdrew.
So the region mechanism **does exist as counters**; what made it invisible was that the single-star
stimuli used in §4 and §5 put an **invalid** board on the wire (one star fails "2 per row" the moment
row 0 ends), and the region counters evidently only run while the pattern is still on a valid path.

That also means the published method as our notes describe it **cannot work on this design** — not
because the method is wrong, but because its stimulus is an invalid board.

Corrected state of the issue:

* region **pairing** (which two stars share a region): **recovered**, confirmed by two independent routes;
* region **extents** (which of the 121 cells belong to each region): **still not recovered** — but the
  eleven region counters are now *identified flops*, so their enable cones are a known target, and
  evaluating those cones over the 121 cells is the remaining step.

Consequently §9's options shift: option 1 is now a well-posed step rather than "try pairs and hope", and
options 2 and 3 (close as a negative / re-scope) are correspondingly less attractive. The §4 table's
R11 and R12 rows remain literally true *for the stimuli they used*, and are superseded by this test.


**What this document is.** A statement of one blocked item in a reverse-engineering project, written so
that it can be reviewed without any other context: what the chip is, what we set out to recover, what
we verified, exactly what we searched for and found, how strong each negative is, the one experiment
still unrun, and the decision we need.

**Prepared:** 2026-09-13. **Status:** blocked, not failed — see §8 for what is and is not at stake.

---

## 1. The puzzle (background)

A hardware checking chip for a Star-Battle-like puzzle:

* A board of **11 × 11 = 121 cells**. A solution places exactly **22 stars**.
* Mechanical rules: exactly **2 stars per row**, **2 per column**, **no two stars adjacent**
  (including diagonals).
* The chip takes the 121 bits **serially** and answers with a message. Messages observed:
  `EMPTY SKY` (no stars), `BIG BANG` (all ones), `TWO NOT TOUCH` (adjacent stars), `TRY AGAIN`
  (generic failure), and on success `(* TWO STARS *)` at cycle 126.
* **Hidden rule** (per the published solution): the board is partitioned into **11 regions**, each
  region may hold at most 2 stars, and the region map is said to **spell "JS"**.

## 2. Our governing constraint

The project must recover things **our own way**, not by re-treading the published pipeline. The
differentiator declared for this item — call it **Δ5** — was:

> Decode the region-select logic **symbolically**: reduce it to a function of the cell index, and
> evaluate it. No stimulus.

That matters because the published solution's own method for this item was *stimulation*: probe one
cell at a time and watch which counter reacts.

## 3. What is verified (so the negatives below carry weight)

| Item | Result |
|---|---|
| The netlist | 1618 cell instances, **630 combinational gates**, **92 flip-flops**, 69 cell masters, recovered from the chip's own geometry and labels — no PDK, no Liberty file |
| Behaviour vs the chip's own reference waveform (C1) | 312 cycles × 9 output bits = **2808 comparisons, 0 mismatches** |
| Behaviour vs an independent simulator, on our netlist (R8) | every module-scope net: **225 956 comparisons, 0 disagreements**; 312 cycles, **0 unknown bits** |
| The published answer (E1) | our netlist emits `(* TWO STARS *)` and asserts `success` on **cycle 126** |
| The hidden constraint exists (C5) | **40 of 40** distinct patterns satisfying all three mechanical rules are rejected |

The instrument used for §4 is simulation-free: the netlist as boolean equations, evaluated in Python
with three-valued logic. It is the thing validated by row 3 above. Its agreement with the simulator is
what lets us treat its silence about a signal as evidence.

## 4. The issue, with the evidence

We cannot find the region map in the logic, and the evidence says the design does not compute it **per
cell**. Five analyses:

| # | Instrument | Searched for | Result |
|---|---|---|---|
| R9 | 121-position sweep with **no stars** (index decode only). Alignment re-derived inside the run: 121 distinct index states, 11/11 column decodes match `p mod 11 == k` | a net high on exactly one region's cells (one-hot enable); any set of nets partitioning the 121 cells; a k=4/5 recombination of decode-shaped nets | 424 nets with non-empty high-sets. The only 11-cell nets are **whole columns** and **sliding windows of 11 consecutive positions**. No one-hot enable. No exact cover. k=4/5 over 27 decode-shaped nets → **0 hits** |
| R10 | same sweep with a star on **every** cell (the puzzle's own all-ones input) | a partition once rows and columns are excluded; an affine map `(a·r + b·c) mod 11` | exact cover = the 11 columns only; excluding rows/columns → **0 alternative covers**. Affine: only rows/columns give 2 stars per class on the winning grid → **no diagonal or mixed map** |
| R11 | 121 probes, **one star at a time**; record which flop *fires* at that cell ⇒ each flop's **cell set** | a flop whose cell set is a region | The instrument validates itself: it reproduces the ones counter (120 cells), the position-counter LSB (109 cells) and **ten column counters** (11 cells each = one whole column, containing exactly that column's two stars). 57 flops have non-empty cell sets, 54 distinct. The only non-column 11-cell set is irregular. **No region counter visible** |
| R12 | the **published** method, implemented faithfully (§5) | the same thing, their way | 37 candidate cell sets (sizes 1–16); **no disjoint cover** of the 121 cells by 11 of them, at any mix of sizes |
| R6/R7 (earlier) | net-and-cone searches | region-gated counters; a disjoint cover from cone candidates | no disjoint cover; a candidate "star grouping" was **withdrawn** when its enable test found no net high on exactly a candidate pair |

**Net result.** What the design *does* count is: **per column** (11 counters, verified), **per pair of
columns** (3 counters), and **in total** (one counter that reaches exactly 22). What it does not
visibly do is count **per region per cell**.

## 5. The published method, run faithfully, also did not produce the map

Our step-2 notes record their Stage 8 as:

> Stimulate each cell position one star at a time and record which per-region saturation counter fires.
> Plotting which counter each cell belongs to recovers the region map, which spells "JS".

We implemented exactly that — their method, our instrument (121 single-star stimuli evaluated through
the verified equations) — and also corrected an assumption of our own in the process: we had required
each region to be exactly **11 cells** (121÷11), which is *our* assumption, not a fact; letter-shaped
regions would differ in size. Relaxed to any sizes, the search still finds **no** partition.

**Result: no region map.** The counter each cell belongs to is its **column** (ten of eleven columns
appear), plus the global counters. Plotting that gives vertical stripes, not "JS".

Two readings we cannot distinguish from outside the chip:

1. **Our notes compress Stage 8.** A *saturation* counter counts to 2 and stops; its "firing" is only
   observable when a **second** star lands in the same region. A single-star stimulus therefore cannot
   fire one — so the faithful experiment is a **pair** of stars, and that has **not been run** (§6).
2. **There is no per-cell region mechanism.** We found the decode vocabulary is dominated by *sliding
   11-position windows* — i.e. the design reasons about the incoming **stream** ("one row's worth" at a
   time), not about grid blobs. If the hidden rule is enforced over the stream the same way, then "the
   regions spell JS" describes the **shape of the answer**, not machinery the chip contains.

### 5.1 Discrepancies with the published description

Independently of the above, several of their structural claims are not what we measure:

| Published description | What we find |
|---|---|
| a counter wrapping at 121 (bit counter) | a **mod-11** column counter that reaches 121 by wrapping |
| a counter incrementing every 11 (row counter) | row predicates exist as nets, but **no per-row counter flop**; a 12-bit shift register holds a row plus one |
| **two flop banks saturating per row and per column** | 11 per-column counters verified; **no per-row counter found**; 3 additional counters link *pairs* of columns |
| an 8-bit counter comparing the running total with 22 | an ones counter — **6 bits** in our reading — reaching exactly 22 |
| the region map recovered by per-cell probing | not reproducible on our recovered netlist; it returns columns, not regions |

They also state that they **never decoded their own region lookup table** (they "declined to decode the
`magic_index` LUT"; their solver took the region constraint on trust), and describe the map as
"confirmation, not prerequisite". So the public "JS" map is itself a post-hoc artefact of probing, not
a decoded mechanism.

## 6. The one experiment still outstanding

Feed **pairs** of stars `(x, y)` and record which counter **saturates**. If some counter reaches 2
exactly when both stars lie in one region, then "same region" is recovered as an equivalence relation
and the map's extents follow. Cost: naive enumeration is 7 260 pairs; it can be cut with a pivot set of
cells, but that is a design decision, not a mechanical step. This is the only experiment we know of that
can overturn §4 and §5.

## 7. How each negative could be wrong (so a reviewer can weigh them)

| Negative | Strength | How it could be wrong |
|---|---|---|
| No one-hot region enable; no exact cover | solid for single-net enables | if the enable is a multi-level cone rather than a single wire, no single net would show it |
| No k=4/5 recombination | restricted | the pool was the 27 nets of 8–16 cells; unbalanced code bits (e.g. a 60/61 split) lay outside it |
| No affine map | airtight as stated | it rests only on the winning grid; it does not rule out irregular maps |
| No region counter in the trigger study | strong, two stated limits | (i) column 10's counter is masked because its last cell is position 120, where end-of-sweep checks fire in the same cycle; (ii) a counter whose next-state equals its current state at its own cell is invisible to that test |
| Published method gives no map | as implemented | the saturation/pair reading in §5 is untested |

## 8. What is at stake, and what is not

* **Not at stake:** the puzzle answer. It is reproduced and verified (§3).
* **At stake:** one acceptance criterion, and the provenance of one claim in the eventual writeup.
* The criterion, agreed before this work, was: *the "JS" region map recovered **independently***.
  Since Δ5 (symbolic decode) was what made it *independent*, and we could not satisfy it, a map
  obtained by the published probing method would **not** be independent in that sense.

Already recorded, not silently absorbed: on the project owner's instruction this item was executed once
using the **published** method instead of our own. That deviation is written into the plan (§C4) and the
step file (R12), with **Δ5 marked not satisfied as written**.

## 9. Options

| # | Option | Cost | Consequence |
|---|---|---|---|
| 1 | Run the pair/saturation experiment (§6) | a real piece of work, may come back empty | the only route that could still produce the map |
| 2 | Close the item as an **evidenced negative** | small | the writeup states: the hidden rule exists, is not a per-cell decode, and here is what it is *not*; the criterion is re-scoped |
| 3 | Re-scope the criterion to "constraint shown to exist and characterised" | small | honest, but gives up the differentiator |
| 4 | Adopt the published probing method outright | small | contradicts the project's hard rule against re-treading the published pipeline; weakens the artefact |

## 10. Questions for the reviewer

1. Is the negative strong enough to **publish as a finding** — "the hidden rule is not a per-cell region
   decode" — given the limits in §7?
2. Is the **pair/saturation experiment** worth its cost, or is the prior probability of success too low?
3. If the map cannot be recovered, how should the criterion and the writeup's claim be re-scoped so the
   artefact stays defensible?
4. Does the **discrepancy table (§5.1)** — published "banks per row and per column" vs our "11 column
   counters, no row counters" — change how much weight the published "JS" claim should carry?

---

## Appendix A — reproduction

All commands run from the project root with the project's virtualenv.

```
# the instrument, and its own validation (expects exit 0: 0 mismatches, 0 unknown bits)
./.venv/Scripts/python.exe recon/scratch/symb.py

# its agreement with iverilog on every module-scope net (expects 0 disagreements / 225956)
./.venv/Scripts/python.exe recon/scratch/probe_c4z.py

# R9: 121-position sweep with no stars; alignment self-checked; partition + k=4/5 search
./.venv/Scripts/python.exe recon/scratch/probe_c4ae.py
./.venv/Scripts/python.exe recon/scratch/probe_c4ag.py

# R10: the all-ones sweep, cover enumeration, and the affine test
./.venv/Scripts/python.exe recon/scratch/probe_c4aj.py
./.venv/Scripts/python.exe recon/scratch/probe_c4ak.py
./.venv/Scripts/python.exe recon/scratch/probe_c4ai.py

# R11: 121 single-star probes -> each flop's cell set
./.venv/Scripts/python.exe recon/scratch/probe_c4an.py

# R12: the published method's own search, with region sizes unconstrained
./.venv/Scripts/python.exe recon/scratch/probe_c4ao.py

# the project's full gate suite (expects ALL GATES: PASS)
./.venv/Scripts/python.exe tools/checks/run_all.py
```

Raw outputs land in `recon/scratch/c4*_out.txt` and `recon/scratch/probe_c4an_progress.txt`.
The step record with the full narrative is `docs/steps/C4.md` (sections R6–R12); the plan and the
recorded deviation from Δ5 are in `docs/03_our_plan.md` §C4.
