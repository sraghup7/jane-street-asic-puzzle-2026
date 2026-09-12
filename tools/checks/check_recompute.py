#!/usr/bin/env python3
"""check_recompute.py -- independent recomputation of the load-bearing numbers.

Every other gate is written by the same hand as the code it judges, and mostly checks one
derived artifact against another. This one is deliberately different: it re-derives the
load-bearing facts **from the raw artifacts with its own code**, importing nothing from
`tools/puzzle` and reusing none of the check helpers. Agreement is therefore evidence
rather than a restatement, and this is the only check in the suite that can catch a
pipeline that is *self-consistently wrong*.

It also validates, before Phase B starts, the two facts Phase B leans on hardest:

  * the warm-up's cell masters are geometrically identical to the puzzle's, so A4's pin
    model transfers to the warm-up without re-derivation;
  * the placement transforms recovered from the GDS match the warm-up DEF's own
    orientation tokens (N / FS / FN / S) -- a pre-validation of B1's transform convention
    against an independent file, count for count.

Convention trap this gate documents by construction: **gdstk returns coordinates in
microns (floats), not in database units.** A4's artifacts store `*_um` fields for the same
reason. B1 must convert once at read time (`round(v * dbu_per_um)`) and stay integral
after, per docs/03_our_plan.md sec.5 convention 1.

    .venv/Scripts/python tools/checks/check_recompute.py
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

import gdstk

ROOT = Path(__file__).resolve().parents[2]

GDS = ROOT / 'asic-puzzle-2026' / 'puzzle.gds'
WU_GDS = ROOT / 'asic-puzzle-2026' / 'warmup' / '04_final.gds'
WU_DEF = ROOT / 'asic-puzzle-2026' / 'warmup' / '03_post_place_and_route.def'
WU_NET = ROOT / 'asic-puzzle-2026' / 'warmup' / '01_netlist.v'

A1 = ROOT / 'recon' / 'derived' / 'layers.json'
A2 = ROOT / 'recon' / 'derived' / 'via_pairs.json'
A3 = ROOT / 'recon' / 'derived' / 'pin_names.json'
A4 = ROOT / 'recon' / 'derived' / 'pinmodel.json'
A5 = ROOT / 'recon' / 'derived' / 'pin_coverage.json'
INV = ROOT / 'recon' / 'inventory.json'

STD = 'sky130_fd_sc_hd__'
VIA = 'VIA_'
CELLNAME_PAIR = (83, 44)

# gdstk rotation is in radians; the DEF writes quarter-turns. This mapping is a
# *hypothesis* that section 7 tests against the DEF rather than assuming.
KIND = {(0, False): 'N', (0, True): 'FS', (2, True): 'FN', (2, False): 'S'}

DEF_COMPONENT = re.compile(
    r'^\s*-\s+\S+\s+\S+\s+\+.*?\(\s*-?\d+\s+-?\d+\s*\)\s+(\w+)\s*;')

results: list[tuple[str, str, object, object]] = []


def check(label: str, actual, expected) -> None:
    results.append(('PASS' if actual == expected else 'FAIL', label, actual, expected))


def load(p: Path):
    return json.loads(p.read_text(encoding='utf-8'))


def poly_area(p) -> float:
    pts = p.points
    s = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def geom_sig(cell) -> Counter:
    return Counter((p.layer, p.datatype, len(p.points)) for p in cell.polygons)


def main() -> int:
    for p in (GDS, WU_GDS, WU_DEF, WU_NET):
        if not p.exists():
            print(f'missing upstream input {p.relative_to(ROOT)}')
            return 1

    a1, a2, a3, a4, a5, inv = (load(p) for p in (A1, A2, A3, A4, A5, INV))
    g = inv['gds']

    lib = gdstk.read_gds(str(GDS))
    tops = lib.top_level()
    top = tops[0]

    # ---- 1. the file itself ------------------------------------------------------
    check('library name', lib.name, g['library_name'])
    check('structure count', len(lib.cells), g['structure_count'])
    check('single top-level cell', len(tops), 1)
    check('top cell name', top.name, g['top_cell'])

    pairs = Counter()
    for c in lib.cells:
        for p in c.polygons:
            pairs[(p.layer, p.datatype)] += 1
        for fp in c.paths:
            for l, dt in zip(fp.layers, fp.datatypes):
                pairs[(l, dt)] += 1
        for L in c.labels:
            pairs[(L.layer, L.texttype)] += 1
    label_pairs = {(r['layer'], r['datatype']) for r in a1['pairs'] if r['role'] == 'label'}
    check('distinct (layer,datatype) pairs in the file', len(pairs), len(a1['pairs']))
    check('the pair set matches exactly',
          sorted(pairs), sorted((r['layer'], r['datatype']) for r in a1['pairs']))
    # Step 1's census counts boundary+path elements only, so it sees 33; the other 8 are
    # text-only label layers. This is that reconciliation, recomputed independently.
    geom_only = {k for k in pairs if k not in label_pairs}
    check('geometry-only pairs == Step 1\'s 33', len(geom_only), 33)
    check('label-only pairs make up the difference (33 + 8 = 41)',
          len(pairs) - len(geom_only), 8)

    # ---- 2. placements -----------------------------------------------------------
    refs = top.references
    std = [r for r in refs if r.cell.name.startswith(STD)]
    vias = [r for r in refs if r.cell.name.startswith(VIA)]
    other = [r for r in refs if not r.cell.name.startswith((STD, VIA))]
    check('standard-cell placements', len(std), g['standard_cell_instances'])
    check('via-master placements', len(vias),
          sum(v for k, v in g['census'].items() if k.startswith(VIA)))
    check('other placements (INTERNAL_*)', len(other),
          g['instances_total'] - len(std) - len(vias))
    check('distinct standard-cell masters placed', len({r.cell.name for r in std}),
          len(a3['masters']))
    check('standard-cell masters in the library',
          len({c.name for c in lib.cells if c.name.startswith(STD)}), 69)

    # ---- 3. geometry convention + the placement grid -----------------------------
    dbu = g['dbu_per_um']
    pitch = a1['derived_site_pitch_um']
    row_pitch = g['row_pitch_um']

    def dbu_int(v: float) -> int:
        return int(round(v * dbu))

    xs_um = sorted({r.origin[0] for r in std})
    ys_um = sorted({r.origin[1] for r in std})
    check('gdstk coordinates are microns, not DBU (row pitch reproduced)',
          dbu_int(ys_um[1] - ys_um[0]), dbu_int(row_pitch))
    check('every placement x is on the site pitch',
          sorted({dbu_int(x) % dbu_int(pitch) for x in xs_um}), [0])
    check('every placement y is on the row pitch',
          sorted({dbu_int(y) % dbu_int(row_pitch) for y in ys_um}), [0])

    # ---- 4. the via masters, from their own geometry -----------------------------
    # The cut is the layer with the least total area -- a rule that needs no role
    # classification. It decides 8 of the 9 masters outright. VIA_L1M1_PR_MR ties,
    # because its li1 landing pad is drawn the same size as its mcon cut (both 0.17 um
    # square); that tie is broken by the datatype convention the other eight establish.
    # (A1's global role derivation handles this case without a tie-break at all, which is
    # worth knowing: the naive per-master rule is the fragile one.)
    per_master: dict[str, dict] = {}
    for c in lib.cells:
        if not c.name.startswith(VIA):
            continue
        d: dict = {}
        for p in c.polygons:
            d.setdefault((p.layer, p.datatype), []).append(p)
        per_master[c.name] = d
    check('every via master has exactly 3 layer pairs',
          sorted(n for n, d in per_master.items() if len(d) != 3), [])
    check('via master count', len(per_master), 9)

    def total_area(d, k) -> float:
        return sum(poly_area(p) for p in d[k])

    by_area = {n: sorted(d, key=lambda k: total_area(d, k)) for n, d in per_master.items()}
    cut, deferred = {}, []
    for n, ks in by_area.items():
        d = per_master[n]
        if total_area(d, ks[0]) < total_area(d, ks[1]) - 1e-12:
            cut[n] = ks[0]
        else:
            deferred.append(n)
    check('masters where the least-area layer decides the cut outright', len(cut), 8)
    check('the one master that ties on area', deferred, ['VIA_L1M1_PR_MR'])
    cond_dt = sorted({k[1] for n in cut for k in per_master[n] if k != cut[n]})
    check('conductors share one datatype across the resolved masters', cond_dt, [20])
    check('cuts share one datatype across the resolved masters',
          sorted({cut[n][1] for n in cut}), [44])
    for n in deferred:
        tie = [k for k in per_master[n] if k[1] == 44]
        check(f'{n}: exactly one tied candidate is a cut', len(tie), 1)
        cut[n] = tie[0]
    check('the tie resolves to the datatype-44 layer', cut['VIA_L1M1_PR_MR'], (67, 44))

    derived = {n: tuple(sorted(k for k in per_master[n] if k != cut[n])) for n in per_master}

    # the enclosure invariant, re-proved independently by exact polygon intersection
    unenclosed = []
    for n, d in per_master.items():
        others = [q for k in per_master[n] if k != cut[n] for q in d[k]]
        for p in d[cut[n]]:
            inter = gdstk.boolean([p], others, 'and', precision=1e-4)
            ia = sum(poly_area(g) for g in inter)
            pa = poly_area(p)
            if abs(ia - pa) > 1e-6 * pa:
                unenclosed.append(n)
                break
    check('every cut is fully enclosed by both metals it bridges',
          sorted(unenclosed), [])

    declared = {}
    for r in a2['pairs']:
        cons = tuple(sorted(tuple(int(x) for x in s.split('/')) for s in r['connects']))
        for m in r['masters']:
            declared[m] = cons
    check('independently derived via pairs == the committed rule set',
          dict(sorted(derived.items())), dict(sorted(declared.items())))
    check('the via masters span one conductor chain',
          sorted({l for v in derived.values() for l, _ in v}), [67, 68, 69, 70, 71, 72])

    # ---- 5. labels in the cell masters ------------------------------------------
    pin, cellname = Counter(), 0
    for c in lib.cells:
        if not c.name.startswith(STD):
            continue
        for L in c.labels:
            k = (L.layer, L.texttype)
            if k == CELLNAME_PAIR:
                cellname += 1
            else:
                pin[k] += 1
    check('every label layer is classified as a label',
          sorted((set(pin) | {CELLNAME_PAIR}) - label_pairs), [])
    check('pin labels in the masters',
          sum(pin.values()), a5['label_census']['total'] - cellname)
    check('cell-name labels', cellname, 73)
    check('pin labels match A3 per layer',
          dict(sorted((f'{l}/{d}', n) for (l, d), n in pin.items())),
          dict(sorted(a3['label_counts_by_layer'].items())))
    check('masters carrying the 236/0 footprint marker',
          sum(1 for c in lib.cells if c.name.startswith(STD)
              and any((p.layer, p.datatype) == (236, 0) for p in c.polygons)), 68)

    # ---- 6. the warm-up ----------------------------------------------------------
    wlib = gdstk.read_gds(str(WU_GDS))
    wtop = wlib.top_level()[0]
    w_by = {c.name: c for c in wlib.cells}
    main_by = {c.name: c for c in lib.cells}
    w_std = [r for r in wtop.references if r.cell.name.startswith(STD)]
    w_via = [r for r in wtop.references if r.cell.name.startswith(VIA)]
    w_types = {r.cell.name for r in w_std}

    check('warm-up top cell', wtop.name, a4['warmup_calibration']['warmup_top_cell'])
    check('warm-up masters are geometrically identical to the puzzle\'s',
          sorted(n for n in w_types if geom_sig(w_by[n]) != geom_sig(main_by[n])), [])
    check('warm-up via masters are geometrically identical',
          sorted({r.cell.name for r in w_via
                  if geom_sig(w_by[r.cell.name]) != geom_sig(main_by[r.cell.name])}), [])
    check('warm-up cell types used', len(w_types),
          a4['warmup_calibration']['cell_types'])

    def_text = WU_DEF.read_text(encoding='utf-8')
    n_def = int(re.search(r'^COMPONENTS (\d+) ;', def_text, re.M).group(1))
    inst_lines = re.findall(r'^\s*(sky130_fd_sc_hd__\w+)\s+(\S+)\s*\(',
                            WU_NET.read_text(encoding='utf-8'), re.M)
    check('warm-up placements == DEF components == netlist instances',
          (len(w_std), n_def, len(inst_lines)), (230, 230, 230))
    check('netlist cell types are the placed types',
          sorted({t for t, _ in inst_lines}), sorted(w_types))
    check('netlist instance names are unique',
          len({n for _, n in inst_lines}), len(inst_lines))

    # ---- 7. transform kinds, against the DEF's own orientation tokens ------------
    gds_kinds = Counter(
        KIND.get((int(round(r.rotation / (math.pi / 2))) % 4, bool(r.x_reflection)), '?')
        for r in w_std)
    def_tokens = Counter(m.group(1) for m in
                         (DEF_COMPONENT.match(ln) for ln in def_text.splitlines()) if m)
    check('no placement has an unexpected transform',
          sorted(gds_kinds), ['FN', 'FS', 'N', 'S'])
    check('GDS transform kinds match the DEF orientation tokens, count for count',
          dict(sorted(gds_kinds.items())), dict(sorted(def_tokens.items())))

    # ---- report ------------------------------------------------------------------
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
        print(f'INDEPENDENT RECOMPUTATION: FAIL ({n_fail} failing)')
        return 1
    print('INDEPENDENT RECOMPUTATION: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
