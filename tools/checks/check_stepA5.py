#!/usr/bin/env python3
"""check_stepA5.py -- executable gate for step A5 (pin model coverage).

A5 is the step that closes Phase A, so it must not be a summary that reads back the
previous steps' own numbers. The gate therefore checks that A5's census reconciles against
A1 and A3 *independently*, and that every gap has a stated cause rather than a shrug:

  - every label in the standard-cell masters is classified, and the pin-label share equals
    A3's independently counted total;
  - A3's named pins, A4's modelled pins, and the geometry-bearing set are the same 560;
  - the 68 pins without routing-layer geometry are each attributed to the one stated cause,
    with no residual "unexplained" bucket;
  - the routing-layer reach is pinned: this is the fact Phase B is built on.

    .venv/Scripts/python tools/checks/check_stepA5.py
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.puzzle import pins as P                      # noqa: E402
from tools.checks._regen import regenerate             # noqa: E402

A1 = ROOT / 'recon' / 'derived' / 'layers.json'
A3 = ROOT / 'recon' / 'derived' / 'pin_names.json'
A4 = ROOT / 'recon' / 'derived' / 'pinmodel.json'
A5 = ROOT / 'recon' / 'derived' / 'pin_coverage.json'

EXPECTED_CENSUS = {'total': 876, 'pin_label': 803, 'cell_name': 73}
# 67/20 is li1, 68/20 is met1. Reading paths as well as polygons (review R2) recovers the
# antenna diode's supply rails, so 30 more pins reach the routing stack than before.
EXPECTED_REACH = {'67/20': 492, '68/20': 209}

results: list[tuple[str, str, object, object]] = []


def check(label, actual, expected):
    results.append(('PASS' if actual == expected else 'FAIL', label, actual, expected))


def main() -> int:
    for p in (A1, A3, A4, A5):
        if not p.exists():
            print(f'missing {p.relative_to(ROOT)} -- run A1..A5 stages first')
            return 1
    before = A5.read_text(encoding='utf-8')
    d = json.loads(before)
    a1 = json.loads(A1.read_text(encoding='utf-8'))
    a3 = json.loads(A3.read_text(encoding='utf-8'))
    a4 = json.loads(A4.read_text(encoding='utf-8'))

    # ---- 1. regenerability -----------------------------------------------------
    rc, produced, _ = regenerate('tools.puzzle.pins', ('COV_OUT',), 'pin-coverage')
    check('the pin-coverage stage exits 0', rc, 0)
    check('the artifact regenerates byte-identically',
          produced == before.encode('utf-8'), True)
    check('regeneration did not touch the working tree',
          A5.read_bytes() == before.encode('utf-8'), True)

    # ---- 2. the label census ---------------------------------------------------
    lc = d['label_census']
    check('label census total over the masters', lc['total'], EXPECTED_CENSUS['total'])
    check('label census by category', lc['by_category'],
          {'pin_label': EXPECTED_CENSUS['pin_label'], 'cell_name': EXPECTED_CENSUS['cell_name']})
    check('nothing is left unclassified', lc['unclassified'], [])
    check('the census is broken down per layer', lc['per_layer'],
          {'64/5': 71, '64/59': 71, '67/5': 521, '68/5': 140, '83/44': 73})
    check('the categories sum to the total',
          sum(lc['by_category'].values()), lc['total'])
    # reconcile against A1 (its own STD-scoped share) and A3 (its own count)
    a1_std = sum(r['by_cell_kind']['STD'] for r in a1['pairs'] if r['role'] == 'label')
    check('the census total equals A1\'s standard-cell label share', lc['total'], a1_std)
    check('the pin-label share equals A3\'s total', lc['by_category']['pin_label'],
          a3['totals']['pin_labels'])

    # ---- 3. named -> modelled -> geometry-bearing ------------------------------
    pc = d['pin_coverage']
    a3_keys = {(mn, pn) for mn, m in a3['masters'].items() for pn in m['pins']}
    a4_keys = {(mn, pn) for mn, m in a4['masters'].items() for pn in m['pins']}
    check('named pins', pc['named'], len(a3_keys))
    check('modelled pins', pc['modelled'], len(a4_keys))
    check('named == modelled', pc['named'], pc['modelled'])
    check('every modelled pin carries geometry', pc['with_geometry'], pc['modelled'])

    # ---- 4. every gap has a cause ----------------------------------------------
    # The antenna diode's supplies used to need a second cause here -- they carried no
    # routing-layer geometry only because reading `cell.polygons` alone dropped the met1 path
    # that draws their rail (review R2). With paths included they are routed pins, not a gap,
    # and the well-tie contact (VPB, drawn only on 64/16) is the sole remaining cause.
    causes = pc['exceptions_by_cause']
    check('exactly one cause for the 68 unrouted pins', len(causes), 1)
    check('the one cause covers all 68 pins', sum(causes.values()),
          pc['without_routing_geometry'])
    check('the well-tie cause accounts for 68', causes.get(
        'well-tie contact only (64/16); no routing-layer geometry'), 68)
    check('no antenna-diode cause remains (its supplies are routed via a met1 path)',
          'antenna diode: supply exists only as a 68/16 pin square' in causes, False)
    check('no residual unexplained bucket', 'unexplained' in causes, False)
    # and the arithmetic of the coverage split
    check('routed + unrouted == modelled',
          pc['with_routing_geometry'] + pc['without_routing_geometry'], pc['modelled'])

    # ---- 5. the fact Phase B depends on ---------------------------------------
    check('cell pins reach the routing stack at exactly two layers',
          d['routing_layer_reach_pins'], EXPECTED_REACH)
    check('li1 carries every routed pin',
          d['routing_layer_reach_pins']['67/20'], pc['with_routing_geometry'])
    check('met1 is reached by a small minority of pins',
          d['routing_layer_reach_pins']['68/20'] < d['routing_layer_reach_pins']['67/20'], True)

    # ---- 6. instance-level reach ---------------------------------------------
    ir = d['instance_reach']
    check('placed pins counted', ir['placed_pins'], 7897)
    check('placed pins with routing geometry', ir['placed_pins_with_routing_geometry'], 6955)
    check('instance coverage percent', ir['coverage_pct'], 88.07)
    check('instance reach is routeable in principle',
          ir['placed_pins_with_routing_geometry'] < ir['placed_pins'], True)

    # ---- 7. the stage's own checks --------------------------------------------
    check('the stage\'s own checks all passed',
          [c['check'] for c in d['checks'] if not c['passed']], [])
    check('the stage reports a PASS verdict', d['verdict'], 'PIN MODEL COVERAGE: PASS')

    # ---- report ---------------------------------------------------------------
    w = max(len(r[1]) for r in results)
    n_fail = 0
    for status, label, actual, expected in results:
        if status == 'FAIL':
            n_fail += 1
            print(f'  FAIL  {label:<{w}}')
            print(f'          got  {actual!r}')
            print(f'          want {expected!r}')
        else:
            print(f'  PASS  {label}')
    passed = sum(1 for r in results if r[0] == 'PASS')
    print()
    print(f'{passed} passed, {n_fail} failed, 0 skipped')
    if n_fail:
        print(f'STEP A5 GATE: FAIL ({n_fail} failing)')
        return 1
    print('STEP A5 GATE: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
