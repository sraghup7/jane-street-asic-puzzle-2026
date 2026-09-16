# Dependencies

**Policy (locked, Q5a):** install what genuinely reduces risk; quote the size before pulling
anything big. No SkyWater PDK, no formal-verification toolchain.

**Verified against:** Python 3.11.15 in `.venv` (Windows 11, git-bash).

**Check this document is still true:**

```
.venv/Scripts/python tools/checks/check_hygiene.py
```

That gate enforces every claim in the tables below that is mechanically checkable.

---

## 1. Python — required

Every package here is *imported by shipped code*, and every third-party module imported by shipped
code is listed in `requirements.txt`. The gate checks both directions, so the two cannot drift apart.

| Package | Version | Imported by | Role |
|---|---|---|---|
| `gdstk` | 1.0.1 | `tools/inventory.py`, `tools/render.py`, and the pipeline stages A1–A5, B1 | GDSII read/write. The pipeline's only source of geometry: layer census, in-master pin labels and pin shapes, and the instance/transform table. |
| `klayout` | 0.30.12 | `tools/kl_recon.py`, and pipeline stages B2/B3 | Connectivity extraction engine. This is differentiator **Δ3** — the path the published work left untested. (Written with the Greek letter here and in `requirements.txt`, not bare "D3", to avoid collision with Phase D's own stage numbering — `solve`/`uniqueness`/`load-bearing`/`answer` are D1-D4 in `docs/verification.md` §15.) |
| `matplotlib` | 3.11.2 | `tools/render.py` | Layer rasterisation for Step 1 recon only. Not part of the pipeline. |

## 2. Non-Python — required

| Tool | Version | Location | Role |
|---|---|---|---|
| `iverilog` | 12.0 | `C:\iverilog\bin\iverilog` (on PATH) | Compiles the emitted structural Verilog + our behavioural cell models. Gates C1, C2, E1, E2. |
| `vvp` | 12.0 | `C:\iverilog\bin\vvp` (on PATH) | Runs the compiled simulation and writes the VCD/`$display` output compared against `example_inputs.vcd`. |

`iverilog` is not installable via pip and is not vendored here; the gate reports its version and
fails if it is absent, so a missing simulator cannot silently skip C1/C2.

Verilator exists only inside WSL and is **not** used. `iverilog` covers every simulation this
project needs, at the scale of a few hundred cycles on a ~740-cell netlist.

## 3. Considered, never adopted

None of these were ever imported by shipped code, and none are in `requirements.txt`. Step F1's
dead-code gate closed the question for each at the pipeline's completion.

| Package | Version | Status |
|---|---|---|
| `numpy` | 2.4.6 | Not adopted. The pipeline's geometry path is integer-DBU throughout and never needed it. |
| `scipy` | 1.17.1 | Not adopted. No stage needed a spatial index. |
| `networkx` | 3.6.1 | Not adopted. The netlist graph in B2–B5 and C3 is small; a hand-written adjacency structure sufficed and avoided the dependency. |
| `pandas` | 3.0.5 | Not adopted. Would have been used for tabular recon output; we emit JSON instead. |
| `shapely` | 2.1.2 | Not adopted, and deliberately so — see §4. |

`tools/inventory.py::env_inventory()` *probes* for these packages and records their versions in
`recon/inventory.json`. That is an observation of the machine, not a dependency: the probe uses
`importlib` and nothing imports the packages. The gate's import check parses with `ast`, so probe
strings are never mistaken for imports.

## 4. Deliberately not depended on

This is the enforced core of the project's approach. The gate fails if any of these appears as an
import anywhere under `tools/`, or as a token under `tools/puzzle/**` or `requirements.txt`.

| Not used | Why |
|---|---|
| `shapely`, or any polygon-merge geometry library | Its `unary_union` + union-find extractor is the published approach (prohibited, P1). Our connectivity comes from KLayout's engine (D3); our geometry path is integer-DBU. |
| The SkyWater PDK — no `libs.ref`, no `*.tlef`, no `primitives.v` | Pins from a technology LEF and models from PDK primitives are the published approach (P2, P3), and three of their worst documented bugs came from that reference file. Our pins come from the chip's own in-master labels (D2); our layer stack from its own via masters (D1). |
| `yosys`, `sby`, `SymbiYosys`, `bitwuzla` | A formal `cover(success)` is the published solve (P4). Ours is a constraint search we wrote, with uniqueness by exhaustive enumeration (D4). |
| `z3`, `pysat`, or any other third-party solver | Same reason. "Two of our own implementations agree, and the space was exhausted" is stronger evidence than a library reporting UNSAT, and it keeps D4's claim literally true. |
| `verilator` | Present only inside WSL. Not needed at this scale (see §2). |

## 5. Scoping note — where the "not used" rule is enforced

The rule is enforced over the **implementation surface**: every `.py` under `tools/`, plus
`requirements.txt`. It is deliberately **not** applied to `docs/**`.

The reason is that the two requirements are otherwise contradictory. `docs/02_known_solution.md`
must *name* these tools, because describing what the published work did is its entire purpose;
`docs/03_our_plan.md` must name them in its prohibited list. A gate that forbade the words in prose
would force the documentation to become vague exactly where it must be precise. The enforceable
invariant is therefore about **code and declared dependencies** — which is also the only place a
dependency can actually take hold.

`docs/03_our_plan.md`'s prohibited list is separately gated by `tools/checks/check_step3.py`, which
asserts that the plan names all five prohibited method families *and* that each declared
differentiator maps to a step that exists.

### Two deliberate weaknesses in the token check

Recorded because both were found by fault injection, not by inspection:

1. **The negation exemption is line-local and naive.** A line carrying a forbidden token is exempt
   if the *same line* also contains a negation word (`no`, `not`, `never`, …). So a stray "no" in a
   comment silently exempts that line. This was observed directly: an injected `LEAK = 'bitwuzla'`
   escaped detection because the injected comment itself contained the word "no".
   *Why it is tolerable:* the primary net is invariant 1, which parses with `ast` and therefore
   catches any forbidden import regardless of comments. The token check is a secondary net for
   non-import drift (a hard-coded PDK path, for instance).
   *If it ever matters,* replace the exemption with an explicit allow-list of `file:line` pairs that
   must be kept up to date by hand.

2. **The token check cannot tell a declaration from a usage.** `tools/puzzle/__init__.py` names
   every forbidden dependency in its docstring, on purpose. That is why the exemption exists at all.

## 6. Reproduce

```
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python tools/checks/run_all.py          # every gate, in order
```
