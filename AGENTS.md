# AGENTS.md — Jane Street ASIC Puzzle: project protocol

> **This file is the governing protocol for this workspace. Read it before doing anything.**
> It is derived from a direct user instruction and applies to *every* session/chat in
> `E:\Projects\Jane-Street-ASIC-Puzzle`. Do not skip, reorder, merge, or one-shot the steps.

## Mission

Reverse-engineer the Jane Street ASIC puzzle (2026) using **our own approach** — one that is
**not** the published approach — and land on the already-known correct answer, as a
technically defensible artifact (target: a public writeup, then PhD outreach).

## Hard rules

1. **No copying the published solution.** Similarity of *approach family* is tolerated;
   directly re-treading the known writeup's pipeline is prohibited. If a step would just be
   re-implementing what is already public, redesign it.
2. **Explicitly different differentiators must be declared up front** and recorded in the plan
   document (e.g. where pin geometry comes from, how the solve is performed). Say what we do
   differently and why it is ours.
3. **Reduced dependency surface is a feature.** Prefer an approach that leans on the puzzle
   artifacts themselves plus already-present tooling, rather than pulling in the same
   heavyweight toolchain the published solution used.
4. **One step at a time.** Never one-shot the plan. Execute a single step, verify it and all
   preceding steps, confirm it is clean and solid, *then* advance.
5. **Verification is mandatory and executable.** Every step ends with a concrete check whose
   result can be pasted as evidence. "Looks right" is not verification.
6. **Regression discipline.** `asic-puzzle-2026/` is read-only upstream input. Our code,
   netlists, tests, and docs live outside it.
7. **Honest reporting.** If a step fails, report the failure and the real tool output. Never
   substitute plausible-looking fabricated results.

## The six steps

| # | Step | Deliverable |
|---|------|-------------|
| 1 | Study the cloned puzzle repo **and** the Jane Street blog post | `docs/01_problem.md` — precise statement of the problem, inputs, interfaces, constraints, resources on hand |
| 2 | Study the known/published solution | `docs/02_known_solution.md` — precise mechanics of how it was solved, its tools, its failure modes |
| 3 | Plan our **own** end-to-end solution | `docs/03_our_plan.md` — no vague hand-waving; every minute detail; split into discrete, individually debuggable steps |
| 4 | Execute the plan **one step at a time** | Code + per-step verification evidence; re-verify all prior steps after each step |
| 5 | Match against the known answer | If mismatch → **return to step 2 and debug from there**. If match → full project verification: no stray code, no badly written/unoptimized code. Output = a clean, finished solution folder |
| 6 | The blog for the complete solution | Planned separately, **after** steps 1–5 are done |

## Definition of done for step 5

- The recovered truth matches the known answer on every acceptance criterion agreed in the plan.
- Repository is clean: no dead code, no scratch files, no unsourced artifacts, no TODOs.
- Documented reproduction path exists (fresh clone → commands → same result).

## Decisions (locked 2026-09-12)

| # | Decision | Choice |
|---|----------|--------|
| Q1 | Differentiator | **(a) Full-stack own approach** — recover pin geometry from the GDS's own standard-cell masters (no PDK/LEF dependency); recover the hidden region map by analysing the LUT netlist itself; solve with our own constraint solver (no SymbiYosys/bitwuzla) |
| Q2 | Acceptance criteria | **(a) Everything** — exact 121-bit vector, `success` at cycle 126, `(* TWO STARS *)`, all four wrong-input messages, byte-exact `example_inputs.vcd` replay, and the "JS" region map recovered independently |
| Q3 | Step-4 pacing | **(b) Stop for explicit go-ahead after every verified step** |
| Q4 | Repo layout | **(a)** Own git repo; `asic-puzzle-2026/` is read-only upstream input (gitignored, never modified); **private** |
| Q5 | Toolchain | **(a)** Install what genuinely reduces risk, including the SkyWater PDK if needed — **quote sizes before any large pull** |

The Jane Street "don't use AI" request is **not** treated as binding (user decision, 2026-09-12);
it is recorded in `docs/01_problem.md` as historical context only.

## Standing constraints / notes

- Jane Street's own rules (published with the puzzle) are recorded in `docs/01_problem.md`.
  Note their request about AI tooling and its status after submissions closed (2026-09-04).
- The known answer is established; submissions are closed. The artifact's value is
  methodological, not competitive.
