#!/usr/bin/env python3
"""check_stepB1.py -- executable gate for step B1 (the instance table).

The claim B1 must earn is that the transform convention is *measured*, not assumed, and
that `transform(master_bbox) == instance_global_bbox` for all 1618 placements. So this
gate re-runs that experiment itself, against the raw GDS, and additionally requires that
the matrix chosen per placement kind is the *only* one that works -- a convention that is
merely one of several fits is not a convention.

Beyond the plan's stated verify clause it also asserts:

  * the **placements do not overlap.** Each instance's cell footprint (the `236/0` marker
    A1 identified, whose global rectangle this step now computes) must be disjoint from
    every other's. This is what makes the table a legal placement rather than 1618
    rectangles that happen to be near each other.
  * the **warm-up DEF still witnesses the convention**: 230 placements with explicit
    coordinates and orientation tokens, matched by cell and exact lower-left corner, with
    a token -> kind map that is a bijection. That is an independent file corroborating the
    convention rather than gdstk's own arithmetic agreeing with itself.

Run from the repository root:

    .venv/Scripts/python tools/checks/check_stepB1.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import gdstk

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.checks._regen import regenerate             # noqa: E402
from tools.puzzle import instances as I                # noqa: E402

A1 = ROOT / 'recon' / 'derived' / 'layers.json'
B1 = ROOT / 'recon' / 'derived' / 'instances.json'
A3 = ROOT / 'recon' / 'derived' / 'pin_names.json'
INV = ROOT / 'recon' / 'inventory.json'

STD = I.STD
KINDS = {'rot000', 'rot000_mirror', 'rot180', 'rot180_mirror'}

results: list[tuple[str, str, object, object]] = []


def check(label: str, actual, expected) -> None:
    results.append(('PASS' if actual == expected else 'FAIL', label, actual, expected))


def main() -> int:
    for p in (B1, A1, INV):
        if not p.exists():
            print(f'missing {p.relative_to(ROOT)} -- run `python -m tools.puzzle instances`')
            return 1
    before = B1.read_bytes()
    d = json.loads(before)
    inv = json.loads(INV.read_text(encoding='utf-8'))
    a1 = json.loads(A1.read_text(encoding='utf-8'))
    a3 = json.loads(A3.read_text(encoding='utf-8'))
    g = inv['gds']
    per_um = int(g['dbu_per_um'])

    # ---- 1. regenerability (hermetic) -------------------------------------------
    rc, produced, _ = regenerate('tools.puzzle.instances', ('OUT',), 'instances')
    check('the instances stage exits 0', rc, 0)
    check('the artifact regenerates byte-identically', produced == before, True)
    check('regeneration did not touch the working tree', B1.read_bytes() == before, True)

    table = d['instances']

    # ---- 2. totals --------------------------------------------------------------
    t = d['totals']
    check('standard-cell placements', t['instances'], g['standard_cell_instances'])
    check('distinct masters', t['masters'], len(a3['masters']))
    check('placement kinds', t['kinds'], g['std_cell_transforms'])
    check('placement kind set', sorted(t['kinds']), sorted(KINDS))
    check('rows used', t['rows'], g['std_cell_row_count'])
    check('per-master counts match Step 1 census',
          d['per_master'], {k: v for k, v in g['census'].items() if k.startswith(STD)})
    check('ids are unique and dense',
          sorted(e['id'] for e in table),
          [f'i{n:04d}' for n in range(len(table))])

    # ---- 3. the convention, re-measured here against the raw file ----------------
    lib = gdstk.read_gds(str(ROOT / 'asic-puzzle-2026' / 'puzzle.gds'))
    top = lib.top_level()[0]
    refs = [r for r in top.references if r.cell.name.startswith(STD)]
    check('placements in the GDS', len(refs), len(table))

    chosen = {k: tuple(v) for k, v in d['convention']['chosen_by_kind'].items()}
    check('a matrix was chosen for every kind', sorted(chosen), sorted(KINDS))
    check('every chosen matrix is a candidate',
          set(chosen.values()) <= {tuple(v) for v in I.R_CANDIDATES.values()}, True)

    # rebuild master bboxes and footprints straight from the GDS
    m_bbox, m_foot, marker_masters = {}, {}, []
    for r in refs:
        n = r.cell.name
        if n in m_bbox:
            continue
        x0, y0, x1, y1 = I.flat_bbox(r.cell)
        m_bbox[n] = tuple(I.dbu(v, per_um) for v in (x0, y0, x1, y1))
        fp = r.cell.get_polygons(layer=I.FOOTPRINT[0], datatype=I.FOOTPRINT[1])
        if fp:
            marker_masters.append(n)
            m_foot[n] = tuple(I.dbu(v, per_um) for v in I.polys_bbox(fp))
        else:
            m_foot[n] = tuple(m_bbox[n][i] + (190, 240, -190, -240)[i] for i in range(4))

    check('artifacts\' master bboxes match the GDS',
          {k: tuple(v) for k, v in d['master_bboxes_dbu'].items()},
          {k: v for k, v in sorted(m_bbox.items())})
    check('artifacts\' master footprints match the GDS',
          {k: tuple(v) for k, v in d['master_footprints_dbu'].items()},
          {k: v for k, v in sorted(m_foot.items())})

    # the decisive test, in the gate, for all 1618
    mismatched, reproduced = [], 0
    for r in refs:
        kind = I.KIND_OF[(int(round(r.rotation / (3.141592653589793 / 2))) % 4,
                          bool(r.x_reflection))]
        origin = (I.dbu(r.origin[0], per_um), I.dbu(r.origin[1], per_um))
        want = tuple(I.dbu(v, per_um) for v in I.flat_bbox(r))
        if I.apply(chosen[kind], origin, m_bbox[r.cell.name]) == want:
            reproduced += 1
        else:
            mismatched.append(r.cell.name)
    check('transform(master_bbox) == instance_global_bbox for all 1618',
          (reproduced, sorted(set(mismatched))), (1618, []))

    # and the chosen matrix must be the ONLY one that fits each kind
    not_unique = []
    for kind in KINDS:
        hits = {}
        for name, mat in I.R_CANDIDATES.items():
            n = sum(1 for r in refs
                    if I.KIND_OF[(int(round(r.rotation / (3.141592653589793 / 2))) % 4,
                                  bool(r.x_reflection))] == kind
                    and I.apply(mat, (I.dbu(r.origin[0], per_um), I.dbu(r.origin[1], per_um)),
                                m_bbox[r.cell.name])
                    == tuple(I.dbu(v, per_um) for v in I.flat_bbox(r)))
            hits[name] = n
        winners = [k for k, n in hits.items() if n == d['totals']['kinds'][kind]]
        if len(winners) != 1 or I.R_CANDIDATES[winners[0]] != chosen[kind]:
            not_unique.append((kind, winners))
    check('the chosen matrix is the unique fit for each kind', not_unique, [])

    # ---- 4. the instance table's own geometry -----------------------------------
    check('every instance records a footprint',
          all(len(e['footprint_dbu']) == 4 for e in table), True)
    check('every bbox contains its own footprint',
          [e['id'] for e in table
           if not (e['bbox_dbu'][0] <= e['footprint_dbu'][0]
                   and e['bbox_dbu'][1] <= e['footprint_dbu'][1]
                   and e['bbox_dbu'][2] >= e['footprint_dbu'][2]
                   and e['bbox_dbu'][3] >= e['footprint_dbu'][3])], [])
    check('every global bbox is unique',
          len({tuple(e['bbox_dbu']) for e in table}), len(table))

    site_dbu = I.dbu(a1['derived_site_pitch_um'], per_um)
    row_dbu = I.dbu(g['row_pitch_um'], per_um)
    check('every anchor x is on the site pitch',
          sorted({e['origin_dbu'][0] % site_dbu for e in table}), [0])
    check('every anchor y is on the row pitch',
          sorted({e['origin_dbu'][1] % row_dbu for e in table}), [0])
    rows = sorted({e['row_line'] for e in table})
    check('row lines are contiguous and complete', rows,
          list(range(rows[0], rows[0] + t['rows'])))
    fp_heights = sorted({e['footprint_dbu'][3] - e['footprint_dbu'][1] for e in table})
    check('every footprint is exactly the cell row height', fp_heights, [2720])
    # The design places cells on row lines 5440 apart, and a cell is 2720 tall: a cell
    # anchored at a line with sy=+1 occupies the band above it, one with sy=-1 the band
    # below, so a 5440 band carries two stacked cells sharing one anchor y. That is what
    # makes the co-anchored pairs from section 4 legal rather than overlapping.
    check('the row pitch is two cell rows', row_dbu // fp_heights[0], 2)
    check('every footprint width is a whole number of sites',
          sorted({(e['footprint_dbu'][2] - e['footprint_dbu'][0]) % site_dbu
                  for e in table}), [0])

    # ---- 5. the placements must not overlap -------------------------------------
    # Strictly disjoint rectangles: sharing an edge is adjacency, not overlap.
    by_y = sorted(table, key=lambda e: (e['footprint_dbu'][1], e['footprint_dbu'][0]))
    overlaps = []
    for i, a in enumerate(by_y):
        ax0, ay0, ax1, ay1 = a['footprint_dbu']
        for b in by_y[i + 1:]:
            bx0, by0, bx1, by1 = b['footprint_dbu']
            if by0 >= ay1:
                break
            if ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1:
                overlaps.append((a['id'], b['id']))
    check('no two placements overlap', overlaps, [])

    # ---- 6. the DEF oracle, re-derived ------------------------------------------
    def_text = (ROOT / 'asic-puzzle-2026' / 'warmup' / '03_post_place_and_route.def') \
        .read_text(encoding='utf-8')
    defc = [{'name': m.group(1), 'cell': m.group(2),
             'xy': (int(m.group(3)), int(m.group(4))), 'orient': m.group(5)}
            for m in (I.DEF_COMPONENT.match(ln) for ln in def_text.splitlines()) if m]
    wlib = gdstk.read_gds(str(ROOT / 'asic-puzzle-2026' / 'warmup' / '04_final.gds'))
    wtop = wlib.top_level()[0]
    w_index, dup = {}, []
    for r in wtop.references:
        if not r.cell.name.startswith(STD):
            continue
        kind = I.KIND_OF[(int(round(r.rotation / (3.141592653589793 / 2))) % 4,
                          bool(r.x_reflection))]
        gx0, gy0, _, _ = I.apply(chosen[kind],
                                 (I.dbu(r.origin[0], per_um), I.dbu(r.origin[1], per_um)),
                                 m_foot[r.cell.name])
        if (r.cell.name, gx0, gy0) in w_index:
            dup.append((r.cell.name, gx0, gy0))
        w_index[(r.cell.name, gx0, gy0)] = kind
    check('warm-up placements indexed by cell + lower-left corner', len(w_index), 230)
    check('no two warm-up placements collide on that key', dup, [])
    matched = sum(1 for c in defc if (c['cell'], c['xy'][0], c['xy'][1]) in w_index)
    check('every DEF component matches a GDS placement', matched, len(defc))
    token_kind = {}
    for c in defc:
        token_kind.setdefault(c['orient'], set()).add(w_index[(c['cell'], c['xy'][0], c['xy'][1])])
    check('the DEF orientation token -> kind map is a bijection',
          {k: sorted(v) for k, v in sorted(token_kind.items())},
          {'FN': ['rot180_mirror'], 'FS': ['rot000_mirror'], 'N': ['rot000'], 'S': ['rot180']})
    check('the artifact records the same oracle result',
          (d['def_oracle']['matched'], d['def_oracle']['token_consistent']), (230, True))

    # ---- 7. the footprint marker's provenance -----------------------------------
    check('68 of 69 masters carry the footprint marker',
          (len(marker_masters), len(m_bbox)), (68, 69))
    check('the marker inset is unanimous among those masters',
          list(d['footprint_source']['inset_votes_dbu']),
          ['(190, 240, 190, 240)'])
    check('the one master without the marker got a documented fallback',
          d['footprint_source']['fallback_inset_dbu'], [190, 240, 190, 240])

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
        print(f'STEP B1 GATE: FAIL ({n_fail} failing)')
        return 1
    print('STEP B1 GATE: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
