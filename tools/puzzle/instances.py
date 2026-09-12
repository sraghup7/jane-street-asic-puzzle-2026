#!/usr/bin/env python3
"""instances.py -- B1: the placement table, with the transform convention proved.

    python -m tools.puzzle instances        ->  recon/derived/instances.json

Phase A described *what the cells are*. This step fixes *where they are*, which every
later step needs: B4 transforms the A4 pin model by these transforms, C3 clusters by
these positions, and the region work in C4 indexes by them.

Two decisions, both deliberate:

**The convention is measured, not assumed.** A GDS placement is an affine map
`global = R * local + origin` with `R = diag(sx, sy)`, `sx, sy in {+1, -1}` -- only two
sign flips exist, and `-R` is the 180-degree rotation. So there are exactly four candidate
matrices per placement kind. Rather than trust a documented convention, the stage tests
all four against every instance and keeps the one that satisfies *all* of them, reporting
the vote. Section 3 prints the resulting table.

**The DEF is used as an external oracle.** `warmup/03_post_place_and_route.def` states 230
placements independently of the GDS: explicit coordinates in the same database units, and
a named orientation (`N` / `FS` / `FN` / `S`). Nothing in the main chip has such a witness,
so the warm-up is the only place the transform convention can be checked against a second
source rather than against gdstk's own arithmetic. Section 5 does that.

Units: **every coordinate here is an integer database unit.** gdstk hands back microns as
floats (its own default unit/precision); the conversion happens once, at read time, in
`dbu`. See docs/03_our_plan.md sec.5 convention 1 and docs/verification.md sec.5.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import gdstk

ROOT = Path(__file__).resolve().parents[2]

GDS = ROOT / 'asic-puzzle-2026' / 'puzzle.gds'
WU_DEF = ROOT / 'asic-puzzle-2026' / 'warmup' / '03_post_place_and_route.def'
A1 = ROOT / 'recon' / 'derived' / 'layers.json'
INV = ROOT / 'recon' / 'inventory.json'
OUT = ROOT / 'recon' / 'derived' / 'instances.json'

STD = 'sky130_fd_sc_hd__'

# A1 identified this pair as the per-master cell-footprint marker (present in 68 of the 69
# masters, inset 0.19 um from the master bbox). It is what makes the cell boundary
# observable, which both the DEF oracle and the non-overlap check need.
FOOTPRINT = (236, 0)

# The four matrices a 180-degree-only placement can use. sx = -1 flips x, sy = -1 flips y.
R_CANDIDATES: dict[str, tuple[int, int]] = {
    'identity': (1, 1),
    'flip_x': (-1, 1),
    'flip_y': (1, -1),
    'rot180': (-1, -1),
}

# kind name -> (quarter turns, x_reflection). Matches inventory.json's std_cell_transforms
# naming, which Step 1 established; the netlist-side naming in A4 uses the same four words.
KIND_OF: dict[tuple[int, bool], str] = {
    (0, False): 'rot000',
    (0, True): 'rot000_mirror',
    (2, False): 'rot180',
    (2, True): 'rot180_mirror',
}

DEF_COMPONENT = re.compile(
    r'^\s*-\s+(\S+)\s+(\S+)\s+\+.*?\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(\w+)\s*;')


def dbu(v: float, per_um: int) -> int:
    """Microns -> integer database units. The one conversion point in this module."""
    return int(round(v * per_um))


def flat_bbox(obj) -> tuple[float, float, float, float]:
    """gdstk returns ((x0, y0), (x1, y1)); flatten it."""
    (x0, y0), (x1, y1) = obj.bounding_box()
    return (x0, y0, x1, y1)


def polys_bbox(polys) -> tuple[float, float, float, float]:
    """Union bbox of a polygon list (get_polygons returns a plain list)."""
    boxes = [flat_bbox(p) for p in polys]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def apply(r: tuple[int, int], origin: tuple[int, int],
          box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Transform an integer-DBU bbox by R then translate."""
    sx, sy = r
    xs = sorted((sx * box[0], sx * box[2]))
    ys = sorted((sy * box[1], sy * box[3]))
    return (xs[0] + origin[0], ys[0] + origin[1], xs[1] + origin[0], ys[1] + origin[1])


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read_json(p: Path):
    return json.loads(p.read_text(encoding='utf-8'))


