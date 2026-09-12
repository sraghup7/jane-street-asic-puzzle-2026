#!/usr/bin/env python3
"""check_stepA2.py -- executable gate for step A2 (the connectivity rule set).

The claim A2 must earn is that the connectivity rules are *proved*, not inferred. So the
gate does not merely read the artifact back: it re-derives the whole rule set from the GDS
and requires a byte-identical result, then checks the physics of every via:

  - each via master is a cell whose geometry is only conductors and cuts;
  - each cut shares area with exactly two conductors and nothing else;
  - the shared area equals the cut's own area, i.e. every cut is fully enclosed by both
    metals it bridges -- the enclosure invariant a real via must satisfy;
  - the rules form a single path over the six conductors;
  - the instances account for every routed via.

    .venv/Scripts/python tools/checks/check_stepA2.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import gdstk                                            # noqa: E402
from tools.puzzle import layers as L                    # noqa: E402

A1 = ROOT / 'recon' / 'derived' / 'layers.json'
A2 = ROOT / 'recon' / 'derived' / 'via_pairs.json'
INV = ROOT / 'recon' / 'inventory.json'

EXPECTED_RULES = {
    '67/44': ('67/20', '68/20'),
    '68/44': ('68/20', '69/20'),
    '69/44': ('69/20', '70/20'),
    '70/44': ('70/20', '71/20'),
    '71/44': ('71/20', '72/20'),
}
CHAIN = [67, 68, 69, 70, 71, 72]
VIA_INSTANCES = 8221

results: list[tuple[str, str, object, object]] = []


def check(label, actual, expected):
    results.append(('PASS' if actual == expected else 'FAIL', label, actual, expected))


def main() -> int:
    for p in (A1, A2):
        if not p.exists():
            print(f'missing {p.relative_to(ROOT)} -- run the A1 and via-pairs stages first')
            return 1
    committed = A2.read_text(encoding='utf-8')
    d = json.loads(committed)
    a1 = json.loads(A1.read_text(encoding='utf-8'))
    inv = json.loads(INV.read_text(encoding='utf-8'))

    # ---- 1. regenerability: re-derive from the GDS and demand a byte-identical result
    roles = {(r['layer'], r['datatype']): r['role'] for r in a1['pairs']}
    lib = gdstk.read_gds(str(L.GDS))
    top = lib.top_level()[0]
    fresh = L.derive_via_pairs(lib, top.name, roles)
    fresh_doc = {'generated_by': 'tools/puzzle/layers.py::stage_via_pairs',
                 'source': {'path': 'asic-puzzle-2026/puzzle.gds',
                            'sha256': d['source']['sha256']},
                 'input': 'recon/derived/layers.json', **fresh}
    check('via_pairs regenerates byte-identically',
          json.dumps(fresh_doc, indent=2, sort_keys=False) + '\n' == committed, True)

    # ---- 2. the via masters ----------------------------------------------------
    check('via master count', len(d['masters']), 9)
    shapes_bad = [m['name'] for m in d['masters'] for c in m['cuts'].values()
                  if len(c['connected_to']) != 2]
    check('every via master cut connects exactly two conductors', shapes_bad, [])
    not_well_formed = [f"{m['name']}:{k}" for m in d['masters']
                       for k, c in m['cuts'].items() if not c.get('is_well_formed')]
    check('every via master is well formed', not_well_formed, [])
    check('each via master has exactly one cut',
          sorted({len(m['cuts']) for m in d['masters']}), [1])

    # ---- 3. the enclosure invariant: shared area == the cut's own area ---------
    # A real via's cut is entirely inside both metals. If a cut only partially overlapped
    # a metal, or reached a third layer, this would fail.
    violations = []
    for m in d['masters']:
        for cut_key, c in m['cuts'].items():
            for cond, det in c['detail'].items():
                if abs(det['shared_area_um2'] - c['cut_area_um2']) > 1e-6:
                    violations.append(
                        f"{m['name']}:{cut_key} vs {cond}: shared "
                        f"{det['shared_area_um2']} != cut area {c['cut_area_um2']}")
    check('every cut is fully enclosed by both metals it bridges', violations, [])
    no_enclosure = [f"{m['name']}:{k}:{c}" for m in d['masters']
                    for k, cut in m['cuts'].items() for c, det in cut['detail'].items()
                    if det['shared_area_um2'] > 0 and not det['cut_enclosed_by_metal']]
    check('enclosure confirmed by bbox containment too', no_enclosure, [])

    # ---- 4. the rule set -------------------------------------------------------
    got = {r['cut']: tuple(r['connects']) for r in d['pairs']}
    check('the rule set is exactly the five expected cut->pair rules', got, EXPECTED_RULES)
    check('every cut layer yields exactly one rule', len(d['pairs']), len(EXPECTED_RULES))
    check('the rules cover exactly A1\'s set of via_cut layers',
          sorted(got), sorted(f'{k[0]}/{k[1]}' for k, r in roles.items()
                              if r == 'via_cut'))

    # ---- 5. the conductor graph is a single path -------------------------------
    g = d['conductor_graph']
    check('conductor graph nodes are A1\'s routing + local_wire layers',
          g['nodes'], sorted(f'{k[0]}/{k[1]}' for k, r in roles.items()
                             if r in ('routing', 'local_wire')))
    check('conductor graph is connected', sorted(int(n) for n in g['adjacency']), CHAIN)
    degs = sorted(len(v) for v in g['adjacency'].values())
    check('conductor graph is a simple path (two ends, no branches)',
          degs, [1, 1] + [2] * (len(CHAIN) - 2))
    ends = sorted(int(n) for n, v in g['adjacency'].items() if len(v) == 1)
    check('the path runs lowest to highest conductor', ends, [CHAIN[0], CHAIN[-1]])

    # ---- 6. instances account for every routed via -----------------------------
    check('via instances total Step 1\'s routed-via count',
          d['totals']['via_instances'], VIA_INSTANCES)
    check('sum over rules equals the total',
          sum(r['instances'] for r in d['pairs']), VIA_INSTANCES)
    check('every via master is instantiated at least once',
          [m['name'] for m in d['masters'] if m['instances'] == 0], [])
    # mirrored variants must bridge the same pair as their base -- but only where the
    # base exists. The file contains only the mirrored li1/met1 via, with no
    # non-mirrored VIA_L1M1_PR, so that one has nothing to compare against.
    pairs_by_master = {m['name']: tuple(next(iter(m['cuts'].values()))['connected_to'])
                       for m in d['masters']}
    mirror_masters = sorted(n for n in pairs_by_master if n.endswith('_MR'))
    check('the mirrored via masters are the two expected ones',
          mirror_masters, ['VIA_L1M1_PR_MR', 'VIA_M1M2_PR_MR'])
    check('VIA_L1M1_PR_MR has no non-mirrored counterpart in the file',
          'VIA_L1M1_PR' in pairs_by_master, False)
    check('VIA_M1M2_PR_MR breaks the tie',
          pairs_by_master['VIA_M1M2_PR'] == pairs_by_master['VIA_M1M2_PR_MR'], True)
    # The names are not merely ambiguous, they are contradictory: `M2M3` and `via2_3`
    # both say "2" and "3" but bridge pairs one layer apart. Pinned so that no later
    # refactor "simplifies" the code by parsing the names instead of the geometry.
    check('the names disagree: M2M3 and via2_3 bridge different pairs',
          pairs_by_master['VIA_M2M3_PR'] != pairs_by_master['VIA_via2_3_2000_480_1_6_320_320'],
          True)
    check('via5_6 sits on met4 -> met5, not layers 5 and 6',
          pairs_by_master['VIA_via5_6_2000_2000_1_1_1600_1600'], ('71/20', '72/20'))

    # ---- 7. cells that use a cut internally ------------------------------------
    internal = d['cells_using_cuts_internally']
    check('one internal-cut group, using 67/44', [e['cuts_used'] for e in internal],
          [['67/44']])
    check('all 69 standard cells use it',
          internal[0]['cells'], inv['gds']['standard_cell_masters'])
    check('internal-cut instances equal the standard-cell placement count',
          internal[0]['instances'], inv['gds']['standard_cell_instances'])

    # ---- 8. contact layers no via master uses ----------------------------------
    contacts = {c['layer']: c for c in d['contact_layers_without_via']}
    check('two contact layers are unclaimed by any via master',
          sorted(contacts), ['122/16', '66/44'])
    # 66/44 is a *shared* contact: it reaches diffusion or poly from below, li1 above
    upstream = {k for k in contacts['66/44']['partners'] if k in ('65/20', '66/20')}
    check('66/44 reaches at least two different layers from below',
          len(upstream) >= 2, True)
    check('66/44 always reaches li1 above',
          contacts['66/44']['partners']['67/20'] >= 2000, True)
    check('122/16 reaches li1 and met1',
          sorted(k for k in contacts['122/16']['partners']
                 if k in ('67/20', '68/20')), ['67/20', '68/20'])

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
        print(f'STEP A2 GATE: FAIL ({n_fail} failing)')
        return 1
    print('STEP A2 GATE: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
