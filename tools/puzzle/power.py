#!/usr/bin/env python3
"""power.py -- C1's own power measurement: what can the replay actually see?

C1 matches 2808 output bits, and it is tempting to read a number that large as strength. It is
not a measure of strength; it is a count of comparisons. The reference is 294 idle cycles around
18 cycles of one constant message, so the replay constrains the design's *timing* tightly and its
message *content* weakly, and a netlist wrong in some other way could match it exactly.

The honest way to state an oracle's power is to measure what it misses, so this stage does the
experiment the Phase B review did by hand (F1: breaking the or-family and watching all 18 gates
pass) -- but systematically, one model at a time, and without touching the tree:

    for each of the 69 cell models: negate its function, rebuild build/cells.v in scratch,
    re-run the C1 replay, and count how many of the 312 cycles' outputs move.

A model whose negation moves nothing is a model C1 cannot vouch for. Those are the models the
warm-up equivalence (C2) exists to cover, and the count is the reason C2 is not redundant.

The consumers of the chip's one undriven net fall out of this as a cross-check: `a31oi_2` and
`a311o_2` -- exactly the two cells that read `n806` -- are both silent here, which is the same
conclusion the forced-run experiment in C1 reaches by a different route.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.puzzle import simulate as S

OUT_REPORT = ROOT / 'recon' / 'derived' / 'c1_power.json'
SCRATCH = ROOT / 'recon' / 'scratch' / 'power'

#: The two cells that read the chip's undriven net, from B5's own enumeration.
UNDRIVEN_CONSUMERS = ('sky130_fd_sc_hd__a311o_2', 'sky130_fd_sc_hd__a31oi_2')

#: F1's break, now a machine-checked expectation rather than a story: the or-family drives the
#: output path, so negating `or2_2` has to be visible.
F1_BREAK = 'sky130_fd_sc_hd__or2_2'

ASSIGN_RE = re.compile(r'^(\s*)assign\s+(\w+)\s*=\s*(.+?);\s*$', re.M)
FLOP_RE = re.compile(r'(<=|<)\s*(~?)D\s*;')


def split_modules(text: str) -> tuple[str, list[str]]:
    """(preamble, [module source ...]) -- enough structure to rebuild the file with one changed."""
    parts = re.split(r'(?m)^(?=module )', text)
    return parts[0], [p for p in parts[1:] if p.strip()]


def module_name(mod: str) -> str:
    return mod.split('(', 1)[0].replace('module', '').strip()


def negate(mod: str) -> tuple[str, str] | None:
    """A module with its function inverted, and a description of how.

    The whole right-hand side is wrapped rather than edited in place: `assign Y = ~(A | B);`
    gives the complement of the modelled function whichever shape the model has, and cannot
    silently fall foul of operator precedence the way `~A | B` would (the same trap that made
    the B6 oracle's first reference expression wrong).
    """
    m = ASSIGN_RE.search(mod)
    if m:
        return (mod[:m.start()] + f'{m.group(1)}assign {m.group(2)} = ~({m.group(3)});'
                + mod[m.end():], f'{m.group(2)} = ~(...)')
    m = FLOP_RE.search(mod)
    if m:
        return (mod[:m.start()] + f'{m.group(1)} {"" if m.group(2) else "~"}D;' + mod[m.end():],
                f'{m.group(1)} D inverted')
    return None


def stage_model_power() -> int:
    ref = S.read_reference()
    exp_O, exp_succ = ref['table']['O'], ref['table']['success']
    n = len(exp_O)
    base_text = S.CELLS.read_text(encoding='utf-8')
    head, modules = split_modules(base_text)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    print(f'cells                             : '
          f'{S.CELLS.relative_to(ROOT).as_posix()} ({len(modules)} modules)')
    print(f'reference                         : {n} cycles, '
          f'{sum(1 for b in exp_O if b != "00000000")} of them carrying a message byte')

    # ---- baseline: the harness must agree before a single mutation is tried ----------
    base_rows, base_rc, _ = S.run_simulation(S.OUT_TB, SCRATCH / 'baseline.vvp')
    base_bad = sum(8 for k in range(min(n, len(base_rows))) if base_rows[k]['O'] != exp_O[k]) \
        + sum(1 for k in range(min(n, len(base_rows))) if base_rows[k]['success'] != exp_succ[k])
    print(f'baseline replay                   : {len(base_rows)} cycles, iverilog rc={base_rc}, '
          f'{base_bad} output bit(s) wrong')

    # ---- one mutation per model ------------------------------------------------------
    per_model, counts = [], {'mutated': 0, 'caught': 0, 'silent': 0, 'not_mutable': 0}
    mutation_was_isolated = 0
    for i, mod in enumerate(modules):
        name = module_name(mod)
        neg = negate(mod)
        if neg is None:
            counts['not_mutable'] += 1
            per_model.append({'model': name, 'mutation': None, 'cycles_wrong': None,
                              'first_wrong_cycle': None, 'caught': None,
                              'note': 'a physical-only master: no logic to negate'})
            print(f'  {name:<34} -- no logic (physical-only master)')
            continue
        mutated, how = neg
        variant = head + ''.join(mutated if j == i else m for j, m in enumerate(modules))
        if variant == base_text:
            raise AssertionError(f'negating {name} produced no change to the file')
        mutation_was_isolated += 1
        path = SCRATCH / 'cells_mut.v'
        path.write_text(variant, encoding='utf-8', newline='\n')
        saved, S.CELLS = S.CELLS, path
        try:
            rows, rc, _ = S.run_simulation(S.OUT_TB, SCRATCH / 'mutant.vvp')
        finally:
            S.CELLS = saved
        if rc != 0 or len(rows) != n:
            raise RuntimeError(f'negating {name} broke the build (rc={rc}, {len(rows)} cycles)')
        o_bad = [k for k in range(n) if rows[k]['O'] != exp_O[k]]
        s_bad = [k for k in range(n) if rows[k]['success'] != exp_succ[k]]
        wrong = len(o_bad) + len(s_bad)
        counts['mutated'] += 1
        counts['caught' if wrong else 'silent'] += 1
        first = (o_bad + s_bad) and min(o_bad + s_bad) or None
        per_model.append({'model': name, 'mutation': how, 'cycles_wrong': wrong,
                          'first_wrong_cycle': first, 'caught': wrong > 0, 'note': None})
        print(f'  {name:<34} {how:<14} {"CAUGHT" if wrong else "silent":<7} '
              f'{wrong:>3} cycle(s)')

    caught = {r['model'] for r in per_model if r['caught']}
    silent = {r['model'] for r in per_model if r['caught'] is False}

    checks = [
        {'check': 'the baseline replay agrees with the reference before any mutation',
         'passed': len(base_rows) == n and base_bad == 0},
        {'check': 'every mutable model was rebuilt with its function negated',
         'passed': mutation_was_isolated == counts['mutated'] and counts['mutated'] > 0},
        {'check': 'the physical-only masters are the only ones with no logic to negate',
         'passed': counts['not_mutable'] == 3 and counts['mutated'] + counts['not_mutable']
                   == len(modules)},
        {'check': 'every model is classified exactly once',
         'passed': counts['caught'] + counts['silent'] == counts['mutated']},
        {'check': 'the F1 experiment s break (negated or2_2) is caught by C1',
         'passed': F1_BREAK in caught},
        {'check': 'and it is caught early, not merely somewhere',
         'passed': next((r['first_wrong_cycle'] for r in per_model
                         if r['model'] == F1_BREAK), 999) < 10},
        {'check': 'C1 is partial by measurement, not by assumption: some models are silent',
         'passed': counts['silent'] > 0},
        {'check': 'both consumers of the chip s undriven net are silent here',
         'passed': all(m in silent for m in UNDRIVEN_CONSUMERS)},
    ]

    report = {
        'generated_by': 'tools/puzzle/power.py::stage_model_power',
        'purpose': 'measure what the C1 replay can and cannot see, by negating each cell model '
                   'in a scratch rebuild of build/cells.v and counting the output cycles that '
                   'move. A model whose negation moves nothing is a model C1 cannot vouch for.',
        'reference': {'path': ref['ref']['path'], 'sha256': ref['ref']['sha256'],
                      'cycles': n, 'message_cycles': sum(1 for b in exp_O
                                                         if b != '00000000')},
        'method': {'mutation': 'wrap the modelled output expression in a complement: '
                               'assign <out> = ~(<expr>); or, for the sequential models, '
                               'invert the data input',
                   'applied_to': 'a scratch copy of build/cells.v; the tree is never modified',
                   'harness': 'tools/puzzle/simulate.py::run_simulation against build/replay_tb.v '
                              'and build/puzzle.v',
                   'metric': 'cycles whose O or success differs from the reference, over 312'},
        'baseline': {'cycles': len(base_rows), 'iverilog_rc': base_rc,
                     'output_bits_mismatched': base_bad},
        'totals': {'modules': len(modules), **counts},
        'per_model': sorted(per_model, key=lambda r: r['model']),
        'observations': [
            {'observation': 'the F1 break is machine-checked here', 'detail':
             f'negating {F1_BREAK} moves {next((r["cycles_wrong"] for r in per_model if r["model"] == F1_BREAK), None)} '
             f'output cycles, starting at cycle '
             f'{next((r["first_wrong_cycle"] for r in per_model if r["model"] == F1_BREAK), None)}. '
             f'In the Phase B review the same break passed all 18 gates; C1 is the first oracle '
             f'in the project that sees it.'},
            {'observation': 'the undriven net s two consumers are silent',
             'detail': 'both a311o_2 and a31oi_2 -- the cells that read n806 -- move nothing '
                       'when negated, which is the forced-run experiment s conclusion reached '
                       'from the other direction. E1 re-checks it on the winning vector, where '
                       'the design reads its inputs for real.'},
            {'observation': 'a large comparison count is not strength', 'detail':
             f'{counts["caught"]} of {counts["mutated"]} negations are visible and '
             f'{counts["silent"]} are not. The 2808-bit figure counts comparisons; this counts '
             f'what a wrong netlist could get away with.'},
        ],
        'checks': checks,
    }
    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    OUT_REPORT.write_text(json.dumps(report, indent=2, sort_keys=False) + '\n',
                          encoding='utf-8', newline='\n')
    print()
    print(f'models negated                    : {counts["mutated"]} of {len(modules)} '
          f'({counts["not_mutable"]} physical-only)')
    print(f'C1 caught                         : {counts["caught"]}')
    print(f'C1 silent (a wrong model would pass): {counts["silent"]}  {sorted(silent)}')
    print(f'report                            : {OUT_REPORT.relative_to(ROOT).as_posix()}')
    failed = [c['check'] for c in checks if not c['passed']]
    print()
    print(f'C1 MODEL POWER: {"PASS" if not failed else "FAIL " + str(failed)}')
    return 0 if not failed else 1


def main(argv: list[str] | None = None) -> int:
    stage = (argv or ['model-power'])[0]
    if stage != 'model-power':
        print(f'power.py has no stage {stage!r}', file=sys.stderr)
        return 2
    return stage_model_power()


if __name__ == '__main__':
    raise SystemExit(main())
