#!/usr/bin/env python3
"""check_stepA4.py -- executable gate for step A4 (master-local pin geometry).

A4 is where a subtle geometric mistake silently produces a wrong netlist, so the gate
leans on invariants that are hard to satisfy accidentally:

  1. regenerability -- the stage reproduces the artifact byte for byte;
  2. the warm-up calibration -- 18/18 cell types agree between our layout-derived pin
     names and the vendor netlist's port names, via the same code path;
  3. the anti-merge invariant -- if the trace over-merges, distinct pins of one master end
     up with identical geometry. The only legitimate such case is a supply pin and its
     body tie, whose labels are drawn at the same coordinates: exactly (VGND, VNB) in
     exactly 68 masters. This invariant is what caught the bug this step fixed;
  4. geometry sanity -- rects inside their master, DBU-aligned, none empty;
  5. agreement with A3 on which (master, pin) pairs exist.

    .venv/Scripts/python tools/checks/check_stepA4.py
"""
from __future__ import annotations

import io
import json
import sys
from collections import defaultdict
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.puzzle import pins as P                      # noqa: E402

from collections import Counter, defaultdict          # noqa: E402

A2 = ROOT / 'recon' / 'derived' / 'via_pairs.json'
A3 = ROOT / 'recon' / 'derived' / 'pin_names.json'
A4 = ROOT / 'recon' / 'derived' / 'pinmodel.json'
BBOX_TOL = 0.002
SUPPLIES = {'VGND', 'VNB', 'VPB', 'VPWR'}      # supply + body-tie pins

EXPECTED_TOTALS = {'masters': 69, 'pins': 560, 'pins_with_geometry': 560,
                   'pin_shapes': 1790,
                   'seed_methods': {'same_layer_number': 492, 'other_layer': 68},
                   'warmup_cell_types': 18, 'warmup_cell_types_all_match': True}
# the cell types whose exact agreement with the vendor netlist is most load-bearing
SPOTLIGHT = {
    'nand2_2': 'A B VGND VNB VPB VPWR Y',
    'dfrtp_2': 'CLK D Q RESET_B VGND VNB VPB VPWR',
    'mux2_1': 'A0 A1 S VGND VNB VPB VPWR X',
    'tapvpwrvgnd_1': 'VGND VPWR',
    'decap_3': 'VGND VNB VPB VPWR',
}

results: list[tuple[str, str, object, object]] = []


def check(label, actual, expected):
    results.append(('PASS' if actual == expected else 'FAIL', label, actual, expected))


