"""Our own reverse-engineering pipeline for the Jane Street ASIC puzzle.

Nothing in this package may import from the SkyWater PDK, from `yosys`/`sby`, or from
`shapely`. The layer stack, the pin model and the solve are all derived from the puzzle
artifacts themselves -- see `docs/03_our_plan.md` for why (differentiators D1-D6).

Run stages with:

    python -m tools.puzzle --list
    python -m tools.puzzle <stage>

Every stage module exposes `main(argv: list[str]) -> int` and returns 0 on success.
"""

__all__ = ['cli']
