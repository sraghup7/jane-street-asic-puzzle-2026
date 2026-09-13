# Spike: KLayout's connectivity engine (B2)

**Question:** can a connectivity engine, configured *only* from our own derived data, recover
the netlist of a design whose correct netlist we hold?
**Answer: yes.** `tools/checks/check_stepB2.py` → **24/24 PASS**, and the partition the engine
produces is identical to `01_netlist.v`'s.

---

## 1. Why this spike comes first

B2 exists because the plan flags it as the project's biggest bet and the project's hard stop.
The published work had KLayout's built-in extractor available and **abandoned it untested**,
hand-rolling a shape graph instead. That makes it a genuine differentiator (**Δ3**) — but only
if it actually works, and only if "it works" means the netlist comes out right. So the engine
is pointed at `warmup/04_final.gds`, where a correct netlist exists to check against, and
**not** at the 1.4 MB puzzle until it earns that.

## 2. What the spike produced

```
warm-up placements (SATD cells)     : 230        cell types : 18
engine: KLayout LayoutToNetlist on 1000 dbu/um
  conductors connected              : 6
  via rules applied (as 2 connects) : 5
  netlist extracted                 : True (27 circuits)

functional pins probed              : 285
  pins with no conductor geometry   : 0
  probe errors                      : 0
  label points disagreeing on a net : 0
  pins the engine left unconnected  : 0

netlist instance -> GDS placement : 230 mapped, 0 unmapped

partition comparison (nets as sets of (instance, pin), canonicalised)
  our nets          : 84          netlist nets      : 84
  our terminals     : 285         netlist terminals : 285
  net signatures only in ours     : 0
  net signatures only in the ref  : 0
  reference terminals compared    : 285 / 285  (100.0%)
```

No net renaming is needed beyond canonicalising each net to the *set* of its terminals: the
two partitions are equal as sets of sets. The engine, the layer stack, the rule set and the
terminals are all ours; only the geometry is shared.

## 3. How it is wired (and it is wired from our data)

| ingredient | source |
|---|---|
| conductor layers | A1 `layers.json` roles `routing` + `local_wire` |
| cut layers and the pairs they bridge | A2 `via_pairs.json` — the rule set A2 *proved*, not a hand-written map |
| terminals (which geometry is a pin) | A4 `pinmodel.json` |
| placements and transforms | B1's convention, re-derived on the warm-up |
| instance identity | the DEF, bridged by cell + lower-left corner |

The engine is therefore asked exactly one question — *what is connected to what* — and is
never asked to infer device structure or pin identity. That division is the point: it is why
this is not a re-implementation of the published pipeline.

## 4. API facts, measured rather than assumed

KLayout 0.30.12's Python API differs from the older recipes in ways that cost real time:

1. **`connect` takes one or two arguments; there is no 3-argument via form.** So each A2 rule
   becomes two pairwise connections, which transitively join the two conductors through the
   cut: `connect(metal_a, cut)` and `connect(cut, metal_b)`.
2. **`connect` wants `Region` objects, not layer indices** — build them with
   `l2n.make_layer(ly.layer(l, dt))`.
3. **`extract_netlist()` returns the `LayoutToNetlist` itself**, and the netlist comes from
   the `.netlist()` accessor.
4. **`probe_net` raises unless extraction has already happened.**
5. **`Net#cluster_id` is a property, not a method**, and it is the stable net identity.
6. **`Shape#bbox()` works for every shape type**; `shape.polygon` is `None` for boxes.

## 5. Three bugs the spike found — two of them mine, one of them the kind that passes

### 5.1 Probe points from a bounding rect can land in a hole (A4's `rect` is a bbox)

The first run left **32 pins unconnected**. The cause was precise: A4's per-shape `rect` is the
**bounding box** of the polygon, and some pins are comb- or ring-shaped. `clkbuf_16`'s `X` pin
is a **68-point polygon** whose bounding rect `[2.28, 0.28, 9.025, 2.46]` has a centre that lies
**inside no polygon at all** — so a centre probe finds nothing.

Measured fix: probe at the pin's **label positions** instead. Every label position was verified
to sit inside a polygon of its pin (that is also how A4 seeded its trace). After the fix: 0
unconnected pins, 0 probe errors, and 0 cases where the label points of one pin disagreed about
which net they are on — which is itself a small consistency result.

### 5.2 Instance identity between the netlist and the DEF

The netlist names instances `\add0/_32_`; the DEF names them `add0/_32_`. The bridge is exactly
`lstrip('\\')`. Guess wrong in either direction and the comparison silently loses instances:
matching raw names mapped **154 of 230** (the physical-only cells), and stripping the hierarchy
(`split('/')[-1]`) mapped the same 154. Both leave all **76 functional instances** out.

### 5.3 The comparison could pass while covering almost nothing

With only the 154 pinless instances mapped, the comparison ran over **4 nets and 6 terminals**
and reported **PASS**. That is the failure mode this project has now hit three times
(`check_step3`'s vacuous uniqueness check; the audit's masked downstream gates; here). Two
guards were added and are asserted by the gate:

* **coverage of the reference** — every terminal the reference connects must be compared
  (285/285, not 6/6);
* **an absolute floor** on the compared terminal count (≥ 250), so a *shrinking* comparison
  cannot pass either.

The restricted comparison itself is deliberate: the reference omits pins it leaves unused, so
both sides are restricted to the terminals the reference connects. A *mis*-connection still
shows up, because it changes which of those terminals share a net. In the event the restriction
removed nothing — our 285 probed terminals are exactly the reference's 285.

## 6. Verdict and what it licenses

**KLayout's engine works, driven entirely from our derived data, and it reproduces the
reference partition exactly.** B2′ (the hand-rolled shape-graph fallback in §6.5) is **not
needed** and will not be written; the plan's kill criterion did not trigger.

What that licenses, and what it does not:

* **B3 may proceed on the puzzle** — the same path, same derived stack, just 1.4 MB instead of
  304 KB, with the pin model supplying terminals for 1618 instances rather than 230.
* It does **not** license skipping B7. B7 is the same comparison run through the whole
  S0→B6 chain, and it is still the phase gate.
* It does **not** establish that the puzzle's netlist is *correct* — only that the extraction
  reproduces a known-correct one. Correctness of the puzzle's netlist is C1's business (the VCD
  replay) and E1/E2's.

## 7. Reproduction

```
.venv/Scripts/python -m tools.puzzle connect      # the spike; writes recon/derived/warmup_netlist.json
.venv/Scripts/python tools/checks/check_stepB2.py # 24/24
```