def main() -> int:
    for p in (A2, A3, A4):
        if not p.exists():
            print(f'missing {p.relative_to(ROOT)} -- run A1..A4 stages first')
            return 1
    before = A4.read_text(encoding='utf-8')
    d = json.loads(before)
    a3 = json.loads(A3.read_text(encoding='utf-8'))
    a2 = json.loads(A2.read_text(encoding='utf-8'))

    # ---- 1. regenerability -----------------------------------------------------
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = P.main(['pin-geom'])
    check('the pin-geom stage exits 0', rc, 0)
    check('the artifact regenerates byte-identically',
          A4.read_text(encoding='utf-8') == before, True)

    # ---- 2. totals -------------------------------------------------------------
    check('totals', d['totals'], EXPECTED_TOTALS)

    # ---- 3. the warm-up calibration --------------------------------------------
    calib = d['warmup_calibration']
    check('the calibration ran on the warm-up', calib['warmup_top_cell'], 'adder_demo')
    check('it compared every cell type in the netlist', calib['cell_types'], 18)
    check('every cell type matches', calib['all_match'], True)
    mismatches = sorted(t for t, v in calib['detail'].items() if not v['match'])
    check('no cell type mismatched', mismatches, [])
    for typ, want in SPOTLIGHT.items():
        v = calib['detail'][typ]
        check(f'calibration {typ} layout==netlist',
              ' '.join(sorted(v['from_layout'])), want)
        check(f'calibration {typ} netlist side non-empty', bool(v['from_netlist']), True)
    # both sides must be independently populated, not empty-on-empty
    empty_types = sorted(t for t, v in calib['detail'].items()
                         if not v['from_layout'] or not v['from_netlist'])
    check('no cell type matched vacuously', empty_types, [])

    # ---- 4. the anti-merge invariant -------------------------------------------
    # Pins of one master must not collapse onto the same geometry. The only legitimate
    # coincidence is a supply pin and its body tie: their labels are drawn at the same
    # coordinates, so they anchor the same shape.
    groups_by_master: dict[str, list] = {}
    for mn, m in d['masters'].items():
        by_geom = defaultdict(list)
        for pn, p in m['pins'].items():
            by_geom[frozenset(tuple(r) for r in p['routing_rects'])].append(pn)
        g = [tuple(sorted(pns)) for _, pns in by_geom.items() if len(pns) > 1]
        if g:
            groups_by_master[mn] = sorted(g)
    offenders = sorted({p for gs in groups_by_master.values() for g in gs for p in g}
                       - SUPPLIES)
    check('only supply/body-tie pins ever share identical geometry', offenders, [])
    check('68 masters have such a coincident group', len(groups_by_master), 68)
    two_way = sorted(mn for mn, gs in groups_by_master.items() if gs == [('VGND', 'VNB')])
    four_way = sorted(mn for mn, gs in groups_by_master.items()
                      if gs == [('VGND', 'VNB', 'VPB', 'VPWR')])
    check('the co-located VGND+VNB pair occurs in 67 masters', len(two_way), 67)
    check('the antenna diode is the one master where all four coincide',
          four_way, ['sky130_fd_sc_hd__diode_2'])
    check('the tap cell has no coincident group',
          [mn for mn in groups_by_master if mn.endswith('tapvpwrvgnd_1')], [])
    # and the specific pins the bug had merged must now be distinct
    for mn, pins in (('sky130_fd_sc_hd__nand2_2', ('A', 'B', 'Y')),
                     ('sky130_fd_sc_hd__dfrtp_2', ('CLK', 'D', 'Q', 'RESET_B'))):
        m = d['masters'][mn]
        sets = {pn: frozenset(tuple(r) for r in m['pins'][pn]['routing_rects'])
                for pn in pins}
        dupes = [(a, b) for i, a in enumerate(pins) for b in pins[i + 1:]
                 if sets[a] == sets[b]]
        check(f'{mn.split("__")[-1]}: {pins} have pairwise distinct geometry', dupes, [])
        check(f'{mn.split("__")[-1]}: none of those pins is empty',
              [pn for pn in pins if not sets[pn]], [])

    # ---- 5. geometry sanity, recomputed from the artifact -----------------------
    outside, nonint, empty = [], [], []
    for mn, m in d['masters'].items():
        x0, y0, x1, y1 = m['bbox_um']
        for pn, p in m['pins'].items():
            if p['n_shapes'] == 0:
                empty.append(f'{mn}:{pn}')
            for s in p['shapes']:
                r = s['rect']
                if (r[0] < x0 - BBOX_TOL or r[1] < y0 - BBOX_TOL
                        or r[2] > x1 + BBOX_TOL or r[3] > y1 + BBOX_TOL):
                    outside.append(f'{mn}:{pn}')
                if any(abs(v * 1000 - round(v * 1000)) > 1e-6 for v in r):
                    nonint.append(f'{mn}:{pn}')
    check('every rect lies inside its master bbox', sorted(set(outside)), [])
    check('every coordinate is DBU-aligned', sorted(set(nonint)), [])
    check('every pin has geometry', empty, [])
    check('the stage\'s own structural checks all passed',
          [c['check'] for c in d['checks'] if not c['passed']], [])

    # ---- 6. agreement with A3 and A2 -------------------------------------------
    a3_keys = {(mn, pn) for mn, m in a3['masters'].items() for pn in m['pins']}
    a4_keys = {(mn, pn) for mn, m in d['masters'].items() for pn in m['pins']}
    check('A4 models exactly the pins A3 found', a4_keys, a3_keys)
    per_master = {mn: len(m['pins']) for mn, m in d['masters'].items()}
    check('per-master pin counts equal A3\'s',
          per_master, {mn: m['n_pins'] for mn, m in a3['masters'].items()})
    check('pin types propagated from A3',
          {mn: m['type'] for mn, m in d['masters'].items()},
          {mn: m['type'] for mn, m in a3['masters'].items()})
    check('the local rule table is A2\'s proved rule set',
          [(f"{r['cut'][0]}/{r['cut'][1]}", r['bridges_pairs']) for r in d['local_rule_table']],
          [(r['cut'], r['connects']) for r in a2['pairs']])
    check('seed methods are from the known set',
          sorted({s for m in d['masters'].values() for p in m['pins'].values()
                  for s in p['seed_methods']}), ['other_layer', 'same_layer_number'])
    # Pins whose geometry never reaches the routing stack. Two distinct reasons, both
    # recorded rather than waved at: VPB is a well-tie contact that exists only on 64/16,
    # and the antenna-diode cell's supplies exist only as 68/16 pin squares with no met1
    # drawing layer at all.
    unrouted = {(mn, pn) for mn, m in d['masters'].items() for pn, p in m['pins'].items()
                if not p['routing_rects']}
    check('pins with no routing-layer geometry, by name',
          dict(Counter(pn for _, pn in unrouted)),
          {'VPB': 68, 'VGND': 1, 'VNB': 1, 'VPWR': 1})
    check('VPB lacks routing geometry in every master that has a VPB',
          sorted(mn for mn, pn in unrouted if pn == 'VPB'),
          sorted(mn for mn, m in d['masters'].items() if 'VPB' in m['pins']))
    check('the only master whose supplies lack routing geometry is the antenna diode',
          sorted({mn for mn, pn in unrouted if pn in ('VGND', 'VNB', 'VPWR')}),
          ['sky130_fd_sc_hd__diode_2'])

    # ---- report ----------------------------------------------------------------
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
        print(f'STEP A4 GATE: FAIL ({n_fail} failing)')
        return 1
    print('STEP A4 GATE: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
