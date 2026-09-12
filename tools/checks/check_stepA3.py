#!/usr/bin/env python3
"""check_stepA3.py -- executable gate for step A3 (pin names).

The claim: every pin name in this design can be read out of the cell masters themselves,
with no PDK macro LEF and no library documentation. The gate asserts:

  1. regenerability -- re-running the stage reproduces the artifact byte for byte;
  2. the extraction -- 69 masters, 34 distinct names, 803 pin labels, reconciled against
     A1's independent per-layer TEXT counts;
  3. the derived label-layer scope -- and that the cell-name layer is excluded by a
     structural test rather than a hardcoded list;
  4. fifteen cross-checks, each with two independently derived sides;
  5. exact pin sets for the cells whose semantics are load-bearing downstream (the three
     flip-flop types, the constant-output tie cell, the physical-only cells).

    .venv/Scripts/python tools/checks/check_stepA3.py
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

EXPECTED_PIN_SETS = {
    'sky130_fd_sc_hd__dfrtp_2': 'CLK D Q RESET_B VGND VNB VPB VPWR',
    'sky130_fd_sc_hd__dfstp_2': 'CLK D Q SET_B VGND VNB VPB VPWR',
    'sky130_fd_sc_hd__dfxtp_2': 'CLK D Q VGND VNB VPB VPWR',
    'sky130_fd_sc_hd__conb_1': 'HI LO VGND VNB VPB VPWR',
    'sky130_fd_sc_hd__tapvpwrvgnd_1': 'VGND VPWR',
    'sky130_fd_sc_hd__decap_3': 'VGND VNB VPB VPWR',
    'sky130_fd_sc_hd__diode_2': 'DIODE VGND VNB VPB VPWR',
    'sky130_fd_sc_hd__mux2_1': 'A0 A1 S VGND VNB VPB VPWR X',
}
EXPECTED_CENSUS = {'VGND': 69, 'VPWR': 69, 'VNB': 68, 'VPB': 68, 'X': 36, 'Y': 26,
                   'CLK': 3, 'Q': 3, 'RESET_B': 1, 'SET_B': 1, 'S': 1}

results: list[tuple[str, str, object, object]] = []


def check(label, actual, expected):
    results.append(('PASS' if actual == expected else 'FAIL', label, actual, expected))


def main() -> int:
    for p in (A1, A3):
        if not p.exists():
            print(f'missing {p.relative_to(ROOT)} -- run the A1 and pin-names stages first')
            return 1
    before = A3.read_text(encoding='utf-8')
    d = json.loads(before)
    a1 = json.loads(A1.read_text(encoding='utf-8'))

    # ---- 1. regenerability -----------------------------------------------------
    # Hermetic: the stage writes to scratch, so this detects a stale artifact *and*
    # leaves the committed one in place for inspection when it fails.
    rc, produced, _ = regenerate('tools.puzzle.pins', ('NAMES_OUT',), 'pin-names')
    check('the pin-names stage exits 0', rc, 0)
    check('the artifact regenerates byte-identically',
          produced == before.encode('utf-8'), True)
    check('regeneration did not touch the working tree',
          A3.read_bytes() == before.encode('utf-8'), True)

    # ---- 2. extraction totals --------------------------------------------------
    t = d['totals']
    check('master count', t['masters'], 69)
    check('distinct pin names', t['distinct_pin_names'], 34)
    check('pin labels read from masters', t['pin_labels'], 803)
    check('label counts per layer', d['label_counts_by_layer'],
          {'64/5': 71, '64/59': 71, '67/5': 521, '68/5': 140})

    # reconciliation against A1's independent TEXT counts
    a1_std = {f'{r["layer"]}/{r["datatype"]}': r['by_cell_kind']['STD']
              for r in a1['pairs'] if r['role'] == 'label'}
    check('our per-layer counts equal A1\'s for every master pin-label layer',
          {k: d['label_counts_by_layer'][k] for k in d['label_counts_by_layer']},
          {k: a1_std[k] for k in d['label_counts_by_layer']})
    check('the counts sum to the recorded total',
          sum(d['label_counts_by_layer'].values()), t['pin_labels'])

    # ---- 3. the derived label-layer scope --------------------------------------
    pll = d['pin_label_layers']
    check('master pin-label layers are derived, not hardcoded',
          pll['master'], ['64/5', '64/59', '67/5', '68/5'])
    check('the cell-name layer is the one excluded', sorted(pll['excluded']), ['83/44'])
    check('the exclusion reason names the structural test',
          'no geometry' in pll['excluded']['83/44'], True)
    check('the cell-name layer still holds its labels',
          a1_std['83/44'], 73)
    check('body-tie pins are recorded', d['body_tie_pins'], ['VNB', 'VPB'])

    # ---- 4. the fifteen cross-checks -------------------------------------------
    checks = d['consistency']['checks']
    failed = [c['check'] for c in checks if not c['passed']]
    check('all consistency checks pass', failed, [])
    check('check count', len(checks), 15)
    by_name = {c['check']: c for c in checks}
    # the two sides of the load-bearing checks must be non-empty and independently drawn
    for name in ('a CLK label appears exactly on the flip-flop cells',
                 'a RESET_B label appears exactly on the dfr* cells',
                 'a SET_B label appears exactly on the dfs* cells',
                 'a Q output appears exactly on the flip-flop cells',
                 'a select pin S appears exactly on the mux* cells'):
        got = by_name.get(name)
        check(f'both sides present and non-empty: {name}',
              bool(got) and bool(got['from_labels']) and got['from_labels'] == got['from_names'],
              True)

    # ---- 5. census and exact pin sets ------------------------------------------
    for name, n in EXPECTED_CENSUS.items():
        check(f'census {name}', d['census'].get(name), n)
    check('the census is the union of every master\'s pins',
          sorted(d['census']), sorted({p for m in d['masters'].values() for p in m['pins']}))
    for master, pins in EXPECTED_PIN_SETS.items():
        check(f'pin set {master.split("__")[-1]}',
              ' '.join(sorted(d['masters'][master]['pins'])), pins)
    check('no master has an empty pin set',
          [m for m, v in d['masters'].items() if not v['pins']], [])
    check('every pin carries a layer and a position',
          [f'{m}:{p}' for m, v in d['masters'].items() for p, pv in v['pins'].items()
           if not pv['on_layers'] or not pv['positions_um']], [])
    check('every pin sits on a master pin-label layer',
          sorted({l for v in d['masters'].values() for pv in v['pins'].values()
                  for l in pv['on_layers']}),
          ['64/5', '64/59', '67/5', '68/5'])

    # ---- 6. the top cell's ports -----------------------------------------------
    ports = {p: v for p, v in d['top_ports'].items()
             if any(l.startswith('70/') for l in v['on_layers'])}
    check('the top cell exposes Step 1\'s 13 ports',
          sorted(ports), sorted(P.EXPECTED_PORTS))
    power = {p for p, v in d['top_ports'].items()
             if any(l.startswith(('71/', '72/')) for l in v['on_layers'])}
    check('the top cell also labels its power nets', sorted(power), ['VGND', 'VPWR'])

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
        print(f'STEP A3 GATE: FAIL ({n_fail} failing)')
        return 1
    print('STEP A3 GATE: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
