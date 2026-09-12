"""Our own reverse-engineering pipeline for the Jane Street ASIC puzzle.

Nothing in this package may depend on:

  - the SkyWater PDK, or any file from it (no libs.ref, no .tlef, no primitives.v)
  - any polygon-merge geometry library (no shapely, no unary_union)
  - yosys, sby, SymbiYosys or bitwuzla (no formal cover solver)
  - z3 or any other third-party solver (no borrowed search)

The layer stack, the pin model and the solve are all derived from the puzzle artifacts
themselves -- see `docs/03_our_plan.md` for why (differentiators D1-D6) and
`docs/deps.md` sec.4 for the reasoning. `tools/checks/check_hygiene.py` enforces all of it.

Run stages with:

    python -m tools.puzzle --list
    python -m tools.puzzle <stage>

Every stage module exposes `main(argv: list[str]) -> int` and returns 0 on success.
"""

__all__ = ['cli']
