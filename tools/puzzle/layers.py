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
A1_OUT = OUT          # A1's artifact; A2 reads it as its input

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
    """Classify every (layer, datatype) pair in the library by the role it plays.

    Roles are decided from the chip's own structure -- a layer that only ever draws via cuts is a via
    layer, one that carries pin labels is a label layer -- and each decision returns its evidence, so
    the classification can be argued with rather than trusted.
    """
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


# ===================================================================== A2: via pairs
VIA_OUT = ROOT / 'recon' / 'derived' / 'via_pairs.json'
BOOLEAN_PRECISION = 1e-3    # um; GDS DBU is 1 nm so this is safe and fast


def _overlap_area(a, b) -> float:
    """Area shared by two polygons, or 0.0 if they do not overlap.

    Bbox rejection first, then an exact boolean intersection, so "the cut touches the
    metal" is proved by shared area rather than assumed from layer membership.
    """
    (ax0, ay0), (ax1, ay1) = a.bounding_box()
    (bx0, by0), (bx1, by1) = b.bounding_box()
    if ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0:
        return 0.0
    try:
        out = gdstk.boolean(a, b, 'and', precision=BOOLEAN_PRECISION)
    except Exception:
        return 0.0
    return float(sum(p.area() for p in out))


def _contained(cut, metal, tol: float = 1e-3) -> bool:
    """True when the cut's bbox lies inside the metal's bbox -- the via enclosure."""
    (cx0, cy0), (cx1, cy1) = cut.bounding_box()
    (mx0, my0), (mx1, my1) = metal.bounding_box()
    return (mx0 - tol <= cx0 and my0 - tol <= cy0
            and cx1 <= mx1 + tol and cy1 <= my1 + tol)


def prove_master_connectivity(cell, role_of, cut_layers, cond_layers) -> dict:
    """Prove, from geometry, which conductors a via master's cut layer bridges.

    Returns a record with the shared-area evidence for each candidate conductor. A cut
    must share area with at least two distinct conductors to be a via; that is the test,
    not layer-name arithmetic.
    """
    by_layer: dict[tuple[int, int], list] = defaultdict(list)
    for p in cell.polygons:
        by_layer[(p.layer, p.datatype)].append(p)

    cuts = [k for k in by_layer if k in cut_layers]
    conds = [k for k in by_layer if k in cond_layers]
    record = {'name': cell.name, 'cuts': {}, 'conductors': sorted(key_str(c) for c in conds)}

    for cut in cuts:
        detail = {}
        for cond in conds:
            shared = 0.0
            enclosed = False
            for a in by_layer[cut]:
                for b in by_layer[cond]:
                    shared += _overlap_area(a, b)
                    enclosed = enclosed or _contained(a, b)
            detail[key_str(cond)] = {'shared_area_um2': round(shared, 6),
                                     'cut_enclosed_by_metal': enclosed}
        record['cuts'][key_str(cut)] = {
            'connected_to': sorted(k for k, v in detail.items()
                                   if v['shared_area_um2'] > 0.0),
            'cut_area_um2': round(sum(p.area() for p in by_layer[cut]), 6),
            'n_cut_shapes': len(by_layer[cut]),
            'detail': detail,
        }
    return record


