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
    ('pins-to-nets',      'netlist',    'B4', 'map each instance pin onto a net'),
    ('netlist-check',     'netlist',    'B5', 'single-driver / floating / port integrity checks'),
    ('emit',              'emit',       'B6', 'write build/puzzle.v and build/cells.v'),
    ('warmup-regression', 'connect',    'B7', 'reproduce warmup/01_netlist.v connectivity'),
    # --- Phase C: understand the chip ---------------------------------------------
    ('vcd-replay',        'simulate',   'C1', 'byte-exact replay of example_inputs.vcd'),
    ('warmup-equiv',      'simulate',   'C2', 'validate our cell models on the warmup adder'),
    ('decompose',         'analyse',    'C3', 'label the counters, shift register, comparator, ROM'),
    ('regions',           'regions',    'C4', 'symbolically decode the region-select logic'),
    ('regions-crosscheck','regions',    'C5', 're-derive the region map by a second route'),
    # --- Phase D: solve -----------------------------------------------------------
    ('solve',             'solve',      'D1', 'our own constraint solver over the region map'),
    ('uniqueness',        'solve',      'D2', 'exhaustive enumeration; two independent enumerators'),
    ('load-bearing',      'solve',      'D3', 'how much work the region constraint actually does'),
    ('answer',            'solve',      'D4', 'emit the vector in both bit orders'),
    # --- Phase E: confirm ---------------------------------------------------------
    ('confirm',           'simulate',   'E1/E2', 'success at cycle 126; the four wrong-input messages'),
    ('acceptance',        'accept',     'E3', 'assert AC1-AC6 against tools/target.py'),
    ('reproduce',         'accept',     'E4', 'full pipeline from clean in one command'),
]

GATES: list[tuple[str, str, str]] = [
    ('step1', 'tools/checks/check_step1.py', '54 checks: problem dossier vs raw artifacts'),
    ('step2', 'tools/checks/check_step2.py', '36 checks: known-solution study + target consistency'),
    ('step3', 'tools/checks/check_step3.py', '42 checks: the plan is internally sound'),
    ('target', 'tools/target.py', 'the acceptance contract is self-consistent'),
]


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
    print('GATES  (run from the repository root)')
    for name, path, purpose in GATES:
        cmd = f'python {path}'
        print(f'  {name:<8} {cmd:<36} {purpose}')
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
