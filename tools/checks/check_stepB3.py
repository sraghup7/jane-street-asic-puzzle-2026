#!/usr/bin/env python3
"""check_stepB3.py -- executable gate for step B3 (full connectivity on the puzzle).

B3's two claims are that the extraction is *complete* (every conductor shape on the die
belongs to exactly one net) and that the supply nets are identified by **pin name** rather
than by size. Both are re-derived here from the artifact and the raw inputs rather than
taken from a verdict string.

Two anti-vacuity assertions are included on purpose: the shape count actually probed must
be the full 40360, and the supply-pin probe count must be the full 5120. A completeness
check over a sample is not a completeness check, and this project has already passed a
comparison over 4 nets once.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.checks._regen import regenerate             # noqa: E402
from tools.puzzle import connect as C                  # noqa: E402

B3 = ROOT / 'recon' / 'derived' / 'nets.json'
A2 = ROOT / 'recon' / 'derived' / 'via_pairs.json'
A4 = ROOT / 'recon' / 'derived' / 'pinmodel.json'
B1 = ROOT / 'recon' / 'derived' / 'instances.json'

results: list[tuple[str, str, object, object]] = []


def check(label: str, actual, expected) -> None:
    results.append(('PASS' if actual == expected else 'FAIL', label, actual, expected))


def main() -> int:
    for p in (B3, A2, A4, B1):
        if not p.exists():
            print(f'missing {p.relative_to(ROOT)} -- run `python -m tools.puzzle nets`')
            return 1
    before = B3.read_bytes()
    d = json.loads(before)
    a2 = json.loads(A2.read_text(encoding='utf-8'))
    pins = json.loads(A4.read_text(encoding='utf-8'))
    insts = json.loads(B1.read_text(encoding='utf-8'))

    # ---- 1. regenerability (hermetic) -------------------------------------------
    import time as _time
    t0 = _time.time()
    rc, produced, _ = regenerate('tools.puzzle.connect', ('OUT_NETS',), 'nets')
    stage_seconds = _time.time() - t0
    check('the nets stage exits 0', rc, 0)
    check('the artifact regenerates byte-identically', produced == before, True)
    check('regeneration did not touch the working tree', B3.read_bytes() == before, True)
    # Runtime is measured here, not stored: a committed artifact that embeds a wall-clock
    # duration cannot regenerate byte-identically, which is a property the project relies on.
    check('the stage completes in reasonable time', stage_seconds < 120, True)
    check('the artifact stores no timing (it must be deterministic)',
          'runtime_s' in d or 'probe_seconds' in d.get('totals', {}), False)

    # ---- 2. the engine was configured from our data ------------------------------
    check('engine', d['engine']['name'], 'klayout LayoutToNetlist')
    check('conductors came from A1/A2', d['engine']['conductors'],
          sorted({p for r in a2['pairs'] for p in r['connects']}))
    check('via rules applied', d['engine']['via_rules'], len(a2['pairs']))

    t = d['totals']
    check('nets in the extracted circuit', t['nets'], 2626)
    # The property that forced the flattening, and the reason this gate exists in this shape:
    # net identity must be unique. Extracted hierarchically it was NOT -- 70 different nets in
    # 70 different circuits all carried cluster_id 2, and `probe_net` returns the owning
    # cell's local net, so any map keyed on cluster_id silently merges unrelated nets.
    check('every net has a distinct cluster id', t['distinct_cluster_ids'], t['nets'])
    check('the artifact asserts unique net identity', t['net_identity_unique'], True)
    check('the flattened extraction has no subcircuit count left',
          'subcircuits' in t, False)

    # ---- 3. completeness: no orphan conductor shapes -----------------------------
    per_layer = d['per_layer_shapes']
    check('conductor shapes on the die', t['conductor_shapes'], 40360)
    check('shapes probed per layer', {k: v['shapes'] for k, v in per_layer.items()},
          {'67/20': 13931, '68/20': 14366, '69/20': 8517,
           '70/20': 2547, '71/20': 855, '72/20': 144})
    check('every shape was assigned to a net', t['shapes_assigned_to_a_net'],
          t['conductor_shapes'])
    check('orphan shapes', t['orphan_shapes'], 0)
    check('orphan examples recorded', d['orphan_examples'], [])
    check('shapes with no interior point (would make the check vacuous)',
          t['shapes_without_interior_point'], 0)
    check('unprobeable examples recorded', d['unprobeable_examples'], [])
    check('every shape in exactly one net', t['every_shape_in_exactly_one_net'], True)
    # anti-vacuity: a floor, so a shrinking probe cannot pass
    check('the probe covered the whole die', t['shapes_assigned_to_a_net'] >= 40000, True)

    # ---- 4. supply nets identified by pin name, not by size ----------------------
    check('supply pins probed', t['supply_pins_probed'], 5120)
    check('supply pins probed is the full set (anti-vacuity)',
          t['supply_pins_probed'] >= 5000, True)
    expected_pins = sum(1 for pl in insts['instances']
                        for pin in pins['masters'][pl['master']]['pins']
                        if pin in C.SUPPLY)
    check('every supply pin of every placement was probed',
          t['supply_pins_probed'], expected_pins)
    check('unassigned supply pins', t['supply_pins_unassigned'],
          t['supply_unassigned_in_known_gap_classes'] + t['supply_unassigned_unexplained'])
    check('no unassigned supply pin outside A5\'s two gap classes',
          t['supply_unassigned_unexplained'], 0)
    check('unassigned pins are the VPB ties plus the diode supplies',
          (t['supply_unassigned_by_pin'].get('VPB'),
           t['supply_pins_unassigned'] - t['supply_unassigned_by_pin'].get('VPB', 0)),
          (942, 30))

    si = d['supply_identification']
    check('the two largest nets are supply nets', si['largest_two_are_supply'], True)
    check('supply names they carry', si['supply_pin_names'], ['VGND', 'VNB', 'VPWR'])
    check('every name on those nets is a supply pin',
          set(si['supply_pin_names']) <= C.SUPPLY, True)
    largest = d['largest_nets']
    check('the largest net carries no functional pin name',
          set(largest[0]['supply_pins']) <= C.SUPPLY, True)
    check('the second largest net carries no functional pin name',
          set(largest[1]['supply_pins']) <= C.SUPPLY, True)
    check('the biggest two nets are the biggest two by polygon count',
          sorted((n['cluster'] for n in largest[:2]),
                 key=lambda c: -[x['polygons'] for x in largest if x['cluster'] == c][0])
          == [n['cluster'] for n in sorted(largest, key=lambda n: -n['polygons'])[:2]], True)
    # Pinned, and this is the strongest form of the claim: the two biggest nets carry exactly
    # the supply pin names, with the counts the pin model predicts (1608 placements carry
    # VPWR; 1608 carry VGND; 932 carry VNB). Identification by name and by size therefore
    # agree, and neither is doing the work alone.
    check('the largest net carries VPWR and nothing else',
          largest[0]['supply_pins'], {'VPWR': 1608})
    check('the second largest carries VGND and VNB and nothing else',
          largest[1]['supply_pins'], {'VGND': 1608, 'VNB': 932})
    # Supply is separated from signal by an order of magnitude in polygon count, so "biggest"
    # is not a knife-edge call that a small change could flip.
    check('the supply nets are >5x the next biggest net',
          min(largest[0]['polygons'], largest[1]['polygons']) > 5 * largest[2]['polygons'],
          True)

    # ---- report -----------------------------------------------------------------
    w = max(len(r[1]) for r in results)
    n_fail = 0
    for status, label, actual, expected in results:
        if status == 'FAIL':
            n_fail += 1
            print(f'  FAIL  {label}')
            print(f'          got  {actual!r}')
            print(f'          want {expected!r}')
        else:
            print(f'  PASS  {label}')
    print()
    print(f'{len(results) - n_fail} passed, {n_fail} failed, 0 skipped')
    if n_fail:
        print(f'STEP B3 GATE: FAIL ({n_fail} failing)')
        return 1
    print('STEP B3 GATE: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
