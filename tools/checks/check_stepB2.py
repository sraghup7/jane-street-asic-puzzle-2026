#!/usr/bin/env python3
"""check_stepB2.py -- executable gate for step B2 (the connectivity spike).

B2's claim is strong: KLayout's engine, configured *only* from our own derived layer stack
and proved rule set, with terminals from our own pin model, reproduces the connectivity of a
design whose netlist we hold. This gate does not take `equivalent: true` on trust. It
re-derives the reference partition from `01_netlist.v` and the DEF, and compares that
against the partition recorded in the artifact.

It also guards against the failure mode this spike actually hit twice: a comparison that
covers almost nothing and therefore "passes". Both the coverage of the reference terminals
and an absolute floor on the compared terminal count are asserted.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.checks._regen import regenerate             # noqa: E402
from tools.puzzle import connect as C                  # noqa: E402

A1 = ROOT / 'recon' / 'derived' / 'layers.json'
A2 = ROOT / 'recon' / 'derived' / 'via_pairs.json'
A4 = ROOT / 'recon' / 'derived' / 'pinmodel.json'
B2 = ROOT / 'recon' / 'derived' / 'warmup_netlist.json'

results: list[tuple[str, str, object, object]] = []


def check(label: str, actual, expected) -> None:
    results.append(('PASS' if actual == expected else 'FAIL', label, actual, expected))


def main() -> int:
    for p in (B2, A1, A2, A4):
        if not p.exists():
            print(f'missing {p.relative_to(ROOT)} -- run `python -m tools.puzzle connect`')
            return 1
    before = B2.read_bytes()
    d = json.loads(before)
    a2 = json.loads(A2.read_text(encoding='utf-8'))
    inv = json.loads((ROOT / 'recon' / 'inventory.json').read_text(encoding='utf-8'))
    per_um = int(inv['gds']['dbu_per_um'])

    # ---- 1. regenerability (hermetic) -------------------------------------------
    rc, produced, _ = regenerate('tools.puzzle.connect', ('OUT',), 'connect')
    check('the connect stage exits 0', rc, 0)
    check('the artifact regenerates byte-identically', produced == before, True)
    check('regeneration did not touch the working tree', B2.read_bytes() == before, True)

    # ---- 2. the engine was configured from our own derived data ------------------
    e = d['engine']
    check('engine is KLayout LayoutToNetlist', e['name'], 'klayout LayoutToNetlist')
    check('conductors came from A1/A2', e['conductors'],
          sorted({p for r in a2['pairs'] for p in r['connects']}))
    check('via rules applied', e['via_rules'], len(a2['pairs']))

    # ---- 3. extraction quality ---------------------------------------------------
    t = d['totals']
    check('warm-up placements', t['placements'], 230)
    check('functional pins probed', t['pins_probed'], 285)
    check('pins with no conductor geometry', t['pins_without_conductor_geometry'], 0)
    check('probe errors', t['probe_errors'], [])
    check('pins the engine left unconnected', t['pins_unconnected_by_engine'], 0)

    # ---- 4. the comparison, re-derived here --------------------------------------
    insts, _order = C.parse_netlist(C.WU_NET.read_text(encoding='utf-8'))
    def_place = C.parse_def_placements(C.WU_DEF.read_text(encoding='utf-8'))
    placements, _m_bbox = C.warmup_instances(per_um)
    by_ll = {(pl['master'], pl['lower_left_dbu']): pl['id'] for pl in placements}
    id_map, unmapped = {}, []
    for name in insts:
        leaf = name.lstrip('\\')
        if leaf not in def_place:
            unmapped.append(name)
            continue
        cell, dx, dy = def_place[leaf]
        if (cell, (dx, dy)) not in by_ll:
            unmapped.append(name)
            continue
        id_map[name] = by_ll[(cell, (dx, dy))]
    check('every netlist instance maps to a GDS placement', (len(id_map), unmapped),
          (230, []))

    theirs = defaultdict(set)
    for name, info in insts.items():
        if name not in id_map:
            continue
        for pin, net in info['pins'].items():
            if pin in C.SUPPLY:
                continue
            theirs[net].add((id_map[name], pin))
    ref_terminals = {t2 for v in theirs.values() for t2 in v}

    ours = defaultdict(set)
    for net, terminals in d['our_partition'].items():
        for inst, pin in terminals:
            ours[net].add((inst, pin))
    ours_r = {k: {t2 for t2 in v if t2 in ref_terminals} for k, v in ours.items()}
    ours_r = {k: v for k, v in ours_r.items() if v}

    canon = lambda part: Counter(frozenset(v) for v in part.values())  # noqa: E731
    a_can, b_can = canon(ours_r), canon(theirs)
    compared = {t2 for v in ours_r.values() for t2 in v}
    check('reference nets', len(theirs), 84)
    check('our nets', len(ours_r), 84)
    check('reference terminals', len(ref_terminals), 285)
    check('net signatures differing', (sum((a_can - b_can).values()),
                                       sum((b_can - a_can).values())), (0, 0))
    # anti-vacuity: the comparison must actually cover the reference, not a corner of it
    check('every reference terminal was compared', len(compared), len(ref_terminals))
    check('the comparison is not vacuous (absolute floor)', len(compared) >= 250, True)
    check('the artifact agrees that the partitions are equivalent',
          d['comparison']['equivalent'], True)
    check('the artifact records full coverage', d['comparison']['signatures_only_in_ours'], 0)
    check('the artifact lists no reference-only nets',
          d['comparison']['signatures_only_in_reference'], 0)

    # ---- 5. the terminals are ours, not the engine's guesses ---------------------
    a4 = json.loads(A4.read_text(encoding='utf-8'))
    pins_expected = {(pl['id'], pin) for pl in placements for pin in
                     a4['masters'][pl['master']]['pins'] if pin not in C.SUPPLY}
    probed = {t2 for v in ours.values() for t2 in v}
    check('the pin model supplies exactly the probed terminals', len(pins_expected), 285)
    check('every probed terminal is a pin of a real placement',
          sorted(probed - pins_expected), [])
    check('every pin the model supplies was probed', sorted(pins_expected - probed), [])

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
        print(f'STEP B2 GATE: FAIL ({n_fail} failing)')
        return 1
    print('STEP B2 GATE: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
