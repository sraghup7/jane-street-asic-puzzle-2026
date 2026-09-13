#!/usr/bin/env python3
"""check_stepB4.py -- executable gate for step B4 (every pin of every instance onto a net).

B4's claim is that the extraction has been turned into a netlist: for all 1618 placements,
every modelled pin is assigned to exactly one net, with 0 unassigned *functional* pins and
only A5's two predicted causes for the supply pins. This gate re-derives all of that from the
raw inputs rather than trusting the artifact's own totals, and it adds three things the totals
cannot provide:

* **Structural coverage, both directions.** Every B1 instance must appear, with exactly the
  pin set A4 models for its master -- no missing pin, no invented pin.
* **Two-representation agreement.** The artifact stores the map twice (per instance, and
  aggregated per net). Both are recomputed against each other, so a divergence cannot hide.
* **An independent re-probe.** For a deterministic sample of terminals, the engine is rebuilt
  and the recorded evidence (layer, point) is probed again; the net must come back the same.
  Without this the artifact's own probe is the only witness to its own evidence.

Anti-vacuity is deliberate here too: floors on the terminal counts, plus an explicit
assertion that net identity is unique. Flattening the layout before extraction is what makes
`cluster_id` a valid key -- hierarchically, 70 nets in 70 circuits shared one id -- so if that
property ever regresses, every mapping in this artifact becomes meaningless and this gate must
fail rather than quietly pass over merged nets.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from klayout import db

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.checks._regen import regenerate             # noqa: E402
from tools.puzzle import connect as C                  # noqa: E402

B4 = ROOT / 'recon' / 'derived' / 'pin_net.json'
B3 = ROOT / 'recon' / 'derived' / 'nets.json'
A2 = ROOT / 'recon' / 'derived' / 'via_pairs.json'
A4 = ROOT / 'recon' / 'derived' / 'pinmodel.json'
B1 = ROOT / 'recon' / 'derived' / 'instances.json'
GDS = ROOT / 'asic-puzzle-2026' / 'puzzle.gds'

STD = 'sky130_fd_sc_hd__'
results: list[tuple[str, str, object, object]] = []


def check(label: str, actual, expected) -> None:
    results.append(('PASS' if actual == expected else 'FAIL', label, actual, expected))


def main() -> int:
    for p in (B4, B3, A2, A4, B1):
        if not p.exists():
            print(f'missing {p.relative_to(ROOT)} -- run `python -m tools.puzzle pins-to-nets`')
            return 1
    before = B4.read_bytes()
    d = json.loads(before)
    b3 = json.loads(B3.read_text(encoding='utf-8'))
    a2 = json.loads(A2.read_text(encoding='utf-8'))
    a4 = json.loads(A4.read_text(encoding='utf-8'))
    b1 = json.loads(B1.read_text(encoding='utf-8'))

    # ---- 1. hermetic regenerability ---------------------------------------------
    t0 = time.time()
    rc, produced, _ = regenerate('tools.puzzle.netlist', ('OUT',), 'pins-to-nets')
    stage_seconds = time.time() - t0
    check('the pins-to-nets stage exits 0', rc, 0)
    check('the artifact regenerates byte-identically', produced == before, True)
    check('regeneration did not touch the working tree', B4.read_bytes() == before, True)
    check('the stage completes in reasonable time', stage_seconds < 300, True)
    check('the artifact stores no timing (it must be deterministic)',
          any(k in d for k in ('runtime_s', 'seconds')) or 'seconds' in d['totals'], False)

    # ---- 2. provenance and engine configuration ---------------------------------
    check('generated_by', d['generated_by'], 'tools/puzzle/netlist.py::stage_pin_net')
    check('source is the puzzle GDS', d['source']['sha256'], C.sha256(GDS))
    check('engine', d['engine']['name'], 'klayout LayoutToNetlist')
    check('conductors came from A1/A2', d['engine']['conductors'],
          sorted({p for r in a2['pairs'] for p in r['connects']}))
    check('via rules applied', d['engine']['via_rules'], len(a2['pairs']))

    t = d['totals']
    # ---- 3. net identity must be unique, or every key in this artifact is worthless ----
    check('nets in the flattened extraction', t['engine_nets'], 2626)
    check('every net has a distinct cluster id',
          t['engine_distinct_cluster_ids'], t['engine_nets'])
    check('the artifact asserts unique net identity', t['net_identity_unique'], True)

    # ---- 4. structural coverage, re-derived from B1 + A4 --------------------------
    placements = b1['instances']
    expected_pins = {pl['id']: set(a4['masters'][pl['master']]['pins']) for pl in placements}
    check('instances in the map', len(d['instances']), len(placements))
    check('the map covers exactly the B1 instances',
          sorted(d['instances']), sorted(expected_pins))
    missing_pins = [f'{i}.{p}' for i, pins in expected_pins.items()
                    for p in pins - set(d['instances'][i]['pins'])]
    extra_pins = [f'{i}.{p}' for i, pins in expected_pins.items()
                  for p in set(d['instances'][i]['pins']) - pins]
    check('no modelled pin is missing from the map', missing_pins[:6], [])
    check('no pin is invented that A4 does not model', extra_pins[:6], [])
    check('masters recorded per instance',
          {i: v['master'] for i, v in d['instances'].items()},
          {pl['id']: pl['master'] for pl in placements})

    endpoints = sum(len(p) for p in expected_pins.values())
    check('endpoints = every pin of every placement', t['endpoints'], endpoints)
    func_pins = {i: {p for p in pins if p not in C.SUPPLY} for i, pins in expected_pins.items()}
    sup_pins = {i: {p for p in pins if p in C.SUPPLY} for i, pins in expected_pins.items()}
    n_func = sum(len(v) for v in func_pins.values())
    n_sup = sum(len(v) for v in sup_pins.values())
    check('functional terminals re-derived', t['terminals_functional'], n_func)
    check('supply terminals re-derived', t['terminals_supply'], n_sup)
    check('functional + supply = endpoints', n_func + n_sup, endpoints)
    # anti-vacuity floors: a map over a shrunken endpoint set cannot pass
    check('the map covers the whole design (floor 6000)', t['endpoints'] >= 6000, True)
    check('functional terminals present (floor 2500)', t['terminals_functional'] >= 2500, True)

    # ---- 5. the B4 acceptance criterion: 0 unassigned functional pins --------------
    check('every functional pin is assigned a net',
          t['unassigned_functional'], 0)
    check('assigned functional pins = all functional pins',
          t['assigned_functional'], t['terminals_functional'])
    check('unassigned total = unassigned functional + unassigned supply',
          t['unassigned'], t['unassigned_functional'] + t['unassigned_supply'])
    check('no pin landed on two nets', t['pins_labelled_with_two_nets'], 0)
    check('no probe raised an error', t['probe_errors'], 0)
    check('conflicts list is empty', d['conflicts'], [])
    check('probe error list is empty', d['probe_errors'], [])

    # ---- 6. the unassigned pins are exactly A5's two classes ----------------------
    vpb = sum(1 for i, pins in sup_pins.items() for p in pins if p == 'VPB')
    diode = sum(1 for i, pl in enumerate(placements)
                for p in sup_pins[pl['id']]
                if p != 'VPB' and 'diode_2' in pl['master'].replace(STD, ''))
    check('the VPB well ties are the whole of gap class 1', vpb, 942)
    check('the antenna diode supplies are the whole of gap class 2', diode, 30)
    check('unassigned supply pins re-derived', t['unassigned_supply'], vpb + diode)
    check('gap class 1 in the artifact', d['gap_classes']['vpb_well_tie_no_routeable_geometry'],
          vpb)
    check('gap class 2 in the artifact', d['gap_classes']['antenna_diode_supply'], diode)
    check('nothing unassigned outside those two classes',
          t['unassigned_supply_unexplained'], 0)
    check('every unassigned entry names one of the two classes',
          sorted({u['gap_class'] for u in d['unassigned']}),
          ['antenna_diode_supply', 'vpb_well_tie_no_routeable_geometry'])

    # ---- 7. the two representations must agree -----------------------------------
    from_map: dict[int, Counter] = defaultdict(Counter)
    names_by_net: dict[int, Counter] = defaultdict(Counter)
    by_pin = Counter()
    nulls = set()
    for iid, rec in d['instances'].items():
        for pin, val in rec['pins'].items():
            if val is None:
                nulls.add((iid, pin))
                by_pin[(pin, 'instances')] += 1
                continue
            cid = val[0]
            from_map[cid][pin] += 1
            names_by_net[cid][pin] += 1
            by_pin[(pin, 'instances')] += 1
            by_pin[(pin, 'assigned')] += 1
    rows = {r['cluster']: r for r in d['nets']}
    check('the net table has no duplicates', len(rows), len(d['nets']))
    check('the net table lists exactly the nets the map uses',
          sorted(rows), sorted(from_map))
    check('every net row carries at least one terminal',
          all(r['terminals'] >= 1 for r in d['nets']), True)
    check('terminal counts agree between the two representations',
          {c: sum(v.values()) for c, v in from_map.items()},
          {c: rows[c]['terminals'] for c in from_map})
    check('pin-name breakdowns agree between the two representations',
          {c: dict(v) for c, v in names_by_net.items()},
          {c: rows[c]['pin_names'] for c in from_map})
    check('functional/supply split agrees per net',
          {c: (sum(v[p] for p in v if p not in C.SUPPLY),
               sum(v[p] for p in v if p in C.SUPPLY)) for c, v in from_map.items()},
          {c: (rows[c]['functional'], rows[c]['supply']) for c in from_map})
    check('is_supply_only recomputed',
          sorted(c for c, v in from_map.items() if set(v) <= C.SUPPLY),
          sorted(c for c, r in rows.items() if r['is_supply_only']))
    check('the unassigned list is exactly the null entries',
          sorted((u['instance'], u['pin']) for u in d['unassigned']), sorted(nulls))
    check('the unassigned entries are all supply pins',
          sorted({u['pin'] for u in d['unassigned']} - {'VPWR', 'VGND', 'VNB', 'VPB'}), [])
    check('nets are ordered by descending terminal count',
          [r['terminals'] for r in d['nets']],
          sorted((r['terminals'] for r in d['nets']), reverse=True))

    # ---- 8. agreement with B3's supply identification ----------------------------
    check('the two largest nets carry only supply pins',
          all(r['is_supply_only'] for r in d['nets'][:2]), True)
    check('the largest net is B3\'s VGND+VNB net',
          d['nets'][0]['cluster'], 3)
    check('the second net is B3\'s VPWR net', d['nets'][1]['cluster'], 27)
    check('the largest net carries VGND and VNB only',
          d['nets'][0]['pin_names'], {'VGND': 1608, 'VNB': 932})
    check('the VPWR net carries VPWR only', d['nets'][1]['pin_names'], {'VPWR': 1608})
    check('the biggest two nets in B3 are the same two clusters',
          sorted(n['cluster'] for n in b3['largest_nets'][:2]),
          sorted(r['cluster'] for r in d['nets'][:2]))
    check('every net carrying only supply pins is one of those two',
          sorted(r['cluster'] for r in d['nets'] if r['is_supply_only']),
          [3, 27])
    check('nets with at least one terminal (floor 700)',
          t['nets_with_terminals'] >= 700, True)

    # ---- 9. independent re-probe of the recorded evidence ------------------------
    conductors = sorted({p for r in a2['pairs'] for p in r['connects']})
    cuts = sorted({r['cut'] for r in a2['pairs']})
    _, _, l2n, _, reg = C.build_engine(GDS, conductors, cuts, a2['pairs'])
    # deterministic sample: every 40th instance, first assigned pin
    sample = []
    for k, (iid, rec) in enumerate(sorted(d['instances'].items())):
        if k % 40:
            continue
        for pin, val in sorted(rec['pins'].items()):
            if val is not None:
                sample.append((iid, pin, val))
                break
    mismatches = []
    for iid, pin, (cid, pair, x, y) in sample:
        n = l2n.probe_net(reg[pair], db.Point(x, y))
        if n is None or n.cluster_id != cid:
            mismatches.append(f'{iid}.{pin}: recorded {cid}, re-probe '
                              f'{n.cluster_id if n else None}')
    check('the re-probe sample is non-trivial (>= 30 terminals)', len(sample) >= 30, True)
    check('every re-probed evidence point returns its recorded net', mismatches[:5], [])

    # ---- 10. the position-layer fallback, re-derived and bounded -------------------
    # `position_layer_fallbacks` was asserted by nothing -- not by this gate, not by the stage's
    # own PASS condition -- while the artifact's `method` block stated the containment guarantee
    # unconditionally. Both are fixed: the text now describes the fallback, and the counter is
    # re-derived here from A4 + B1 + A2's conductor set. The claim that matters is not the number
    # but the *bound*: the fallback may never produce the net for an assigned pin. Measured:
    # every one of the 981 events sits on a pin with no net, and 951 of them are `VPB`, whose
    # only shape is on a pair outside the conductor set -- so its pair list is empty and the
    # "fallback" probes nothing at all.
    cset = set(conductors)
    fallback_events, fallback_sites = 0, set()
    for pl in placements:
        for pin, info in a4['masters'][pl['master']]['pins'].items():
            rects = [(s['pair'], s['rect']) for s in info['shapes'] if s['pair'] in cset]
            for px, py in ((float(p[0]), float(p[1]))
                           for p in (info.get('positions_um') or [])):
                if not any(r[0] <= px <= r[2] and r[1] <= py <= r[3] for _, r in rects):
                    fallback_events += 1
                    fallback_sites.add((pl['id'], pin))
    check('the fallback counter re-derives from A4 + B1 + A2',
          fallback_events, t['position_layer_fallbacks'])
    check('the fallback sites are a non-trivial set (floor 100)',
          len(fallback_sites) >= 100, True)
    unassigned_pins = {(u['instance'], u['pin']) for u in d['unassigned']}
    check('every fallback site is a pin B4 records as unassigned -- never an assigned one',
          sorted(fallback_sites - unassigned_pins), [])

    # ---- report ------------------------------------------------------------------
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
        print(f'STEP B4 GATE: FAIL ({n_fail} failing)')
        return 1
    print('STEP B4 GATE: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
