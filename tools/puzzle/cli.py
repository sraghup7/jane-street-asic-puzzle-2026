#!/usr/bin/env python3
"""cli.py -- single entry point for every pipeline stage.

    python -m tools.puzzle --list          show the stage table and its status
    python -m tools.puzzle --gates         show the acceptance gates and their commands
    python -m tools.puzzle <stage> [...]   run one stage

The registry is the machine-readable half of `docs/03_our_plan.md` sec.7: each stage names
the plan step that produces it. `--list` reports whether the module exists yet, so the
table is honest about what is built and what is still planned.
"""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# (stage, module, plan step, purpose)
STAGES: list[tuple[str, str, str, str]] = [
    # --- Phase A: the chip describes itself --------------------------------------
    ('layers',            'layers',     'A1', 'classify every (layer,datatype) into a role'),
    ('via-pairs',         'layers',     'A2', 'derive conductor layer pairs from the via masters'),
    ('pin-names',         'pins',       'A3', 'read pin names from in-master pin labels'),
    ('pin-geom',          'pins',       'A4', 'master-local pin geometry, calibrated on warmup/'),
    ('pin-coverage',      'pins',       'A5', 'report pins named but lacking geometry'),
    # --- Phase B: instances, connectivity, netlist --------------------------------
    ('instances',         'instances',  'B1', 'placement table with exact affine transforms'),
    ('connect',           'connect',    'B2/B3', 'connectivity extraction (spike on warmup first)'),
    ('nets',              'connect',    'B3', 'full connectivity on the puzzle; no orphan polygons'),
    ('pins-to-nets',      'netlist',    'B4', 'map each instance pin onto a net'),
    ('netlist-check',     'netlist',    'B5', 'single-driver / floating / port integrity checks'),
    ('emit',              'emit',       'B6', 'write build/puzzle.v and build/cells.v'),
    ('cells',             'cells',      'B6', 'write build/cells.v (our behavioural models)'),
    ('warmup-regression', 'warmup',    'B7', 'reproduce warmup/01_netlist.v connectivity'),
    # --- Phase C: understand the chip ---------------------------------------------
    ('vcd-replay',        'simulate',   'C1', 'byte-exact replay of example_inputs.vcd'),
    ('model-power',       'power',      'C1', 'how much a wrong cell model would show up in C1'),
    ('warmup-equiv',      'equiv',      'C2', 'validate our cell models on the warmup adder'),
    ('warmup-power',      'power',      'C2', 'how much a wrong cell model would show up in C2'),
    ('decompose',         'analyse',    'C3', 'label the counters, shift register, comparator, ROM'),
    ('region-map',        'verdict',    'C4', 'find the partition the eleven latches form, with its controls'),
    ('rejections',        'verdict',    'C5', 'boards that satisfy every visible rule and are rejected'),
    ('winning',           'verdict',    'E1', 'the winning vector driven through the netlist'),
    ('confirm',           'simulate',   'E2', 'the four wrong-input messages under iverilog'),
    # --- Phase D: solve -----------------------------------------------------------
    # One computation, four plan steps: each stage name runs the whole D phase and prints its
    # section. Kept as four entries so the stage table still lines up with the plan.
    ('solve',             'solve',      'D1', 'our own solver over the five constraints (runs all of D)'),
    ('uniqueness',        'solve',      'D2', 'two independent enumerations, run to completion'),
    ('load-bearing',      'solve',      'D3', 'the bounded count without the region constraint'),
    ('answer',            'solve',      'D4', 'the vector in both bit orders, against the contract'),
    # --- Phase E: confirm ---------------------------------------------------------
    ('acceptance',        'accept',     'E3', 'assert AC1-AC6 against tools/target.py'),
    ('reproduce',         'accept',     'E4', 'full pipeline from clean in one command'),
]