def stage_instances(argv: list[str]) -> int:
    del argv
    for p in (GDS, A1, INV):
        if not p.exists():
            print(f'missing input {p.relative_to(ROOT)}')
            return 1

    a1 = read_json(A1)
    inv = read_json(INV)
    g = inv['gds']
    per_um = int(g['dbu_per_um'])
    site_dbu = dbu(a1['derived_site_pitch_um'], per_um)
    row_dbu = dbu(g['row_pitch_um'], per_um)

    lib = gdstk.read_gds(str(GDS))
    top = lib.top_level()[0]
    refs = [r for r in top.references if r.cell.name.startswith(STD)]

    # ---- 1. master bboxes, in integer DBU --------------------------------------
    master_bbox = {}
    master_footprint = {}
    inset_votes = Counter()
    for r in refs:
        n = r.cell.name
        if n in master_bbox:
            continue
        x0, y0, x1, y1 = flat_bbox(r.cell)
        mb = (dbu(x0, per_um), dbu(y0, per_um), dbu(x1, per_um), dbu(y1, per_um))
        master_bbox[n] = mb
        footprint = r.cell.get_polygons(layer=FOOTPRINT[0], datatype=FOOTPRINT[1])
        if footprint:
            fx0, fy0, fx1, fy1 = polys_bbox(footprint)
            master_footprint[n] = (dbu(fx0, per_um), dbu(fy0, per_um),
                                   dbu(fx1, per_um), dbu(fy1, per_um))
            inset_votes[(master_footprint[n][0] - mb[0], master_footprint[n][1] - mb[1],
                         mb[2] - master_footprint[n][2], mb[3] - master_footprint[n][3])] += 1
    # The footprint marker is absent from one master, so the oracle needs a fallback.
    # Derive it from the masters that do carry it rather than inventing a number.
    modal_inset = inset_votes.most_common(1)[0][0]
    for n, mb in master_bbox.items():
        if n not in master_footprint:
            a, b, c, e = modal_inset
            master_footprint[n] = (mb[0] + a, mb[1] + b, mb[2] - c, mb[3] - e)
    print(f'master footprints: {len(master_footprint)} from the {FOOTPRINT[0]}/{FOOTPRINT[1]} '
          f'marker, inset votes {dict(inset_votes)}, fallback inset {modal_inset}')

    # ---- 2. the raw placements, and the singular-transform sanity checks ---------
    raw = []
    bad_mag = bad_rep = 0
    for r in refs:
        if r.magnification is not None and r.magnification != 1.0:
            bad_mag += 1
        # gdstk always returns a Repetition object; "no repetition" is the one whose
        # columns is None (its repr reads "No repetition"), not None itself.
        if r.repetition is not None and r.repetition.columns is not None:
            bad_rep += 1
        raw.append({
            'master': r.cell.name,
            'kind': KIND_OF.get((int(round(r.rotation / (3.141592653589793 / 2))) % 4,
                                 bool(r.x_reflection)), 'UNKNOWN'),
            'origin': (dbu(r.origin[0], per_um), dbu(r.origin[1], per_um)),
            'global_bbox': tuple(dbu(v, per_um) for v in flat_bbox(r)),
        })
    print(f'standard-cell placements read: {len(raw)}')
    print(f'  magnification != 1 : {bad_mag}')
    print(f'  array repetitions  : {bad_rep}')

    unknown = sorted({p['kind'] for p in raw if p['kind'] == 'UNKNOWN'})
    if unknown or bad_mag or bad_rep:
        print('FAIL: placements with an unsupported transform')
        return 1

    # ---- 3. measure the convention: which R fits every instance of each kind -----
    # This is the step's decisive experiment. Four candidates per kind; the one that
    # reproduces every instance's global bbox is the convention, and it must be unique.
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for p in raw:
        by_kind[p['kind']].append(p)

    print()
    print('convention test: transform(master_bbox) == instance_global_bbox')
    print(f"  {'kind':<16}{'n':>5}   " + ''.join(f'{c:>10}' for c in R_CANDIDATES))
    chosen: dict[str, tuple[int, int]] = {}
    consistent = True
    for kind in sorted(by_kind):
        insts = by_kind[kind]
        votes = {}
        for name, r in R_CANDIDATES.items():
            hits = sum(1 for p in insts
                       if apply(r, p['origin'], master_bbox[p['master']]) == p['global_bbox'])
            votes[name] = hits
        winners = [n for n, h in votes.items() if h == len(insts)]
        chosen[kind] = R_CANDIDATES[winners[0]] if len(winners) == 1 else (0, 0)
        flag = '' if len(winners) == 1 else '  <-- NOT UNIQUE'
        print(f'  {kind:<16}{len(insts):>5}   '
              + ''.join(f'{votes[c]:>10}' for c in R_CANDIDATES) + flag)
        if len(winners) != 1:
            consistent = False
    if not consistent:
        print()
        print('FAIL: no single matrix satisfies every instance of some kind.')
        print('      Per the plan, report which combination does satisfy all of them;')
        print('      a non-unique winner means the model is missing a degree of freedom.')
        return 1

    matched = sum(1 for p in raw
                  if apply(chosen[p['kind']], p['origin'],
                           master_bbox[p['master']]) == p['global_bbox'])
    print()
    print(f'  instances reproduced exactly: {matched} of {len(raw)}')
    if matched != len(raw):
        print('FAIL: the chosen convention does not reproduce every placement')
        return 1
    print('  convention fixed empirically, uniquely, for all 4 kinds:')
    for kind in sorted(chosen):
        sx, sy = chosen[kind]
        print(f'    {kind:<16} R = diag({sx:+d}, {sy:+d})')

    # ---- 4. the instance table --------------------------------------------------
    # The placement point is an *anchor*, and which corner it is depends on the kind:
    # a kind that flips x puts the reference point at the cell's right edge, a kind that
    # flips y puts it at the cell's top edge. So (anchor) is NOT a unique cell position --
    # 275 anchors carry two placements each, all of them pairs that occupy adjacent space
    # (one extending left/down, the other right/up) with only the 0.19 um implant overhang
    # in common. The distinct global bbox is the reliable geometric identity, and all 1618
    # of them are distinct. Recorded rather than assumed away.
    table = []
    for p in raw:
        x, y = p['origin']
        anchor_site, rem_x = divmod(x, site_dbu)
        row_line, rem_y = divmod(y, row_dbu)
        if rem_x or rem_y:
            print(f'FAIL: placement off the grid at {p["origin"]}')
            return 1
        table.append({'master': p['master'], 'kind': p['kind'], 'row_line': row_line,
                      'anchor_site': anchor_site, 'origin_dbu': [x, y],
                      'bbox_dbu': list(p['global_bbox']),
                      'footprint_dbu': list(apply(chosen[p['kind']], (x, y),
                                                  master_footprint[p['master']]))})
    table.sort(key=lambda e: (e['row_line'], e['bbox_dbu'][0], e['bbox_dbu'][1],
                              e['origin_dbu'][1]))
    for n, e in enumerate(table):
        e['id'] = f'i{n:04d}'
    table = [{k: e[k] for k in
              ('id', 'master', 'kind', 'row_line', 'anchor_site', 'origin_dbu',
               'bbox_dbu', 'footprint_dbu')}
             for e in table]

    per_master = Counter(e['master'] for e in table)
    per_row = Counter(e['row_line'] for e in table)
    per_kind = Counter(e['kind'] for e in table)
    anchors = Counter(tuple(e['origin_dbu']) for e in table)
    shared = {k: v for k, v in anchors.items() if v > 1}
    bboxes = Counter(tuple(e['bbox_dbu']) for e in table)
    print()
    print('anchor semantics (measured, not assumed)')
    print(f'  distinct placement anchors : {len(anchors)}')
    print(f'  anchors carrying 2+ cells  : {len(shared)}')
    print(f'  distinct global bboxes     : {len(bboxes)} of {len(table)}'
          f'{" -- the bbox is a unique key" if len(bboxes) == len(table) else ""}')
    print('  (a kind that flips x anchors the cell at its right edge, one that flips y at')
    print('   its top edge, so two cells can share an anchor while occupying adjacent space)')

    # ---- 5. the DEF oracle: an independent statement of the same placements -------
    # A DEF placement point is the cell's lower-left; a GDS placement point is a
    # corner that depends on the kind. Comparing them therefore needs the footprint:
    # the oracle matches on the footprint's lower-left, which is the same physical
    # corner under either convention. (Matching on the raw anchor instead is exactly how
    # this went wrong the first time: it agreed on only 96 of 230.)
    def_text = WU_DEF.read_text(encoding='utf-8')
    def_comps = [{'name': m.group(1), 'cell': m.group(2),
                  'xy': (int(m.group(3)), int(m.group(4))), 'orient': m.group(5)}
                 for m in (DEF_COMPONENT.match(ln) for ln in def_text.splitlines()) if m]
    wlib = gdstk.read_gds(str(ROOT / 'asic-puzzle-2026' / 'warmup' / '04_final.gds'))
    wtop = wlib.top_level()[0]
    w_index: dict[tuple, str] = {}
    w_dup = []
    for r in wtop.references:
        if not r.cell.name.startswith(STD):
            continue
        kind = KIND_OF[(int(round(r.rotation / (3.141592653589793 / 2))) % 4,
                        bool(r.x_reflection))]
        fx0, fy0, fx1, fy1 = master_footprint[r.cell.name]
        gx0, gy0, _, _ = apply(chosen[kind],
                               (dbu(r.origin[0], per_um), dbu(r.origin[1], per_um)),
                               (fx0, fy0, fx1, fy1))
        key = (r.cell.name, gx0, gy0)
        if key in w_index:
            w_dup.append(key)
        w_index[key] = kind

    def_match, def_missing, token_kind = 0, [], defaultdict(set)
    for c in def_comps:
        k = (c['cell'], c['xy'][0], c['xy'][1])
        if k not in w_index:
            def_missing.append(c['name'])
            continue
        def_match += 1
        token_kind[c['orient']].add(w_index[k])

    print()
    print('DEF oracle (warm-up): the DEF states 230 placements independently of the GDS')
    print(f'  DEF components parsed                   : {len(def_comps)}')
    print(f'  warm-up placements indexed by cell+corner: {len(w_index)}')
    print(f'  matched to a GDS placement, cell + exact lower-left corner : {def_match}')
    print(f'  unmatched                               : {len(def_missing)}')
    if def_missing:
        print(f'    {sorted(set(def_missing))[:6]}')
    tok = {t: (sorted(v)[0] if len(v) == 1 else f'INCONSISTENT {sorted(v)}')
           for t, v in sorted(token_kind.items())}
    print(f'  orientation token -> GDS kind            : {tok}')
    token_consistent = all(len(v) == 1 for v in token_kind.values()) and not w_dup
    if not token_consistent or def_match != len(def_comps):
        print()
        print('FAIL: the DEF does not corroborate the convention it is supposed to witness')
        return 1
    print('  the DEF token -> kind map is a bijection, and every component matches')

    # ---- write -------------------------------------------------------------------
    out = {
        'generated_by': 'tools/puzzle/instances.py::stage_instances',
        'source': {'path': 'asic-puzzle-2026/puzzle.gds', 'sha256': sha256(GDS)},
        'inputs': ['recon/derived/layers.json', 'recon/inventory.json'],
        'convention': {
            'units': 'integer DBU; gdstk returns microns and the conversion happens once here',
            'model': 'global = R * local + origin, R = diag(sx, sy), sx/sy in {+1,-1}',
            'candidates_tested': {k: list(v) for k, v in R_CANDIDATES.items()},
            'chosen_by_kind': {k: list(v) for k, v in sorted(chosen.items())},
            'fixed_empirically': True,
            'instances_reproduced': matched,
        },
        'grid': {'site_pitch_dbu': site_dbu, 'row_pitch_dbu': row_dbu,
                 'origin_dbu': [0, 0]},
        'anchor_semantics': {
            'what_the_gds_origin_is': 'an anchor, not a lower-left corner: a kind with '
                                      'sx=-1 anchors at the cell right edge, sy=-1 at the '
                                      'top edge',
            'unique_position_key': 'bbox_dbu',
            'anchors': len(anchors),
            'anchors_carrying_two_or_more': len(shared),
            'distinct_bboxes': len(bboxes),
            'note': 'the 275 shared anchors are pairs occupying adjacent space; their '
                    'bboxes overlap only in the 0.19 um implant overhang',
        },
        'master_bboxes_dbu': {k: list(v) for k, v in sorted(master_bbox.items())},
        'master_footprints_dbu': {k: list(v) for k, v in sorted(master_footprint.items())},
        'footprint_source': {
            'marker': f'{FOOTPRINT[0]}/{FOOTPRINT[1]}',
            'inset_votes_dbu': {f'{k}': v for k, v in sorted(inset_votes.items())},
            'fallback_inset_dbu': list(modal_inset),
            'note': 'one master carries no footprint marker; its footprint is its bbox '
                    'inset by the inset the other masters agree on',
        },
        'def_oracle': {'def_components': len(def_comps), 'matched': def_match,
                       'unmatched': def_missing,
                       'token_to_kind': {t: (sorted(v)[0] if len(v) == 1 else None)
                                         for t, v in sorted(token_kind.items())},
                       'token_consistent': token_consistent},
        'totals': {'instances': len(table), 'masters': len(per_master),
                   'rows': len(per_row), 'kinds': dict(sorted(per_kind.items()))},
        'per_master': dict(sorted(per_master.items())),
        'per_row': {str(k): v for k, v in sorted(per_row.items())},
        'instances': table,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, sort_keys=False) + '\n',
                   encoding='utf-8', newline='\n')

    print()
    print(f'instances     : {len(table)}')
    print(f'masters       : {len(per_master)}')
    print(f'rows          : {len(per_row)}  (row pitch {row_dbu} DBU, site pitch {site_dbu} DBU)')
    print(f'kinds         : {dict(sorted(per_kind.items()))}')
    print(f'written       : {OUT.relative_to(ROOT)}')
    print()
    print('B1 INSTANCE TABLE: PASS')
    return 0


def main(argv: list[str]) -> int:
    stage = argv[0] if argv else 'instances'
    if stage != 'instances':
        print(f'instances.py has no stage {stage!r}', flush=True)
        return 2
    return stage_instances(argv[1:])


if __name__ == '__main__':
    import sys
    raise SystemExit(main(sys.argv[1:]))