def derive_via_pairs(lib, top_name: str, roles: dict) -> dict:
    """A2: the connectivity rule set, derived from the via masters' own geometry."""
    top = next(c for c in lib.cells if c.name == top_name)
    cut_layers = {k for k, r in roles.items() if r == 'via_cut'}
    cond_layers = {k for k, r in roles.items() if r in ('routing', 'local_wire')}
    geometry_layers = {k for k, r in roles.items()
                       if r in ('routing', 'local_wire', 'cell_geometry')}

    placements: Counter = Counter()
    for ref in top.references:
        cols = getattr(ref, 'columns', 1) or 1
        rows = getattr(ref, 'rows', 1) or 1
        placements[ref.cell.name] += cols * rows

    # A via master is a cell whose geometry is *only* conductors and cuts. This is the
    # principled test -- it uses no cell names. Standard cells contain device layers
    # (64/20, 65/20, 66/20), so they are excluded even though they also contain 67/44
    # as a cell-internal contact. Those cells are recorded separately below.
    conductor_or_cut = {k for k, r in roles.items()
                        if r in ('routing', 'local_wire', 'via_cut')}

    via_master_cells, other_cut_cells = [], []
    for cell in lib.cells:
        layers = {(p.layer, p.datatype) for p in cell.polygons}
        if not (layers & cut_layers):
            continue
        (via_master_cells if layers <= conductor_or_cut else other_cut_cells).append(cell)

    masters = []
    for cell in sorted(via_master_cells, key=lambda c: c.name):
        rec = prove_master_connectivity(cell, roles, cut_layers, cond_layers)
        rec['instances'] = placements.get(cell.name, 0)
        for cut_key, d in rec['cuts'].items():
            d['is_well_formed'] = (len(d['connected_to']) == 2
                                   and all(v['shared_area_um2'] > 0.0
                                           for v in d['detail'].values()))
        masters.append(rec)

    # cells that use a cut layer internally without being via masters
    internal: dict[str, dict] = {}
    for cell in other_cut_cells:
        used = sorted(key_str(k) for k in
                      {(p.layer, p.datatype) for p in cell.polygons} & cut_layers)
        e = internal.setdefault('+'.join(used),
                                {'cuts_used': used, 'cells': 0, 'instances': 0})
        e['cells'] += 1
        e['instances'] += placements.get(cell.name, 0)

    # group masters by (cut, frozenset(conductors)) -> the rule table
    rules: dict[tuple, dict] = {}
    for rec in masters:
        for cut_key, d in rec['cuts'].items():
            conds = tuple(d['connected_to'])
            if len(conds) != 2:
                continue
            key = (cut_key, conds)
            r = rules.setdefault(key, {'cut': cut_key, 'connects': list(conds),
                                       'masters': [], 'instances': 0})
            r['masters'].append(rec['name'])
            r['instances'] += rec['instances']

    # conductor graph from the rules
    edges = []
    for key, r in rules.items():
        a, b = (int(s.split('/')[0]) for s in r['connects'])
        edges.append((a, b, r['cut']))
    adj: dict[int, set[int]] = defaultdict(set)
    for a, b, _ in edges:
        adj[a].add(b)
        adj[b].add(a)

    # non-via contact layers: who do they bridge, geometrically?
    non_via_contacts = []
    for k, role in sorted(roles.items()):
        if role != 'contact' or k in cut_layers:
            continue
        partners: Counter = Counter()
        for cell in lib.cells:
            shapes = [p for p in cell.polygons
                      if (p.layer, p.datatype) == k]
            if not shapes:
                continue
            others = [p for p in cell.polygons
                      if (p.layer, p.datatype) != k
                      and (p.layer, p.datatype) in geometry_layers]
            for a in shapes:
                for b in others:
                    if _overlap_area(a, b) > 0.0:
                        partners[key_str((b.layer, b.datatype))] += 1
        non_via_contacts.append({
            'layer': key_str(k),
            'shapes_in_masters': sum(1 for c in lib.cells for p in c.polygons
                                     if (p.layer, p.datatype) == k),
            'partners': {p: n for p, n in sorted(partners.items())},
            'overlap_events': sum(partners.values()),
        })

    return {
        'cut_layers': sorted(key_str(k) for k in cut_layers),
        'conductor_layers': sorted(key_str(k) for k in cond_layers),
        'masters': masters,
        'pairs': [rules[k] for k in sorted(rules)],
        'conductor_graph': {
            'nodes': sorted(key_str(k) for k in cond_layers),
            'adjacency': {str(n): sorted(adj[n]) for n in sorted(adj)},
            'edges': [[a, b, key_str(cut)] for a, b, cut in sorted(edges)],
        },
        'cells_using_cuts_internally': sorted(internal.values(),
                                              key=lambda e: -e['instances']),
        'contact_layers_without_via': non_via_contacts,
        'totals': {
            'via_masters': len(masters),
            'via_instances': sum(m['instances'] for m in masters),
            'internal_cut_cells': sum(e['cells'] for e in internal.values()),
            'internal_cut_instances': sum(e['instances'] for e in internal.values()),
            'by_pair': {f"{r['cut']}::{'+'.join(r['connects'])}": r['instances']
                        for r in rules.values()},
        },
    }


