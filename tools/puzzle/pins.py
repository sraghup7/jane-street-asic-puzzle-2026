#!/usr/bin/env python3
"""pins.py -- steps A3 (pin names), A4 (pin geometry) and A5 (coverage).

Differentiator D2: pin names and pin shapes come from the cell masters *inside*
`puzzle.gds`, never from a PDK macro LEF. A3 reads the names; A4 reads the shapes.

    python -m tools.puzzle pin-names
    python -m tools.puzzle pin-geom
    python -m tools.puzzle pin-coverage
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import gdstk

ROOT = Path(__file__).resolve().parents[2]
GDS = ROOT / 'asic-puzzle-2026' / 'puzzle.gds'
A1_OUT = ROOT / 'recon' / 'derived' / 'layers.json'
NAMES_OUT = ROOT / 'recon' / 'derived' / 'pin_names.json'

# The 13 top-level ports, from Step 1's independent reading of the waveform and labels.
EXPECTED_PORTS = {'clk', 'rst_n', 'enable', 'I', 'success'} | {f'O[{i}]' for i in range(8)}
BODY_TIE_PINS = {'VPB', 'VNB'}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def cell_kind(name: str, top_name: str) -> str:
    if name == top_name:
        return 'TOP'
    if 'via' in name.lower():
        return 'VIA'
    if name.lower().startswith('sky130_fd_sc_hd__'):
        return 'STD'
    return 'OTHER'


def cell_type(master: str) -> str:
    """`sky130_fd_sc_hd__nand2_2` -> `nand2` (the library's own name for the function)."""
    tail = master.split('__')[-1]
    m = re.match(r'^(.*?)_(\d+)$', tail)
    return m.group(1) if m else tail


def derive_pin_label_layers(a1: dict) -> dict:
    """A label layer annotates its layer number. So a label pair is a *pin*-label layer
    for a given scope iff its layer number carries geometry in that scope.

    This is what excludes `83/44`, which carries the cell's own name: layer 83 has no
    geometry anywhere in the file. Note the test is per scope, which matters -- the
    master power labels on `68/5` are not top-level ports, and the port labels on `70/5`
    annotate a layer that only exists in the top cell.
    """
    geom_layers = {'STD': set(), 'TOP': set()}
    label_pairs = []
    for r in a1['pairs']:
        if r['role'] == 'label':
            label_pairs.append((r['layer'], r['datatype']))
            continue
        for scope in ('STD', 'TOP'):
            if r['by_cell_kind'][scope] > 0:
                geom_layers[scope].add(r['layer'])

    out = {'master': [], 'top': [], 'excluded': {}}
    for (L, D) in sorted(label_pairs):
        in_master = L in geom_layers['STD']
        in_top = L in geom_layers['TOP']
        if in_master:
            out['master'].append(f'{L}/{D}')
        if in_top:
            out['top'].append(f'{L}/{D}')
        if not (in_master or in_top):
            out['excluded'][f'{L}/{D}'] = (
                f'layer {L} carries no geometry in any scope -> carries names, not pins')
    out['geometry_layers'] = {'master': sorted(geom_layers['STD']),
                              'top': sorted(geom_layers['TOP'])}
    return out


def extract_pins(cell, pairs) -> dict:
    pins: dict[str, dict] = {}
    for lb in cell.labels:
        if (lb.layer, lb.texttype) not in pairs:
            continue
        p = pins.setdefault(lb.text, {'labels': 0, 'on_layers': set(), 'positions_um': []})
        p['labels'] += 1
        p['on_layers'].add(f'{lb.layer}/{lb.texttype}')
        p['positions_um'].append([round(lb.origin[0], 4), round(lb.origin[1], 4)])
    return {k: {**v, 'on_layers': sorted(v['on_layers'])}
            for k, v in sorted(pins.items())}


def consistency_checks(masters, top_ports, a1, label_counts, derived) -> list[dict]:
    """Cross-checks that each have two *independently derived* sides.

    One side is read from the labels inside the cell; the other from the library's own
    name for the cell's function. They never share an input, so agreement is evidence
    that the pin-name extraction is right.
    """
    def with_pin(pin):
        return sorted(m for m, d in masters.items() if pin in d['pins'])

    def of_type(pred):
        return sorted(m for m, d in masters.items() if pred(d['type']))

    checks: list[dict] = []

    def eq(label, from_labels, from_names):
        checks.append({'check': label, 'from_labels': from_labels,
                       'from_names': from_names, 'passed': from_labels == from_names})

    eq('a CLK label appears exactly on the flip-flop cells',
       with_pin('CLK'), of_type(lambda t: t.startswith('df')))
    eq('a RESET_B label appears exactly on the dfr* cells',
       with_pin('RESET_B'), of_type(lambda t: t.startswith('dfr')))
    eq('a SET_B label appears exactly on the dfs* cells',
       with_pin('SET_B'), of_type(lambda t: t.startswith('dfs')))
    eq('a select pin S appears exactly on the mux* cells',
       with_pin('S'), of_type(lambda t: t.startswith('mux')))

    # X and Y are the two output-pin conventions and must never coexist in one master;
    # the flip-flops use Q instead, which is itself a check worth making.
    both = sorted(m for m, d in masters.items()
                  if 'X' in d['pins'] and 'Y' in d['pins'])
    checks.append({'check': 'no master has both an X and a Y pin',
                   'from_labels': both, 'from_names': [], 'passed': both == []})
    eq('a Q output appears exactly on the flip-flop cells',
       with_pin('Q'), of_type(lambda t: t.startswith('df')))
    q_and_out = sorted(m for m, d in masters.items()
                       if 'Q' in d['pins'] and ({'X', 'Y'} & set(d['pins'])))
    checks.append({'check': 'no master has both Q and an X/Y output',
                   'from_labels': q_and_out, 'from_names': [], 'passed': q_and_out == []})
    # Only the physical cells have no output of any kind.
    no_output = sorted(m for m, d in masters.items()
                       if not ({'X', 'Y', 'Q', 'HI', 'LO', 'DIODE'} & set(d['pins'])))
    checks.append({'check': 'only the physical cells (tap, decap) lack an output pin',
                   'from_labels': sorted({masters[m]['type'] for m in no_output}),
                   'from_names': ['decap', 'tapvpwrvgnd'],
                   'passed': sorted({masters[m]['type'] for m in no_output})
                             == ['decap', 'tapvpwrvgnd']})

    # Semantic anchors: names whose meaning is not in dispute, used to pin the
    # convention. (This is what the plan's 'spot-checks' clause asks for.)
    anchors = [('buf', 'X'), ('inv', 'Y'), ('and2', 'X'), ('nand2', 'Y'),
               ('or2', 'X'), ('nor2', 'Y'), ('xor2', 'X'), ('xnor2', 'Y'),
               ('mux2', 'A0'), ('mux2', 'A1'), ('mux2', 'S'),
               ('dfrtp', 'CLK'), ('dfrtp', 'D'), ('dfrtp', 'Q'), ('dfrtp', 'RESET_B'),
               ('dfstp', 'SET_B'), ('conb', 'HI'), ('conb', 'LO')]
    bad = [f'{t}:{p}' for t, p in anchors
           if not any(d['type'] == t and p in d['pins'] for d in masters.values())]
    checks.append({'check': 'the named-type anchors have their expected pin',
                   'from_labels': bad, 'from_names': [], 'passed': bad == []})

    # supply and body ties
    no_supply = sorted(m for m, d in masters.items()
                       if not {'VPWR', 'VGND'} <= set(d['pins']))
    checks.append({'check': 'every master has both VPWR and VGND',
                   'from_labels': no_supply, 'from_names': [], 'passed': no_supply == []})
    no_vpb = sorted(m for m, d in masters.items() if 'VPB' not in d['pins'])
    checks.append({'check': 'every master but the tap cell has VPB',
                   'from_labels': no_vpb, 'from_names': 'tapvpwrvgnd',
                   'passed': all(m.endswith('tapvpwrvgnd_1') for m in no_vpb)
                             and len(no_vpb) == 1})
    no_vnb = sorted(m for m, d in masters.items() if 'VNB' not in d['pins'])
    checks.append({'check': 'every master but the tap cell has VNB',
                   'from_labels': no_vnb, 'from_names': 'tapvpwrvgnd',
                   'passed': all(m.endswith('tapvpwrvgnd_1') for m in no_vnb)
                             and len(no_vnb) == 1})

    # Label totals must agree with A1's independent per-layer TEXT count, compared
    # like-with-like: A1 splits its count by the kind of cell the labels are in, and we
    # only read the standard-cell masters here. The cell-name layer (83/44) is outside
    # that scope, so its own count is checked separately.
    a1_std = {f'{r["layer"]}/{r["datatype"]}': r['by_cell_kind']['STD']
              for r in a1['pairs'] if r['role'] == 'label'}
    pin_keys = set(derived['master'])
    mismatch = {k: [label_counts.get(k, 0), a1_std.get(k, 0)] for k in sorted(pin_keys)
                if label_counts.get(k, 0) != a1_std.get(k, 0)}
    extra = sorted(set(label_counts) - pin_keys)
    checks.append({'check': 'per-layer label totals agree with A1 (master scope)',
                   'from_labels': dict(label_counts), 'from_names': a1_std,
                   'passed': not mismatch and not extra, 'detail': {'mismatch': mismatch,
                                                                    'unexpected': extra}})
    excl = derived['excluded']
    checks.append({'check': 'the excluded label layer really is out of scope',
                   'from_labels': {k: a1_std.get(k, 0) for k in excl},
                   'from_names': {'83/44': 73},
                   'passed': {k: a1_std.get(k, 0) for k in excl} == {'83/44': 73}})

    # the top cell's ports
    got_ports = sorted(p for p, d in top_ports.items()
                       if any(l.startswith('70/') for l in d['on_layers']))
    checks.append({'check': 'the top cell exposes Step 1\'s 13 ports',
                   'from_labels': got_ports, 'from_names': sorted(EXPECTED_PORTS),
                   'passed': got_ports == sorted(EXPECTED_PORTS)})
    return checks


# ===================================================================== A4: pin geometry
GEOM_OUT = ROOT / 'recon' / 'derived' / 'pinmodel.json'
VIA_OUT = ROOT / 'recon' / 'derived' / 'via_pairs.json'
WARMUP_GDS = ROOT / 'asic-puzzle-2026' / 'warmup' / '04_final.gds'
WARMUP_NETLIST = ROOT / 'asic-puzzle-2026' / 'warmup' / '02_netlist_with_power_rails.v'

BBOX_TOL = 0.002          # um -- slack for the "inside the master" check
TOUCH_EPS = 0.005         # um -- grow one operand so ABUTTING shapes count as touching
PLANE_ROLES = ('routing', 'local_wire', 'pin')
CUT_ROLES = ('via_cut', 'contact')


def _rect(poly) -> list[float]:
    (x0, y0), (x1, y1) = poly.bounding_box()
    return [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]


def _rect_touch(a, b, tol: float = 0.0) -> bool:
    """Bounding-box touch test. Used only as a cheap prefilter -- never as the verdict."""
    return not (a[2] + tol < b[0] or b[2] + tol < a[0]
                or a[3] + tol < b[1] or b[3] + tol < a[1])


def _inflate(poly, eps: float = TOUCH_EPS):
    """Grow a polygon slightly, so that shapes sharing an edge overlap after growth."""
    try:
        out = gdstk.offset(poly, eps, precision=0.001, use_union=True)
    except Exception:
        return None
    return out[0] if out else None


def _shapes_touch(a, b, a_infl, a_rect, b_rect) -> bool:
    """Exact touch test: does `a` grown by ~5 nm intersect `b`?

    Growing one operand is what lets *abutting* shapes count as connected -- a wire drawn
    as abutting rectangles is one wire, and merging only overlapping polygons is the
    published work's trap T5. But the test must stay exact for non-rectangular polygons:
    inside a standard cell the li1 network is not rectilinear, and a bounding-box test
    merges *every* pin of the cell into one component. That failure was observed here, and
    it is precisely what the warm-up calibration exists to catch.
    """
    if not _rect_touch(a_rect, b_rect, TOUCH_EPS):
        return False
    if a_infl is None:
        return True
    try:
        return bool(gdstk.boolean(a_infl, b, 'and', precision=0.001))
    except Exception:
        return False


def local_rule_table(via_doc) -> list[dict]:
    """The master-local connectivity rule set, from A2's proved via pairs.

    A cut layer's *layer number* is shared with the layer it sits on (67/44 is the
    li1->met1 cut, on layer number 67), so a cut cannot be treated as a plane shape just
    because its number matches. Roles separate them: a shape is a *cut node* iff its pair
    is `via_cut` or `contact`; plane shapes are grouped into one physical layer by
    *layer number*, which correctly merges `67/16` with `67/20` but never with `67/44`.
    """
    rules = []
    for r in via_doc['pairs']:
        cut = tuple(int(x) for x in r['cut'].split('/'))
        (la, lb) = (int(s.split('/')[0]) for s in r['connects'])
        rules.append({'cut': cut, 'bridges_layers': [la, lb],
                      'bridges_pairs': list(r['connects']), 'instances': r['instances']})
    return rules


def cell_graph(cell, roles) -> tuple[list[dict], list[set]]:
    """Nodes = shapes on electrical pairs; edges = exact same-layer touch."""
    nodes: list[dict] = []
    for p in cell.polygons:
        k = (p.layer, p.datatype)
        role = roles.get(k)
        if role not in PLANE_ROLES + CUT_ROLES:
            continue
        nodes.append({'pair': k, 'layer': k[0], 'role': role,
                      'is_cut': role in CUT_ROLES, 'rect': _rect(p),
                      'poly': p, 'infl': _inflate(p)})
    adj: list[set] = [set() for _ in nodes]

    planes = [i for i, n in enumerate(nodes) if not n['is_cut']]
    by_layer: dict[int, list[int]] = {}
    for i in planes:
        by_layer.setdefault(nodes[i]['layer'], []).append(i)
    for group in by_layer.values():
        for a in range(len(group)):
            for b in range(a + 1, len(group)):
                ia, ib = group[a], group[b]
                if _shapes_touch(nodes[ia]['poly'], nodes[ib]['poly'], nodes[ia]['infl'],
                                 nodes[ia]['rect'], nodes[ib]['rect']):
                    adj[ia].add(ib)
                    adj[ib].add(ia)
    return nodes, adj


def add_cut_edges(nodes, adj, rules) -> int:
    """Connect each cut node to the plane shapes it bridges, per the rule table."""
    added = 0
    cuts = [i for i, n in enumerate(nodes) if n['is_cut']]
    planes = [i for i, n in enumerate(nodes) if not n['is_cut']]
    for i in cuts:
        for rule in rules:
            if nodes[i]['pair'] != rule['cut']:
                continue
            lo, hi = rule['bridges_layers']

            def touched(j) -> bool:
                return _shapes_touch(nodes[i]['poly'], nodes[j]['poly'], nodes[i]['infl'],
                                     nodes[i]['rect'], nodes[j]['rect'])

            touch_lo = [j for j in planes if nodes[j]['layer'] == lo and touched(j)]
            touch_hi = [j for j in planes if nodes[j]['layer'] == hi and touched(j)]
            if touch_lo and touch_hi:
                for j in touch_lo + touch_hi:
                    if j not in adj[i]:
                        adj[i].add(j)
                        adj[j].add(i)
                        added += 1
    return added


def connected(nodes, adj, seed: list[int]) -> list[int]:
    """Breadth-first closure of the seed over the cell's local connectivity."""
    seen = set(seed)
    queue = list(seed)
    while queue:
        i = queue.pop()
        for j in adj[i]:
            if j not in seen:
                seen.add(j)
                queue.append(j)
    return sorted(seen)


def _contains_point(poly, pt, precision: float = 0.001) -> bool:
    """Exact point-in-polygon test.

    A bounding-box test is not good enough for seeding. Inside a standard cell many li1
    shapes are non-rectangular combs, and a pin label that falls inside such a shape's
    bounding box but outside its outline would seed the wrong net -- observed here, where
    a 14-point li1 shape from the output net had a bounding box covering both input pins.
    """
    try:
        return bool(gdstk.inside([pt], [poly], precision=precision)[0])
    except Exception:
        (x0, y0), (x1, y1) = poly.bounding_box()
        return x0 <= pt[0] <= x1 and y0 <= pt[1] <= y1


def pin_components(cell, roles, rules, label_pairs) -> list[dict]:
    """Every pin of one master: its label anchors and the geometry they reach.

    Only labels on the master's *pin*-label layers count. The cell-name layer (`83/44`)
    is excluded here for the same structural reason A3 excludes it: its labels annotate
    nothing electrical, and treating one as a pin would invent a pin that does not exist.
    """
    nodes, adj = cell_graph(cell, roles)
    add_cut_edges(nodes, adj, rules)
    if not nodes:
        return []
    planes = [i for i, n in enumerate(nodes) if not n['is_cut']]
    out = []
    for lb in cell.labels:
        if (lb.layer, lb.texttype) not in label_pairs:
            continue
        pose = lb.origin
        containing = [i for i in planes if _contains_point(nodes[i]['poly'], pose)]
        if not containing:
            out.append({'pin': lb.text, 'position_um': [round(pose[0], 4),
                                                        round(pose[1], 4)],
                        'seed_method': 'none', 'shapes': []})
            continue
        # Prefer a shape on the label's own layer number; fall back to the smallest on any
        # plane layer. The fallback matters for exactly one pin: VNB's label sits on layer
        # 64/59 while its own contact lives on 122/16, so the label lands on the supply
        # pin geometry drawn at the same coordinates. Recorded, not hidden.
        own = [i for i in containing if nodes[i]['layer'] == lb.layer]
        pick = own or containing
        seed = [min(pick, key=lambda i: (nodes[i]['rect'][2] - nodes[i]['rect'][0])
                    * (nodes[i]['rect'][3] - nodes[i]['rect'][1]))]
        method = 'same_layer_number' if own else 'other_layer'
        comp = connected(nodes, adj, seed)
        out.append({
            'pin': lb.text, 'position_um': [round(pose[0], 4), round(pose[1], 4)],
            'seed_method': method,
            'shapes': [{'pair': f"{nodes[i]['pair'][0]}/{nodes[i]['pair'][1]}",
                        'role': nodes[i]['role'], 'rect': nodes[i]['rect']}
                       for i in comp],
        })
    return out


def parse_netlist_ports(text: str) -> dict[str, set[str]]:
    """cell type -> the set of port names the netlist connects by name."""
    out: dict[str, set[str]] = {}
    for m in re.finditer(r'sky130_fd_sc_hd__([a-z0-9_]+)\s+(\S+)\s*\(', text):
        typ, start = m.group(1), m.end()
        end = text.find(');', start)
        if end < 0:
            continue
        for pm in re.finditer(r'\.([A-Za-z0-9_\[\]]+)\s*\(', text[start:end]):
            out.setdefault(typ, set()).add(pm.group(1))
    return out


def warmup_calibration(roles) -> dict:
    """Run *this same pipeline* on warmup/04_final.gds and compare against its netlist.

    The netlist was produced by the vendor flow; our names come from the layout. They
    share no input, so agreement checks the extraction rather than restating it.
    """
    from tools.puzzle import layers as L
    lib_w = gdstk.read_gds(str(WARMUP_GDS))
    top_w = lib_w.top_level()[0]
    res = L.classify_all(lib_w, top_w.name)
    derived_w = derive_pin_label_layers({'pairs': res['records']})
    pairs_w = {tuple(int(x) for x in s.split('/')) for s in derived_w['master']}

    ours: dict[str, set[str]] = {}
    for cell in lib_w.cells:
        if cell_kind(cell.name, top_w.name) != 'STD':
            continue
        pins = {lb.text for lb in cell.labels if (lb.layer, lb.texttype) in pairs_w}
        # keyed by the full library tail (`nand2_2`), the same key the netlist uses --
        # `cell_type()` strips the drive suffix and would not line up.
        ours.setdefault(cell.name.split('__')[-1], set()).update(pins)

    netlist = parse_netlist_ports(WARMUP_NETLIST.read_text(encoding='utf-8'))
    types = sorted(set(ours) | set(netlist))
    detail = {}
    for t in types:
        a, b = sorted(ours.get(t, set())), sorted(netlist.get(t, set()))
        detail[t] = {'from_layout': a, 'from_netlist': b, 'match': a == b}
    return {
        'warmup_top_cell': top_w.name,
        'netlist': WARMUP_NETLIST.name,
        'cell_types': len(types),
        'all_match': all(v['match'] for v in detail.values()),
        'detail': detail,
        'label_layers_derived_on_warmup': derived_w['master'],
        'warmup_layer_roles_excluded': derived_w['excluded'],
    }


def stage_pin_geom(argv: list[str]) -> int:
    lib = gdstk.read_gds(str(GDS))
    top = lib.top_level()[0]
    a1 = json.loads(A1_OUT.read_text(encoding='utf-8'))
    names = json.loads(NAMES_OUT.read_text(encoding='utf-8'))
    via = json.loads(VIA_OUT.read_text(encoding='utf-8'))
    roles = {(r['layer'], r['datatype']): r['role'] for r in a1['pairs']}
    rules = local_rule_table(via)
    label_pairs = {tuple(int(x) for x in s.split('/'))
                   for s in names['pin_label_layers']['master']}

    masters = {}
    for cell in sorted(lib.cells, key=lambda c: c.name):
        if cell_kind(cell.name, top.name) != 'STD':
            continue
        bb = cell.bounding_box()
        pins: dict[str, dict] = {}
        for comp in pin_components(cell, roles, rules, label_pairs):
            p = pins.setdefault(comp['pin'], {'labels': 0, 'seed_methods': set(),
                                              'shapes': [], 'positions_um': []})
            p['labels'] += 1
            p['seed_methods'].add(comp['seed_method'])
            p['positions_um'].append(comp['position_um'])
            for s in comp['shapes']:
                if s not in p['shapes']:
                    p['shapes'].append(s)
        # keep only pins that this master actually labels, and summarise
        for name in list(pins):
            p = pins[name]
            pairs_used = sorted({s['pair'] for s in p['shapes']})
            p['seed_methods'] = sorted(p['seed_methods'])
            p['by_pair'] = {k: sum(1 for s in p['shapes'] if s['pair'] == k)
                            for k in pairs_used}
            p['routing_rects'] = [s['rect'] for s in p['shapes']
                                  if s['role'] in ('routing', 'local_wire')]
            p['area_um2'] = round(sum((s['rect'][2] - s['rect'][0])
                                      * (s['rect'][3] - s['rect'][1])
                                      for s in p['shapes']), 4)
            p['n_shapes'] = len(p['shapes'])
        for name in sorted(set(names['masters'][cell.name]['pins']) - set(pins)):
            pins[name] = {'labels': 0, 'seed_methods': ['none'], 'shapes': [],
                          'by_pair': {}, 'routing_rects': [], 'area_um2': 0.0,
                          'n_shapes': 0, 'positions_um': []}
        masters[cell.name] = {'type': cell_type(cell.name),
                              'bbox_um': [round(v, 4) for v in bb[0] + bb[1]],
                              'pins': {k: pins[k] for k in sorted(pins)},
                              'n_pins': len(pins)}

    # ---- structural checks
    checks = []
    outside, nonint, empty = [], [], []
    for mn, m in masters.items():
        x0, y0, x1, y1 = m['bbox_um']
        for pn, p in m['pins'].items():
            if p['n_shapes'] == 0:
                empty.append(f'{mn}:{pn}')
            for s in p['shapes']:
                r = s['rect']
                if (r[0] < x0 - BBOX_TOL or r[1] < y0 - BBOX_TOL
                        or r[2] > x1 + BBOX_TOL or r[3] > y1 + BBOX_TOL):
                    outside.append(f'{mn}:{pn}:{s["pair"]}{r}')
                if any(abs(v * 1000 - round(v * 1000)) > 1e-6 for v in r):
                    nonint.append(f'{mn}:{pn}:{r}')
    checks.append({'check': 'every pin rect lies inside its master bbox', 'failures': outside})
    checks.append({'check': 'every coordinate is an integer number of DBU', 'failures': nonint})
    checks.append({'check': 'no named pin is left without geometry', 'failures': empty})
    for c in checks:
        c['passed'] = not c['failures']

    calib = warmup_calibration(roles)

    out = {
        'generated_by': 'tools/puzzle/pins.py::pin-geom',
        'source': {'path': 'asic-puzzle-2026/puzzle.gds', 'sha256': sha256(GDS)},
        'inputs': ['recon/derived/layers.json', 'recon/derived/pin_names.json',
                   'recon/derived/via_pairs.json'],
        'local_rule_table': rules,
        'plane_roles': list(PLANE_ROLES),
        'cut_roles': list(CUT_ROLES),
        'touch_rule': 'bounding boxes touching or overlapping (abutting counts)',
        'scope_note': ('traced over routing/local_wire/pin/contact pairs only; device '
                       'layers (well, diffusion, poly) are deliberately not traversed'),
        'masters': masters,
        'checks': checks,
        'warmup_calibration': calib,
        'totals': {
            'masters': len(masters),
            'pins': sum(m['n_pins'] for m in masters.values()),
            'pins_with_geometry': sum(1 for m in masters.values()
                                      for p in m['pins'].values() if p['n_shapes']),
            'pin_shapes': sum(p['n_shapes'] for m in masters.values()
                              for p in m['pins'].values()),
            'seed_methods': dict(Counter(p['seed_methods'][0] for m in masters.values()
                                         for p in m['pins'].values())),
            'warmup_cell_types_all_match': calib['all_match'],
            'warmup_cell_types': calib['cell_types'],
        },
    }
    GEOM_OUT.parent.mkdir(parents=True, exist_ok=True)
    GEOM_OUT.write_text(json.dumps(out, indent=2, sort_keys=False) + '\n',
                        encoding='utf-8', newline='\n')

    # ---- report
    print('local rule table:')
    for r in rules:
        print(f"  {r['cut'][0]}/{r['cut'][1]:<3} bridges layers {r['bridges_layers']} "
              f"({r['bridges_pairs']}), {r['instances']} instances")
    print()
    print(f'{"master":<38}{"pins":>5}{"shapes":>7}  seed methods')
    print('-' * 88)
    for mn, m in masters.items():
        nsh = sum(p['n_shapes'] for p in m['pins'].values())
        sm = sorted({s for p in m['pins'].values() for s in p['seed_methods']})
        print(f"{mn:<38}{m['n_pins']:>5}{nsh:>7}  {', '.join(sm)}")
    print()
    print('structural checks:')
    for c in checks:
        print(f"  {'PASS' if c['passed'] else 'FAIL'}  {c['check']}"
              + ('' if c['passed'] else f"  -> {c['failures'][:4]}"))
    print()
    print(f"warm-up calibration ({calib['netlist']} vs warmup/04_final.gds):")
    print(f"  top cell {calib['warmup_top_cell']!r}, {calib['cell_types']} cell types, "
          f"all match: {calib['all_match']}")
    for t, v in sorted(calib['detail'].items()):
        if not v['match']:
            print(f"    MISMATCH {t}: layout={v['from_layout']}")
            print(f"                 netlist={v['from_netlist']}")
    print()
    missing = [f'{mn}:{pn}' for mn, m in masters.items()
               for pn, p in m['pins'].items() if p['n_shapes'] == 0]
    print(f'pins with no geometry ({len(missing)}): {missing}')
    print('totals:', json.dumps(out['totals']))
    print(f'written: {GEOM_OUT.relative_to(ROOT)}')
    bad = (not calib['all_match']) or any(not c['passed'] for c in checks)
    return 1 if bad else 0


# ===================================================================== A5: coverage
COV_OUT = ROOT / 'recon' / 'derived' / 'pin_coverage.json'


def stage_pin_coverage(argv: list[str]) -> int:
    """A5: state exactly how complete the pin model is, before anything trusts it.

    Note on artifact ownership: the plan said to append this to `pinmodel.json`. It is a
    separate file instead, because A4's gate asserts that re-running `pin-geom` reproduces
    that file byte for byte -- an appended key would break that guarantee for a stage that
    does not own it. Each stage owns its own artifact.
    """
    lib = gdstk.read_gds(str(GDS))
    top = lib.top_level()[0]
    a1 = json.loads(A1_OUT.read_text(encoding='utf-8'))
    a3 = json.loads(NAMES_OUT.read_text(encoding='utf-8'))
    a4 = json.loads(GEOM_OUT.read_text(encoding='utf-8'))

    pin_layers = {tuple(int(x) for x in s.split('/'))
                  for s in a3['pin_label_layers']['master']}
    excluded_layers = {tuple(int(x) for x in s.split('/'))
                       for s in a3['pin_label_layers']['excluded']}

    # ---- 1. complete label census over the standard-cell masters ---------------
    census: dict[str, int] = {}
    per_layer: Counter = Counter()
    unknown: list[str] = []
    placements: Counter = Counter()
    for ref in top.references:
        placements[ref.cell.name] += (getattr(ref, 'columns', 1) or 1) * \
                                     (getattr(ref, 'rows', 1) or 1)
    for cell in lib.cells:
        if cell_kind(cell.name, top.name) != 'STD':
            continue
        for lb in cell.labels:
            k = (lb.layer, lb.texttype)
            if k in pin_layers:
                cat = 'pin_label'
            elif k in excluded_layers:
                cat = 'cell_name'
            else:
                cat = 'unclassified'
                unknown.append(f'{cell.name}:{lb.text}')
            census[cat] = census.get(cat, 0) + 1
            per_layer[f'{lb.layer}/{lb.texttype}'] += 1

    # ---- 2. named vs modelled vs geometry-bearing -----------------------------
    a3_keys = {(mn, pn) for mn, m in a3['masters'].items() for pn in m['pins']}
    a4_keys = {(mn, pn) for mn, m in a4['masters'].items() for pn in m['pins']}
    geom_keys = {(mn, pn) for mn, m in a4['masters'].items()
                 for pn, p in m['pins'].items() if p['n_shapes'] > 0}
    routed_keys = {(mn, pn) for mn, m in a4['masters'].items()
                   for pn, p in m['pins'].items() if p['routing_rects']}

    # ---- 3. what each exception is, by cause ---------------------------------
    def cause(mn: str, pn: str) -> str:
        if pn == 'VPB':
            return 'well-tie contact only (64/16); no routing-layer geometry'
        if mn.endswith('diode_2'):
            return "antenna diode: supply exists only as a 68/16 pin square"
        return 'unexplained'

    exceptions = Counter()
    unexplained = []
    for mn, pn in sorted(a4_keys - routed_keys):
        c = cause(mn, pn)
        exceptions[c] += 1
        if c == 'unexplained':
            unexplained.append(f'{mn}:{pn}')

    # ---- 4. per-routing-layer reach, and instance-level reach ----------------
    layer_reach: Counter = Counter()
    for m in a4['masters'].values():
        for p in m['pins'].values():
            for pair in p['by_pair']:
                if pair in {f'{r["layer"]}/{r["datatype"]}' for r in a1['pairs']
                            if r['role'] in ('routing', 'local_wire')}:
                    layer_reach[pair] += 1
    pin_instances = sum(placements.get(mn, 0) * len(m['pins'])
                        for mn, m in a4['masters'].items())
    routed_instances = sum(placements.get(mn, 0) * sum(
        1 for p in m['pins'].values() if p['routing_rects'])
        for mn, m in a4['masters'].items())

    checks = [
        {'check': 'every master pin label falls in a known category',
         'failures': unknown, 'passed': not unknown},
        {'check': 'A4 models exactly the pins A3 named',
         'failures': sorted(set(a3_keys) ^ set(a4_keys)), 'passed': a3_keys == a4_keys},
        {'check': 'every modelled pin carries geometry',
         'failures': sorted(a4_keys - geom_keys), 'passed': a4_keys == geom_keys},
        {'check': 'every pin without routing geometry has a recorded cause',
         'failures': unexplained, 'passed': not unexplained},
        {'check': 'the label census accounts for every label in the masters',
         'failures': [], 'passed': sum(census.values()) == a1_total_labels(a1)},
        {'check': 'the pin-label total matches A3',
         'failures': [], 'passed': census.get('pin_label', 0) == a3['totals']['pin_labels']},
    ]

    out = {
        'generated_by': 'tools/puzzle/pins.py::pin-coverage',
        'source': {'path': 'asic-puzzle-2026/puzzle.gds', 'sha256': sha256(GDS)},
        'inputs': ['recon/derived/pin_names.json', 'recon/derived/pinmodel.json'],
        'label_census': {
            'total': sum(census.values()),
            'by_category': dict(sorted(census.items())),
            'per_layer': dict(sorted(per_layer.items())),
            'excluded_layers': sorted(f'{a}/{b}' for a, b in excluded_layers),
            'unclassified': unknown,
        },
        'pin_coverage': {
            'named': len(a3_keys),
            'modelled': len(a4_keys),
            'with_geometry': len(geom_keys),
            'with_routing_geometry': len(routed_keys),
            'without_routing_geometry': len(a4_keys - routed_keys),
            'exceptions_by_cause': dict(sorted(exceptions.items())),
            'routing_coverage_pct': round(100.0 * len(routed_keys) / max(len(a4_keys), 1), 2),
        },
        'routing_layer_reach_pins': dict(sorted(layer_reach.items())),
        'instance_reach': {
            'placed_pins': pin_instances,
            'placed_pins_with_routing_geometry': routed_instances,
            'coverage_pct': round(100.0 * routed_instances / max(pin_instances, 1), 2),
        },
        'checks': checks,
        'verdict': 'PIN MODEL COVERAGE: PASS' if all(c['passed'] for c in checks)
                   else 'PIN MODEL COVERAGE: FAIL',
    }
    COV_OUT.parent.mkdir(parents=True, exist_ok=True)
    COV_OUT.write_text(json.dumps(out, indent=2, sort_keys=False) + '\n',
                       encoding='utf-8', newline='\n')

    # ---- report
    lc, pc = out['label_census'], out['pin_coverage']
    print('label census over the 69 standard-cell masters:')
    for k, v in sorted(lc['per_layer'].items()):
        role = ('pin label' if k in a3['pin_label_layers']['master']
                else 'cell name' if k in a3['pin_label_layers']['excluded']
                else 'UNCLASSIFIED')
        print(f'   {k:<16}{v:>5}   {role}')
    print(f'   {"total":<16}{lc["total"]:>5}')
    print()
    print('pin coverage:')
    print(f'   named (A3)                  {pc["named"]:>5}')
    print(f'   modelled (A4)               {pc["modelled"]:>5}')
    print(f'   with geometry               {pc["with_geometry"]:>5}')
    print(f'   with routing geometry       {pc["with_routing_geometry"]:>5}  '
          f'({pc["routing_coverage_pct"]}%)')
    print(f'   without routing geometry    {pc["without_routing_geometry"]:>5}')
    for c, n in pc['exceptions_by_cause'].items():
        print(f'        {n:>4}  {c}')
    print()
    print('routing-layer reach (pins with geometry on each layer):')
    for k, v in out['routing_layer_reach_pins'].items():
        print(f'   {k:<8}{v:>5}')
    print()
    ir = out['instance_reach']
    print(f'instance reach: {ir["placed_pins_with_routing_geometry"]} of '
          f'{ir["placed_pins"]} placed pins ({ir["coverage_pct"]}%)')
    print()
    print('checks:')
    for c in checks:
        print(f"  {'PASS' if c['passed'] else 'FAIL'}  {c['check']}"
              + ('' if c['passed'] else f'  -> {c["failures"][:4]}'))
    print()
    print(out['verdict'])
    print(f'written: {COV_OUT.relative_to(ROOT)}')
    return 0 if all(c['passed'] for c in checks) else 1


def a1_total_labels(a1: dict) -> int:
    """Label elements inside the standard-cell masters, from A1's per-pair counts.

    A1 splits each label pair by the kind of cell the labels sit in, so this must be the
    STD share -- the file total would also count the top cell's ports and the two
    outside-die cells, which are not part of the master label census.
    """
    return sum(r['by_cell_kind']['STD'] for r in a1['pairs'] if r['role'] == 'label')


def main(argv: list[str]) -> int:
    stage = argv[0] if argv else 'pin-names'
    if stage == 'pin-names':
        return stage_pin_names(argv[1:])
    if stage == 'pin-geom':
        return stage_pin_geom(argv[1:])
    if stage == 'pin-coverage':
        return stage_pin_coverage(argv[1:])
    print(f'pins.py does not provide stage {stage!r} yet', file=sys.stderr)
    return 2


def stage_pin_names(argv: list[str]) -> int:
    lib = gdstk.read_gds(str(GDS))
    top = lib.top_level()[0]
    a1 = json.loads(A1_OUT.read_text(encoding='utf-8'))
    derived = derive_pin_label_layers(a1)
    master_pairs = {tuple(int(x) for x in s.split('/')) for s in derived['master']}
    top_pairs = {tuple(int(x) for x in s.split('/')) for s in derived['top']}

    masters = {}
    label_counts: Counter = Counter()
    for cell in sorted(lib.cells, key=lambda c: c.name):
        if cell_kind(cell.name, top.name) != 'STD':
            continue
        for lb in cell.labels:
            if (lb.layer, lb.texttype) in master_pairs:
                label_counts[f'{lb.layer}/{lb.texttype}'] += 1
        pins = extract_pins(cell, master_pairs)
        masters[cell.name] = {'type': cell_type(cell.name), 'pins': pins,
                              'n_pins': len(pins)}
    top_ports = extract_pins(top, top_pairs)

    census: Counter = Counter()
    for m in masters.values():
        for p in m['pins']:
            census[p] += 1

    checks = consistency_checks(masters, top_ports, a1, label_counts, derived)
    n_failed = sum(1 for c in checks if not c['passed'])

    out = {
        'generated_by': 'tools/puzzle/pins.py::pin-names',
        'source': {'path': 'asic-puzzle-2026/puzzle.gds', 'sha256': sha256(GDS)},
        'input': 'recon/derived/layers.json',
        'pin_label_layers': {k: derived[k] for k in ('master', 'top', 'excluded',
                                                     'geometry_layers')},
        'body_tie_pins': sorted(BODY_TIE_PINS),
        'masters': masters,
        'top_ports': top_ports,
        'census': dict(sorted(census.items(), key=lambda kv: (-kv[1], kv[0]))),
        'label_counts_by_layer': dict(sorted(label_counts.items())),
        'consistency': {'checks': checks, 'passed': sum(1 for c in checks if c['passed']),
                        'failed': n_failed},
        'totals': {'masters': len(masters), 'distinct_pin_names': len(census),
                   'pin_labels': sum(label_counts.values()),
                   'top_port_labels': len(top_ports)},
    }
    NAMES_OUT.parent.mkdir(parents=True, exist_ok=True)
    NAMES_OUT.write_text(json.dumps(out, indent=2, sort_keys=False) + '\n',
                         encoding='utf-8', newline='\n')

    # ---- report
    print(f'pin-label layers for masters : {derived["master"]}')
    print(f'pin-label layers for top cell : {derived["top"]}')
    for k, why in derived['excluded'].items():
        print(f'excluded label layer         : {k}  ({why})')
    print(f'label counts per layer       : {dict(sorted(label_counts.items()))}')
    print()
    print(f'{"master":<38}{"type":<12}{"n":>3}  pins')
    print('-' * 102)
    for name, m in masters.items():
        print(f"{name:<38}{m['type']:<12}{m['n_pins']:>3}  {' '.join(sorted(m['pins']))}")
    print()
    print('top-cell ports:', ' '.join(sorted(top_ports)))
    print()
    print('consistency checks:')
    for c in checks:
        print(f"  {'PASS' if c['passed'] else 'FAIL'}  {c['check']}")
        if not c['passed']:
            print(f"          from labels: {c['from_labels']}")
            print(f"          from names : {c['from_names']}")
    print()
    print(f'{len(census)} distinct pin names across {len(masters)} masters '
          f'({sum(label_counts.values())} pin labels)')
    print(f'written: {NAMES_OUT.relative_to(ROOT)}')
    return 1 if n_failed else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
