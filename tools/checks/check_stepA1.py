#!/usr/bin/env python3
"""check_stepA1.py -- executable gate for step A1 (layer roles).

Three jobs:

  1. COMPLETENESS -- the role table partitions every (layer, datatype) pair in the file,
     with each pair's role assigned by a named rule and its evidence recorded.
  2. THE DECISIVE CROSS-CHECK -- the via masters' own layer sets must form a single
     chain over the layers this step called conductors. This is differentiator D1 stated
     as an executable claim: if the layer stack were inferred wrongly, the chain would
     not close. Nothing downstream is trustworthy until this passes.
  3. RE-DERIVATION -- the facts Step 1 measured independently (cell counts, die box,
     port labels, the 68-of-69 marker layer, the outside-die decoration) must come back
     out of a *different* code path.

    .venv/Scripts/python tools/checks/check_stepA1.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.checks._regen import regenerate             # noqa: E402

A1 = ROOT / 'recon' / 'derived' / 'layers.json'
INV = ROOT / 'recon' / 'inventory.json'
GDS = ROOT / 'asic-puzzle-2026' / 'puzzle.gds'

ROLE_SET = {'label', 'pin', 'via_cut', 'routing', 'local_wire', 'contact',
            'cell_geometry', 'non_electrical'}
CONNECTIVITY_ROLES = {'via_cut', 'routing', 'local_wire', 'pin', 'label'}

EXPECTED = {
    'routing': {(68, 20), (69, 20), (70, 20), (71, 20), (72, 20)},
    'local_wire': {(67, 20)},
    'via_cut': {(67, 44), (68, 44), (69, 44), (70, 44), (71, 44)},
    'pin': {(64, 16), (67, 16), (68, 16), (70, 16), (71, 16), (72, 16)},
    'label': {(64, 5), (64, 59), (67, 5), (68, 5), (70, 5), (71, 5), (72, 5), (83, 44)},
}
# the conductor chain the via masters must reproduce, lowest to highest
CHAIN = [67, 68, 69, 70, 71, 72]
VIA_PLACEMENT_TOTAL = 8221      # Step 1's independently measured routed-via count
PORT_NAMES = {'clk', 'rst_n', 'enable', 'I', 'success'} | {f'O[{i}]' for i in range(8)}

results: list[tuple[str, str, object, object]] = []


def check(label, actual, expected):
    results.append(('PASS' if actual == expected else 'FAIL', label, actual, expected))


def pairs_of(d):
    return {(r['layer'], r['datatype']): r for r in d['pairs']}


def main() -> int:
    if not A1.exists():
        print(f'missing {A1.relative_to(ROOT)} -- run `python -m tools.puzzle layers` first')
        return 1
    d = json.loads(A1.read_text(encoding='utf-8'))
    inv = json.loads(INV.read_text(encoding='utf-8'))
    g = inv['gds']
    P = pairs_of(d)

    # ---- 0. regenerability -----------------------------------------------------
    # Hermetic: the stage writes to scratch, so a stale layers.json is detected *without*
    # the gate overwriting the committed artifact it is judging.
    rc, produced, _ = regenerate('tools.puzzle.layers', ('OUT', 'A1_OUT'), 'layers')
    check('the layers stage exits 0', rc, 0)
    check('the artifact regenerates byte-identically',
          produced == A1.read_bytes(), True)

    # ---- 1. completeness -------------------------------------------------------
    check('pair count', len(P), 41)
    check('role names are all known', sorted({r['role'] for r in d['pairs']} - ROLE_SET), [])
    check('role table partitions the pairs',
          sum(len(v) for v in d['roles'].values()), len(P))
    dupes = [k for k in (tuple(x) for v in d['roles'].values() for x in v)
             if sum(1 for v in d['roles'].values() if list(k) in v) > 1]
    check('no pair appears in two roles', dupes, [])
    # `roles` (the role -> pair-list table a consumer reads) is built from the same
    # classification as `pairs[].role`, but nothing asserted the two agree -- so a
    # hand-edited or drifted role table passed silently. Assert the bijection both ways.
    role_drift = []
    for (l, dt), r in sorted(P.items()):
        in_table = [role for role, lst in d['roles'].items() if [l, dt] in lst]
        if in_table != [r['role']]:
            role_drift.append(f'{l}/{dt}: pairs[].role={r["role"]!r} roles[]={in_table}')
    check('the role table agrees with each pair\'s own role field', role_drift, [])
    check('the role table uses exactly the roles the pairs use',
          sorted(d['roles']), sorted({r['role'] for r in d['pairs']}))
    check('every pair names the rule that assigned it',
          [f'{l}/{dt}' for (l, dt), r in P.items() if not r.get('rule')], [])
    check('every non-high-confidence role carries a note',
          [f'{l}/{dt}' for (l, dt), r in P.items()
           if r['confidence'] != 'high' and not r['notes']], [])

    # ---- 2. the decisive cross-check: the via masters' own layer sets ----------
    # Each via master must decompose into exactly two conductors + one cut, and the
    # resulting edges must form a single simple path over the conductor chain.
    edges, shape_errors = [], []
    for name, layers in d['via_layer_sets'].items():
        parts = [(int(s.split('/')[0]), int(s.split('/')[1])) for s in layers]
        conds = [k for k in parts if P.get(k, {}).get('role') in
                 ('routing', 'local_wire')]
        cuts = [k for k in parts if P.get(k, {}).get('role') == 'via_cut']
        if len(conds) != 2 or len(cuts) != 1:
            shape_errors.append(f'{name}: {len(conds)} conductors, {len(cuts)} cuts')
            continue
        edges.append((tuple(conds), cuts[0]))
    check('every via master is exactly 2 conductors + 1 cut', shape_errors, [])
    check('via master count', len(edges), 9)

    # the cut layer of a via master must itself be classified as a cut
    check('every via master cut is classified via_cut',
          sorted({c for _, c in edges}), sorted(EXPECTED['via_cut']))
    used_conds = {l for cs, _ in edges for l, _ in cs}
    check('the via masters touch exactly the conductor layers',
          sorted(used_conds), sorted({l for l, _ in EXPECTED['routing'] | EXPECTED['local_wire']}))

    # build the conductor adjacency graph and assert it is one simple path in order
    adj: dict[int, set[int]] = {}
    for cs, _ in edges:
        (a, _), (b, _) = cs
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    check('the conductor chain is connected',
          sorted(adj), CHAIN)
    degrees = sorted((len(adj.get(c, ())), c) for c in CHAIN)
    check('the chain has exactly two ends and no branches',
          [deg for deg, _ in degrees], [1, 1] + [2] * (len(CHAIN) - 2))
    ordered = [min(c for c in CHAIN if len(adj[c]) == 1)]
    while len(ordered) < len(CHAIN):
        nxt = [c for c in adj[ordered[-1]] if c not in ordered]
        if not nxt:
            break
        ordered.append(nxt[0])
    check('the chain reproduces the layer order', ordered, CHAIN)
    # each adjacent pair must be bridged by exactly one cut layer
    cuts_by_edge = {}
    for cs, cut in edges:
        (a, _), (b, _) = cs
        cuts_by_edge.setdefault(tuple(sorted((a, b))), set()).add(cut)
    check('each adjacent pair is bridged by exactly one cut layer',
          {k: sorted(v) for k, v in cuts_by_edge.items() if len(v) != 1}, {})

    # ---- 3. exact roles for the connectivity-critical layers -------------------
    for role, expected in EXPECTED.items():
        got = {(l, dt) for l, dt in P if P[(l, dt)]['role'] == role}
        check(f'role {role} is exactly the expected set', got, expected)

    # ---- 4. derived facts ------------------------------------------------------
    check('site pitch is derived, not assumed',
          d['derived_site_pitch_um'], 0.46)
    check('pin datatype is derived, not assumed',
          d['derived_pin_datatype']['datatype'], 16)
    ev = d['derived_pin_datatype']['evidence']
    # the top cell carries 13 port labels (layer 70) plus 4 power labels (71, 72);
    # only layer 70's labels stand in 1:1 correspondence with shapes on one datatype.
    check('pin datatype derivation saw every top-cell label', ev['top_labels'], 17)
    check('top-cell labels sit on the layers Step 1 recorded',
          ev['labels_by_layer'], {'70': 13, '71': 2, '72': 2})
    check('pin datatype chosen by 1:1 shape/label correspondence',
          ev['candidates'].get('70/16'),
          {'shapes': 13, 'labels': 13, 'one_shape_per_label': True})
    check('drawing layer failed the 1:1 test',
          ev['candidates'].get('70/20', {}).get('one_shape_per_label'), False)
    check('power layers are rejected by the shape-count test',
          [(k, ev['candidates'][k]['shapes'], ev['candidates'][k]['labels'])
           for k in ('71/16', '72/16')],
          [('71/16', 12, 2), ('72/16', 18, 2)])
    # 83/44 carries cell names rather than net names, but it is still a label layer
    check('label layers are derived, not assumed',
          d['derived_label_layers'], [64, 67, 68, 70, 71, 72, 83])
    check('the cell-name label layer is text-only',
          P[(83, 44)]['role'], 'label')

    # ---- 5. re-derivation of Step 1's independent measurements -----------------
    check('structure count agrees with Step 1', d['cells']['total'], g['structure_count'])
    check('standard-cell masters agree with Step 1',
          d['cells']['std_masters'], g['standard_cell_masters'])
    check('via master count', len(d['cells']['via_masters']), 9)
    check('other masters are the two INTERNAL_* cells',
          d['cells']['other_masters'], ['INTERNAL_3', 'INTERNAL_7'])
    check('die bbox agrees with Step 1',
          {'x0': d['die_bbox_um'][0][0], 'y0': d['die_bbox_um'][0][1],
           'x1': d['die_bbox_um'][1][0], 'y1': d['die_bbox_um'][1][1],
           'w': round(d['die_bbox_um'][1][0] - d['die_bbox_um'][0][0], 4),
           'h': round(d['die_bbox_um'][1][1] - d['die_bbox_um'][0][1], 4)},
          g['die_bbox_um'])
    check('source hash present', len(d['source']['sha256']), 64)
    check('via placements total Step 1\'s routed-via count',
          sum(d['via_placements'].values()), VIA_PLACEMENT_TOTAL)
    check('every via master is instantiated at least once',
          [n for n, c in d['via_placements'].items() if c == 0], [])

    # 33 geometry-bearing pairs + 8 text-only pairs = 41, reconciled explicitly
    geo_pairs = {k for k, r in P.items()
                 if r['elements'].get('boundary', 0) + r['elements'].get('path', 0) > 0}
    text_only = {k for k, r in P.items()
                 if r['elements'].get('text', 0) > 0
                 and r['elements'].get('boundary', 0) + r['elements'].get('path', 0) == 0}
    check('Step 1\'s 33 geometry-bearing pairs are contained in ours',
          sorted(geo_pairs - {tuple(int(x) for x in s.split('/'))
                              for s in g['layers']}), [])
    check('Step 1\'s 33 + our 8 text-only pairs = 41',
          len(g['layers']) + len(text_only), len(P))
    check('the text-only pairs are exactly the label role',
          text_only, EXPECTED['label'])

    # the top-cell port labels land on the layer A1 calls a label layer
    ports = {l['text'] for l in g['top_labels'] if l['layer'] == 70}
    check('top-cell port names agree with Step 1', ports, PORT_NAMES)
    check('layer 70 is a label layer', 70 in d['derived_label_layers'], True)

    # markers Step 1 identified independently
    check('the 236/0 cell-footprint marker is non-electrical',
          P[(236, 0)]['role'], 'non_electrical')
    check('236/0 notes name the footprint-marker subkind',
          any('footprint_marker' in n for n in P[(236, 0)]['notes']), True)
    check('the die outline (235/4) is non-electrical',
          P[(235, 4)]['role'], 'non_electrical')
    check('the die outline subkind is die_outline',
          any('die_outline' in n for n in P[(235, 4)]['notes']), True)
    check('the outside-die decoration (200/0) is non-electrical',
          P[(200, 0)]['role'], 'non_electrical')
    check('200/0 subkind is outside_die',
          any('outside_die' in n for n in P[(200, 0)]['notes']), True)
    check('200/0 is confined to the INTERNAL_* cells',
          P[(200, 0)]['by_cell_kind']['STD'], 0)
    check('236/0 appears in 68 of 69 masters',
          P[(236, 0)]['geometry']['masters_containing'], 68)

    # ---- 6. honesty: review flags may not touch the connectivity roles ---------
    review = {tuple(k) for k in d['summary']['needs_review']}
    check('no connectivity-critical pair is flagged for review',
          sorted(review & {k for k, r in P.items() if r['role'] in CONNECTIVITY_ROLES}), [])
    check('pairs flagged for review all carry a subkind or explanation',
          [f'{l}/{dt}' for l, dt in review
           if not any(re.match(r'subkind=', n) or len(n) > 20 for n in P[(l, dt)]['notes'])], [])

    # ---- 7. the summary block is derived data too, so it must equal its derivation ---
    # Injected faults that only corrupt `summary` (or only `roles`) previously fired no
    # gate at all: the block was written but never checked against the pairs it summarises.
    by_role: dict[str, int] = {}
    for r in d['pairs']:
        by_role[r['role']] = by_role.get(r['role'], 0) + 1
    check('the summary pair count is the real pair count',
          d['summary']['pairs'], len(d['pairs']))
    check('the summary role histogram is the real histogram',
          d['summary']['by_role'], dict(sorted(by_role.items())))
    check('the summary review list is exactly the pairs flagged for review',
          sorted(tuple(k) for k in d['summary']['needs_review']),
          sorted((r['layer'], r['datatype']) for r in d['pairs'] if r['needs_review']))
    check('the per-pair review flag agrees with the confidence level',
          sorted(f"{r['layer']}/{r['datatype']}" for r in d['pairs']
                 if r['needs_review'] != (r['confidence'] != 'high')), [])

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
        print(f'STEP A1 GATE: FAIL ({n_fail} failing)')
        return 1
    print('STEP A1 GATE: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