def stage_via_pairs(argv: list[str]) -> int:
    """A2 entry point: write recon/derived/via_pairs.json and report it."""
    lib = gdstk.read_gds(str(GDS))
    top = lib.top_level()[0]
    if not A1_OUT.exists():
        print('missing recon/derived/layers.json -- run `python -m tools.puzzle layers` first',
              file=sys.stderr)
        return 1
    roles = {(r['layer'], r['datatype']): r['role']
             for r in json.loads(A1_OUT.read_text(encoding='utf-8'))['pairs']}
    d = derive_via_pairs(lib, top.name, roles)
    out = {
        'generated_by': 'tools/puzzle/layers.py::stage_via_pairs',
        'source': {'path': 'asic-puzzle-2026/puzzle.gds', 'sha256': sha256(GDS)},
        'input': 'recon/derived/layers.json',
        **d,
    }
    VIA_OUT.parent.mkdir(parents=True, exist_ok=True)
    VIA_OUT.write_text(json.dumps(out, indent=2, sort_keys=False) + '\n',
                       encoding='utf-8', newline='\n')

    print('via masters -- proved from shared polygon area, not layer names:')
    print(f"  {'master':<42}{'cut':<7}{'bridges':<22}{'inst':>6}  shared area (um^2)")
    print('  ' + '-' * 100)
    bad = []
    for m in d['masters']:
        for cut_key, det in sorted(m['cuts'].items()):
            shared = '  '.join(f"{k}={v['shared_area_um2']:.4f}"
                               for k, v in sorted(det['detail'].items()))
            flag = '' if det.get('is_well_formed') else '  <-- NOT WELL FORMED'
            if flag:
                bad.append(m['name'])
            print(f"  {m['name']:<42}{cut_key:<7}"
                  f"{'<->'.join(det['connected_to']):<22}{m['instances']:>6}  {shared}{flag}")
    print(f"  {len(d['masters'])} via masters, all well formed: {not bad}")
    print()
    print('connectivity rules (the rule set B2/B3 will use):')
    for r in d['pairs']:
        print(f"  {r['cut']:<7} bridges {r['connects'][0]:<7} <-> {r['connects'][1]:<7}"
              f"  {len(r['masters']):>2} master(s), {r['instances']:>5} instances")
    print()
    g = d['conductor_graph']
    chain = []
    cur, seen = 67, {67}
    chain.append('67/20')
    while True:
        nxt = [n for n in g['adjacency'].get(str(cur), []) if n not in seen]
        if not nxt:
            break
        cur = nxt[0]
        seen.add(cur)
        chain.append(f'{cur}/20')
    print(f"conductor graph  : {' -- '.join(chain)}  (single path: {len(seen)} nodes)")
    print(f"                   nodes={g['nodes']}")
    print()
    print('cells that use a cut layer internally but are not via masters:')
    for e in d['cells_using_cuts_internally'][:4]:
        print(f"  cuts {e['cuts_used']} in {e['cells']} cells, "
              f"{e['instances']} instances")
    print()
    print('contact layers no via master uses:')
    for c in d['contact_layers_without_via']:
        print(f"  {c['layer']:<8} {c['shapes_in_masters']:>5} shapes; "
              f"overlaps {c['partners']}")
    print()
    t = d['totals']
    print(f"totals: {t['via_masters']} via masters, {t['via_instances']} via instances; "
          f"{t['internal_cut_cells']} cells use a cut internally "
          f"({t['internal_cut_instances']} instances)")
    print(f"written: {VIA_OUT.relative_to(ROOT)}")
    return 0


# --------------------------------------------------------------------- report
def main(argv: list[str]) -> int:
    stage = argv[0] if argv else 'layers'
    rest = argv[1:]
    if stage == 'via-pairs':
        return stage_via_pairs(rest)
    if stage == 'layers':
        return stage_layers(rest)
    print(f'layers.py does not provide stage {stage!r}', file=sys.stderr)
    return 2


def classify_all(lib, top_name: str) -> dict:
    """Classify every (layer, datatype) pair in a library, and report the derived facts.

    Factored out of `stage_layers` so that A4 can calibrate against
    `warmup/04_final.gds` through *this same code path*. If the warm-up used a different
    path, passing there would prove nothing about the puzzle.
    """
    a = analyse(lib, top_name)
    pairs, die, pitch = a['pairs'], a['die_bbox'], a['pitch']

    pin_dt, pin_evidence = derive_pin_datatype(lib, top_name)
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

    return {'records': records, 'analyse': a, 'pin_datatype': pin_dt,
            'pin_evidence': pin_evidence, 'label_layers': label_layers}


def stage_layers(argv: list[str]) -> int:
    """A1: classify every (layer, datatype) into a role, derived from the chip itself.

    Writes `recon/derived/layers.json`; the via-pair derivation (A2) is in the same module and the
    same stage family.
    """
    lib = gdstk.read_gds(str(GDS))
    top = lib.top_level()[0]
    res = classify_all(lib, top.name)
    a, records = res['analyse'], res['records']
    die, pitch = a['die_bbox'], a['pitch']
    pin_dt, pin_evidence = res['pin_datatype'], res['pin_evidence']
    label_layers = res['label_layers']

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
    # newline='\n' on purpose: the default text mode writes CRLF on Windows while git
    # normalises to LF, so a fresh clone would show the regenerated file as modified.
    OUT.write_text(json.dumps(out, indent=2, sort_keys=False) + '\n',
                   encoding='utf-8', newline='\n')

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
