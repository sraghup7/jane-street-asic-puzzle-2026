#!/usr/bin/env python3
"""layers.py -- step A1: classify every (layer, datatype) pair into a role.

The plan's differentiator D1 is that *the chip describes its own layer stack*. Nothing
here reads a tech file, a LEF, or a layer map. Every role below is assigned from
measurable structure in `puzzle.gds` itself:

  - which element kinds a pair carries (BOUNDARY / PATH / TEXT)
  - which kinds of cell it appears in (the top cell, a via master, a standard cell)
  - the shape geometry (sizes, squareness, area)
  - whether the pair's geometry contains that layer's own TEXT labels
  - whether the shapes sit on a lattice of the site pitch, which is itself derived
    from the drawn cell widths rather than assumed

The connectivity-critical roles -- `routing` and `via_cut` -- are provable: they are
decided by membership in the via masters' own layer sets. Roles that are *not*
connectivity-critical are annotated with their evidence and flagged `needs_review`
when the evidence is ambiguous, rather than being overclaimed.

    python -m tools.puzzle layers
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import gdstk

ROOT = Path(__file__).resolve().parents[2]
GDS = ROOT / 'asic-puzzle-2026' / 'puzzle.gds'
OUT = ROOT / 'recon' / 'derived' / 'layers.json'

# Thresholds. Each is a stated round number, not a fitted parameter.
CUT_MAX_DIM = 1.0        # um -- a contact/via cut is at most this across
DEVICE_MIN_AREA = 1.0    # um^2 -- a device layer's shapes are at least this big
SQUARE_TOL = 0.06        # relative |w-h| for "this shape is square"
LATTICE_TOL = 0.006      # um -- absolute slack when testing a pitch multiple
LABEL_DATATYPES = (5, 59)  # datatypes that carry names in this file
PIN_DATATYPE = 16          # datatype that carries pin geometry in this file


# --------------------------------------------------------------------- gds access
def cell_kind(name: str, top_name: str) -> str:
    if name == top_name:
        return 'TOP'
    if 'via' in name.lower():
        return 'VIA'
    if name.lower().startswith('sky130_fd_sc_hd__'):
        return 'STD'
    return 'OTHER'


def path_layers(path):
    """FlexPath carries parallel .layers/.datatypes lists; plain Path carries singles."""
    ls = getattr(path, 'layers', None)
    if ls is None:
        return [(path.layer, path.datatype)]
    return list(zip(ls, getattr(path, 'datatypes', ls)))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def key_str(k: tuple[int, int]) -> str:
    """Canonical string form of a (layer, datatype) pair, for JSON keys and reports."""
    return f'{k[0]}/{k[1]}'


# --------------------------------------------------------------------- evidence
def estimate_site_pitch(lib, top_name: str) -> float:
    """Derive the site pitch from the drawn cell widths instead of assuming 0.46.

    Standard-cell widths in a row-based library are integer multiples of the site
    pitch, so the smallest positive difference between distinct widths is it.
    """
    widths = sorted({round(c.bounding_box()[1][0] - c.bounding_box()[0][0], 4)
                     for c in lib.cells if cell_kind(c.name, top_name) == 'STD'})
    diffs = [round(b - a, 4) for a, b in zip(widths, widths[1:]) if b - a > 1e-6]
    if not diffs:
        return 0.0
    return min(diffs)


def analyse(lib, top_name: str) -> dict:
    top = next(c for c in lib.cells if c.name == top_name)
    die = top.bounding_box()

    pairs = defaultdict(lambda: {
        'elements': Counter(), 'by_kind': Counter(), 'sizes': Counter(),
        'areas': [], 'masters': set(), 'per_master': Counter(),
        'insets': Counter(), 'in_via_masters': set(),
    })

    via_masters = [c for c in lib.cells if cell_kind(c.name, top_name) == 'VIA']
    via_layer_sets = {}
    for c in via_masters:
        seen = defaultdict(int)
        for p in c.polygons:
            seen[(p.layer, p.datatype)] += 1
        for p in c.paths:
            for L, D in path_layers(p):
                seen[(L, D)] += 1
        via_layer_sets[c.name] = dict(sorted(seen.items()))
    via_layers = {k for s in via_layer_sets.values() for k in s}

    # per-pair aggregation
    for c in lib.cells:
        kind = cell_kind(c.name, top_name)
        cb = c.bounding_box()
        for p in c.polygons:
            k = (p.layer, p.datatype)
            e = pairs[k]
            e['elements']['boundary'] += 1
            e['by_kind'][kind] += 1
            (x0, y0), (x1, y1) = p.bounding_box()
            e['sizes'][(round(x1 - x0, 4), round(y1 - y0, 4))] += 1
            e['areas'].append(p.area())
            e['masters'].add(c.name)
            e['per_master'][c.name] += 1
            e['insets'][(round(x0 - cb[0][0], 4), round(y0 - cb[0][1], 4),
                         round(cb[1][0] - x1, 4), round(cb[1][1] - y1, 4))] += 1
        for p in c.paths:
            for L, D in path_layers(p):
                e = pairs[(L, D)]
                e['elements']['path'] += 1
                e['by_kind'][kind] += 1
                e['masters'].add(c.name)
                e['per_master'][c.name] += 1
        for lb in c.labels:
            e = pairs[(lb.layer, lb.texttype)]
            e['elements']['text'] += 1
            e['by_kind'][kind] += 1
            e['masters'].add(c.name)
            e['per_master'][c.name] += 1

    for k in via_layers:
        if k in pairs:
            pairs[k]['in_via_masters'] = {c.name for c in via_masters
                                          if k in via_layer_sets[c.name]}

    # which pairs contain their own layer's TEXT labels?
    label_contained = Counter()
    for c in lib.cells:
        labels = [(lb.layer, lb.texttype, lb.origin) for lb in c.labels]
        polys = [(p.layer, p.datatype, p.bounding_box()) for p in c.polygons]
        for ll, lt, (lx, ly) in labels:
            if lt not in LABEL_DATATYPES:
                continue
            for pl, pd, ((x0, y0), (x1, y1)) in polys:
                if pl == ll and x0 <= lx <= x1 and y0 <= ly <= y1:
                    label_contained[(pl, pd)] += 1

    # which pairs touch a cut layer, per master (bbox approximation, documented)
    small = [k for k, e in pairs.items()
             if e['areas'] and max((max(w, h) for w, h in e['sizes']), default=0.0) <= CUT_MAX_DIM]
    touches = defaultdict(Counter)
    for c in lib.cells:
        boxes = defaultdict(list)
        for p in c.polygons:
            boxes[(p.layer, p.datatype)].append(p.bounding_box())
        for src in small:
            for (ax0, ay0), (ax1, ay1) in boxes.get(src, []):
                for k, blist in boxes.items():
                    if k == src:
                        continue
                    for (bx0, by0), (bx1, by1) in blist:
                        if ax0 <= bx1 and bx0 <= ax1 and ay0 <= by1 and by0 <= ay1:
                            touches[k][src] += 1
                            break

    # instantiation counts from the top cell (via masters are the interesting ones)
    placements: Counter = Counter()
    for ref in top.references:
        cols = getattr(ref, 'columns', 1) or 1
        rows = getattr(ref, 'rows', 1) or 1
        placements[ref.cell.name] += cols * rows

    return {
        'die_bbox': [[round(v, 4) for v in die[0]], [round(v, 4) for v in die[1]]],
        'via_layer_sets': {name: {key_str(k): n for k, n in s.items()}
                           for name, s in via_layer_sets.items()},
        'via_layers': sorted(via_layers),
        'placements': dict(placements),
        'pairs': pairs,
        'label_contained': label_contained,
        'touches': {k: {key_str(s): n for s, n in v.items()} for k, v in touches.items()},
        'pitch': estimate_site_pitch(lib, top_name),
    }


def path_bbox(path):
    """Bounding box of a path, via its polygons (FlexPath has no bounding_box())."""
    try:
        polys = path.to_polygons()
    except Exception:
        return None
    xs, ys = [], []
    for q in polys:
        for x, y in q.points:
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return ((min(xs), min(ys)), (max(xs), max(ys)))


def derive_pin_datatype(lib, top_name: str, label_datatypes=LABEL_DATATYPES) -> tuple[int | None, dict]:
    """Learn which datatype this file uses for pin geometry -- do not assume it.

    In the top cell the ports are labelled. The pin layer is the datatype whose shapes
    stand in a *one-to-one* correspondence with those labels *and* whose shape count
    equals the label count. Two weaker tests are not enough on their own:

      - drawing layers also contain the labels, but carry extra shapes, so they fail the
        count test;
      - the power layers (71/16, 72/16) pass the one-shape-per-label test -- each of the
        two power labels sits in exactly one shape -- but carry 12 and 18 shapes for 2
        labels, so they fail the count test as well.

    Only the count test separates the pin datatype from both.
    """
    top = next(c for c in lib.cells if c.name == top_name)
    labels = [(lb.layer, lb.origin) for lb in top.labels if lb.texttype in label_datatypes]
    by_layer: dict[str, int] = {}
    for ll, _ in labels:
        by_layer[str(ll)] = by_layer.get(str(ll), 0) + 1
    evidence = {'top_labels': len(labels), 'labels_by_layer': by_layer, 'candidates': {}}
    if not labels:
        return None, evidence

    polys = defaultdict(list)
    for p in top.polygons:
        polys[(p.layer, p.datatype)].append(p.bounding_box())
    for p in top.paths:
        for L, D in path_layers(p):
            bb = path_bbox(p)
            if bb is not None:
                polys[(L, D)].append(bb)

    best = None
    for (L, D), boxes in sorted(polys.items()):
        if not any(ll == L for ll, _ in labels):
            continue
        same = [(ll, (x, y)) for ll, (x, y) in labels if ll == L]
        hits = [sum(1 for ((x0, y0), (x1, y1)) in boxes if x0 <= x <= x1 and y0 <= y <= y1)
                for _, (x, y) in same]
        one_each = bool(hits) and all(h == 1 for h in hits)
        evidence['candidates'][key_str((L, D))] = {
            'shapes': len(boxes), 'labels': len(same), 'one_shape_per_label': one_each}
        if one_each and len(boxes) == len(same):
            if best is None or len(boxes) < evidence['candidates'][key_str(best)]['shapes']:
                best = (L, D)

    if best is not None:
        evidence['chosen'] = key_str(best)
        evidence['datatype'] = best[1]
    return (best[1] if best else None), evidence


def classify(k, e, ctx) -> tuple[str, str, str, list[str]]:
    """Return (role, rule, confidence, notes).

    Rules are tried in order. The connectivity-critical roles (`via_cut`, `routing`,
    `local_wire`) are provable: they follow from the via masters' own layer sets.
    """
    notes: list[str] = []
    n_bound = e['elements']['boundary']
    n_path = e['elements']['path']
    n_text = e['elements']['text']
    geom = n_bound + n_path
    sizes = e['sizes']
    widths = sorted({w for w, h in sizes})
    heights = sorted({h for w, h in sizes})
    max_dim = max((max(w, h) for w, h in sizes), default=0.0)
    max_area = max(e['areas']) if e['areas'] else 0.0
    square_fraction = (e['_square_fraction'] if sizes else 0.0)
    per_master = sorted(set(e['per_master'].values())) or [0]
    in_via = bool(e['in_via_masters'])
    top_used = e['by_kind']['TOP'] > 0
    in_std = e['by_kind']['STD'] > 0

    # R1 -- carries names only
    if n_text and not geom:
        return 'label', 'R1', 'high', notes

    # R2 -- the file's pin-geometry datatype, on a layer number that carries labels
    if k[1] == ctx['pin_datatype'] and k[0] in ctx['label_layers']:
        return 'pin', 'R2', 'high', [
            f'datatype {k[1]} is the pin datatype (derived from the top-cell ports); '
            f'layer {k[0]} carries labels']

    # R3 -- a cut layer that a via master actually uses
    if in_via and square_fraction == 1.0 and 0 < max_dim <= CUT_MAX_DIM:
        return 'via_cut', 'R3', 'high', [
            f"used by {len(e['in_via_masters'])} via master(s): "
            f"{', '.join(sorted(e['in_via_masters']))}"]

    # R4/R5 -- a via master's non-cut layer is a conductor
    if in_via and geom:
        if top_used:
            return 'routing', 'R4', 'high', [
                f'bridged by a via master; used in the top cell '
                f"({e['by_kind']['TOP']} shapes/paths)"]
        return 'local_wire', 'R5', 'high', [
            'bridged by a via master; confined to the cell masters']

    # R6 -- cut-shaped, but no via master uses it
    if square_fraction == 1.0 and 0 < max_dim <= CUT_MAX_DIM and in_std:
        return 'contact', 'R6', 'medium', [
            'cut-shaped but no via master contains it']

    # R7 -- footprint-pinned markup: present ~once per master, pinned to the cell box
    if max(per_master) <= 2 and sizes:
        insets = e['insets']
        if len(insets) == 1:
            only = next(iter(insets))
            if all(v > 0 for v in only):
                return 'non_electrical', 'R7a', 'high', [
                    f'present at most twice per master; every shape inset from the '
                    f'master bbox by the same {only}', 'subkind=footprint_marker']
        if len(heights) == 1 and ctx['pitch'] > 0:
            p = ctx['pitch']
            snapped = all(abs(round(w / p) * p - w) <= LATTICE_TOL and round(w / p) >= 1
                          for w in widths)
            if snapped and abs(widths[0] - p) <= LATTICE_TOL:
                return 'non_electrical', 'R7b', 'high', [
                    f'widths snap to the {p:.2f} um site pitch, height constant at '
                    f'{heights[0]} um', 'subkind=footprint_marker']

    # R8 -- other geometry belonging to the cells (masters only; the top cell's own
    # decorative geometry and anything outside the die fall through to R9)
    if in_std:
        sub = ('contact_sized' if max_dim <= CUT_MAX_DIM
               else 'footprint_pinned' if max(per_master) <= 2
               else 'in_master_geometry')
        touched = sorted(e.get('_touches', {}))
        if touched:
            notes.append(f"shares area with cut layers in-master: {touched}")
        notes.append(f'subkind={sub}')
        conf = 'high' if sub == 'in_master_geometry' else 'medium'
        return 'cell_geometry', 'R8', conf, notes

    # R9 -- anything else
    sub = 'unclassified'
    diew = ctx['die'][1][0] - ctx['die'][0][0]
    dieh = ctx['die'][1][1] - ctx['die'][0][1]
    if sizes and max_area >= 0.5 * diew * dieh:
        sub = 'die_outline'
    elif e['by_kind']['OTHER'] and not e['by_kind']['STD']:
        sub = 'outside_die'
    notes.append(f'subkind={sub}')
    return 'non_electrical', 'R9', 'high' if sub != 'unclassified' else 'low', notes


# --------------------------------------------------------------------- report
def main(argv: list[str]) -> int:
    lib = gdstk.read_gds(str(GDS))
    top = lib.top_level()[0]
    a = analyse(lib, top.name)
    pairs, die, pitch = a['pairs'], a['die_bbox'], a['pitch']

    pin_dt, pin_evidence = derive_pin_datatype(lib, top.name)
    label_layers = sorted({k[0] for k, e in pairs.items()
                           if e['elements']['text']
                           and not (e['elements']['boundary'] + e['elements']['path'])})
    ctx = {'pin_datatype': pin_dt, 'label_layers': set(label_layers),
           'pitch': pitch, 'die': die}

    # attach derived evidence before classification
    for k, e in pairs.items():
        e['_touches'] = dict(a['touches'].get(k, {}))
        sizes = e['sizes']
        e['_square_fraction'] = (
            sum(1 for w, h in sizes if abs(w - h) <= SQUARE_TOL * max(w, h)) / len(sizes)
            if sizes else 0.0)

    records = []
    for k in sorted(pairs):
        e = pairs[k]
        role, rule, conf, notes = classify(k, e, ctx)
        sizes = e['sizes']
        records.append({
            'layer': k[0], 'datatype': k[1],
            'elements': dict(e['elements']),
            'by_cell_kind': {kk: e['by_kind'][kk] for kk in ('VIA', 'STD', 'TOP', 'OTHER')},
            'geometry': {
                'shapes': sum(sizes.values()),
                'distinct_sizes': len(sizes),
                'square_fraction': round(
                    (sum(1 for w, h in sizes if abs(w - h) <= SQUARE_TOL * max(w, h))
                     / len(sizes)) if sizes else 0.0, 4),
                'min_dim': round(min((min(w, h) for w, h in sizes), default=0.0), 4),
                'max_dim': round(max((max(w, h) for w, h in sizes), default=0.0), 4),
                'median_area': round(sorted(e['areas'])[len(e['areas']) // 2], 4)
                if e['areas'] else 0.0,
                'max_area': round(max(e['areas']), 4) if e['areas'] else 0.0,
                'distinct_insets': len(e['insets']),
                'max_shapes_per_master': max(e['per_master'].values()) if e['per_master'] else 0,
                'masters_containing': len(e['masters']),
            },
            'in_via_masters': sorted(e['in_via_masters']),
            'labels_contained': a['label_contained'].get(k, 0),
            'role': role, 'rule': rule, 'confidence': conf,
            'needs_review': conf != 'high',
            'notes': notes,
        })

    roles = defaultdict(list)
    for r in records:
        roles[r['role']].append([r['layer'], r['datatype']])

    out = {
        'generated_by': 'tools/puzzle/layers.py',
        'source': {'path': 'asic-puzzle-2026/puzzle.gds', 'sha256': sha256(GDS)},
        'cells': {
            'total': len(lib.cells), 'top': top.name,
            'via_masters': sorted(c.name for c in lib.cells
                                  if cell_kind(c.name, top.name) == 'VIA'),
            'std_masters': sum(1 for c in lib.cells
                               if cell_kind(c.name, top.name) == 'STD'),
            'other_masters': sorted(c.name for c in lib.cells
                                    if cell_kind(c.name, top.name) == 'OTHER'),
        },
        'die_bbox_um': die,
        'derived_site_pitch_um': pitch,
        'derived_pin_datatype': {'datatype': pin_dt, 'evidence': pin_evidence},
        'derived_label_layers': label_layers,
        'pairs': records,
        'roles': {k: sorted(v) for k, v in sorted(roles.items())},
        'via_layer_sets': a['via_layer_sets'],
        'via_placements': {name: a['placements'].get(name, 0)
                           for name in sorted(a['via_layer_sets'])},
        'summary': {
            'pairs': len(records),
            'by_role': {k: len(v) for k, v in sorted(roles.items())},
            'needs_review': [[r['layer'], r['datatype']] for r in records if r['needs_review']],
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, sort_keys=False) + '\n', encoding='utf-8')

    # ---- console report
    print(f'source            : {out["source"]["path"]}')
    print(f'cells             : {out["cells"]["total"]} '
          f'(top={top.name}, via={len(out["cells"]["via_masters"])}, '
          f'std={out["cells"]["std_masters"]}, other={len(out["cells"]["other_masters"])})')
    print(f'derived site pitch: {pitch} um')
    print(f'pairs classified  : {len(records)}')
    print()
    hdr = (f"{'layer/dt':<10}{'role':<18}{'rule':<6}{'conf':<8}{'VIA':>5}{'STD':>6}"
           f"{'TOP':>6}{'OTHER':>6}{'shapes':>7}  notes")
    print(hdr)
    print('-' * (len(hdr) + 20))
    for r in records:
        b = r['by_cell_kind']
        note = r['notes'][0] if r['notes'] else ''
        key = f"{r['layer']}/{r['datatype']}"
        print(f'{key:<10}{r["role"]:<18}{r["rule"]:<6}'
              f'{r["confidence"]:<8}{b["VIA"]:>5}{b["STD"]:>6}{b["TOP"]:>6}{b["OTHER"]:>6}'
              f'{r["geometry"]["shapes"]:>7}  {note[:56]}')
    print()
    print('roles:')
    for role, lst in out['roles'].items():
        print(f'  {role:<18} {len(lst):>2}  {["%d/%d" % (l, d) for l, d in lst]}')
    review = out['summary']['needs_review']
    print()
    print(f'needs review: {len(review)} pairs -> {["%d/%d" % (l, d) for l, d in review]}')
    print(f'written: {OUT.relative_to(ROOT)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