# Optional one-liners for known gates. The gate *list* is discovered from the filesystem
# by tools/checks/run_all.py -- that is the single source of truth, so a newly added gate
# shows up here automatically and this table cannot drift out of date. A gate with no
# entry prints a visible '-' rather than silently going missing.
GATE_NOTES: dict[str, str] = {
    'target': 'the acceptance contract is self-consistent',
    'hygiene': 'dependency policy: no PDK, no external solver, no bare imports',
    'step1': 'problem dossier vs the raw artifacts',
    'step2': 'known-solution study + target consistency',
    'step3': 'the plan is internally sound',
    'stepA1': 'layer roles, derived from the chip itself',
    'stepA2': 'the connectivity rule set, re-derived from the GDS',
    'stepA3': 'pin names, read from the cell masters',
    'stepA4': 'pin geometry, calibrated on the warm-up',
    'stepA5': 'pin model coverage',
    'recompute': 'independent recomputation of the A-phase numbers',
    'stepB1': 'the placement table: transform convention and anchor semantics',
    'stepB2': 'the connectivity engine, validated against the warm-up partition',
    'stepB3': 'full connectivity on the chip; every conductor shape accounted for',
    'stepB4': 'every instance pin mapped onto a net',
    'stepB5': 'netlist integrity, and the documented interface read from the layout',
    'stepB6': 'the emitted Verilog round-trips, and the cell models match their masters',
    'stepB7': 'the whole chain, on a design whose real netlist we hold',
    'stepC4': 'the instrument, the verdict flop, and the candidate partition with its controls',
    'stepC5': 'the hidden constraint exists; and how little a rejection says about the map',
    'stepD': 'the answer derived by us: two enumerators agree, and both bit orders match',
    'stepE1': 'success at cycle 126, and the four wrong-input messages',
}


def discovered_gates() -> list[tuple[str, str]]:
    """(name, repo-relative path) for every gate, via run_all's own discovery."""
    from tools.checks.run_all import gate_paths
    return [(name, str(path.relative_to(ROOT))) for name, path in gate_paths()]


def _module_status(module: str) -> str:
    try:
        importlib.import_module(f'tools.puzzle.{module}')
        return 'ready'
    except ModuleNotFoundError:
        return 'planned'


def cmd_list() -> int:
    print('STAGES')
    print(f"  {'stage':<20} {'module':<12} {'step':<6} {'status':<8} purpose")
    for stage, module, step, purpose in STAGES:
        print(f'  {stage:<20} {module:<12} {step:<6} {_module_status(module):<8} {purpose}')
    print()
    print('GATES  (the list is discovered, not declared; run them from the repository root)')
    gates = discovered_gates()
    for name, path in gates:
        print(f'  {name:<10} {("python " + path):<40} {GATE_NOTES.get(name, "-")}')
    print(f'  ({len(gates)} gates; `python tools/checks/run_all.py` runs them all)')
    return 0


def cmd_run(stage: str, argv: list[str]) -> int:
    match = [s for s in STAGES if s[0] == stage]
    if not match:
        print(f'unknown stage: {stage!r}', file=sys.stderr)
        print('run `python -m tools.puzzle --list` to see the stage table', file=sys.stderr)
        return 2
    _, module, step, _ = match[0]
    try:
        mod = importlib.import_module(f'tools.puzzle.{module}')
    except ModuleNotFoundError:
        print(f"stage {stage!r} is planned for step {step} and is not implemented yet "
              f"(tools/puzzle/{module}.py does not exist)",
              file=sys.stderr)
        return 3
    if not hasattr(mod, 'main'):
        print(f'tools/puzzle/{module}.py has no main(argv) entry point', file=sys.stderr)
        return 4
    # The stage name is passed as argv[0] so that two stages can share one module
    # (e.g. `layers` and `via-pairs` both live in tools/puzzle/layers.py).
    return mod.main([stage] + argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog='python -m tools.puzzle',
        description='Jane Street ASIC puzzle -- our own reverse-engineering pipeline')
    parser.add_argument('--list', action='store_true', help='show the stage table')
    parser.add_argument('--gates', action='store_true', help='show the acceptance gates')
    parser.add_argument('stage', nargs='?', help='stage to run')
    parser.add_argument('rest', nargs=argparse.REMAINDER, help='arguments for the stage')
    args = parser.parse_args(argv)

    if args.list or args.gates:
        return cmd_list()
    if not args.stage:
        parser.print_help()
        return 0
    return cmd_run(args.stage, args.rest)


if __name__ == '__main__':
    raise SystemExit(main())
