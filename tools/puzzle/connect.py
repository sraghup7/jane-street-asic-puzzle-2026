#!/usr/bin/env python3
"""connect.py -- B2/B3: connectivity extraction.

    python -m tools.puzzle connect          ->  recon/derived/warmup_netlist.json

B2 is the spike the plan flags as the project's biggest bet: run the connectivity engine on
the **warm-up**, whose correct netlist we hold, before pointing it at the puzzle. Two
decisions make this our own rather than a re-implementation of the published pipeline:

* **The engine is KLayout's built-in extractor (D3)** -- the tool the published work
  abandoned untested. It is configured entirely from *our* derived data: A1's conductor and
  cut sets, and A2's proved rule set. Nothing is a hand-written layer map.
* **The terminals are ours (A4).** The engine is given geometry and asked only "what is
  connected to what"; which geometry constitutes a pin comes from the pin model, so the
  engine is never asked to guess device structure.

Section 4 is the verification the plan demands: our extracted partition of
(instance, pin) into nets must equal `01_netlist.v`'s, up to net renaming. Instance identity
between the two is bridged by the DEF, which B1 proved matches the GDS placement exactly.

Reading the extraction API (0.30.12, all measured not assumed):
  * `connect` takes ONE or TWO arguments -- there is no 3-argument via form, so each A2 rule
    becomes two pairwise connections that transitively join the two conductors;
  * `extract_netlist()` returns the LayoutToNetlist itself, and the netlist comes from the
    `.netlist()` accessor;
  * `Net#cluster_id()` is the stable net identity, and `probe_net(region, point)` answers
    "which net is at this point" only after extraction.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

import gdstk
from klayout import db

ROOT = Path(__file__).resolve().parents[2]

WU_GDS = ROOT / 'asic-puzzle-2026' / 'warmup' / '04_final.gds'
WU_DEF = ROOT / 'asic-puzzle-2026' / 'warmup' / '03_post_place_and_route.def'
WU_NET = ROOT / 'asic-puzzle-2026' / 'warmup' / '01_netlist.v'

A1 = ROOT / 'recon' / 'derived' / 'layers.json'
A2 = ROOT / 'recon' / 'derived' / 'via_pairs.json'
A4 = ROOT / 'recon' / 'derived' / 'pinmodel.json'
INV = ROOT / 'recon' / 'inventory.json'
OUT = ROOT / 'recon' / 'derived' / 'warmup_netlist.json'
OUT_NETS = ROOT / 'recon' / 'derived' / 'nets.json'

from tools.puzzle import instances as I                 # noqa: E402

STD = 'sky130_fd_sc_hd__'

# Pins that are supply/body, not signal. The netlist we compare against (01_netlist.v) is
# the pre-power-rails netlist, so power is excluded from both sides.
SUPPLY = {'VPWR', 'VGND', 'VPB', 'VNB'}

INSTANCE = re.compile(r'(sky130_fd_sc_hd__\w+)\s+(\S+)\s*\((.*?)\);', re.S)
PIN = re.compile(r'\.(\w+)\s*\(\s*(\\?\S+?)\s*\)')


def read_json(p: Path):
    return json.loads(p.read_text(encoding='utf-8'))


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def parse_netlist(text: str):
    """-> ({instance: {pin: net}}, instantiation order)"""
    insts, order = {}, []
    for cell, name, body in INSTANCE.findall(text):
        insts[name] = {'cell': cell, 'pins': dict(
            (p, n.lstrip('\\')) for p, n in PIN.findall(body)), 'zero_pin': not body.strip()}
        order.append(name)
    return insts, order


def parse_def_placements(text: str):
    """DEF component name -> (cell, lower-left x, lower-left y) in DBU."""
    out = {}
    for m in (I.DEF_COMPONENT.match(ln) for ln in text.splitlines()):
        if m:
            out[m.group(1)] = (m.group(2), int(m.group(3)), int(m.group(4)))
    return out


def warmup_instances(per_um: int):
    """The 230 warm-up placements, using the B1 convention and the same anchor semantics."""
    lib = gdstk.read_gds(str(WU_GDS))
    top = lib.top_level()[0]
    refs = [r for r in top.references if r.cell.name.startswith(STD)]

    m_bbox, m_foot = {}, {}
    for r in refs:
        n = r.cell.name
        if n in m_bbox:
            continue
        x0, y0, x1, y1 = I.flat_bbox(r.cell)
        m_bbox[n] = tuple(I.dbu(v, per_um) for v in (x0, y0, x1, y1))
        fp = r.cell.get_polygons(layer=I.FOOTPRINT[0], datatype=I.FOOTPRINT[1])
        m_foot[n] = (tuple(I.dbu(v, per_um) for v in I.polys_bbox(fp)) if fp
                     else tuple(m_bbox[n][i] + (190, 240, -190, -240)[i] for i in range(4)))

    # The B1 convention, applied to the warm-up. Re-derived here rather than imported,
    # because a whole-table assumption is what B1 tests.
    conv = {'rot000': (1, 1), 'rot000_mirror': (1, -1),
            'rot180': (-1, -1), 'rot180_mirror': (-1, 1)}
    out = []
    for r in refs:
        kind = I.KIND_OF[(int(round(r.rotation / (3.141592653589793 / 2))) % 4,
                          bool(r.x_reflection))]
        origin = (I.dbu(r.origin[0], per_um), I.dbu(r.origin[1], per_um))
        gx0, gy0, _, _ = I.apply(conv[kind], origin, m_foot[r.cell.name])
        out.append({'id': f'w{len(out):03d}', 'master': r.cell.name, 'kind': kind,
                    'origin_dbu': origin, 'lower_left_dbu': (gx0, gy0),
                    'transform': conv[kind]})
    return out, m_bbox


def main(argv: list[str]) -> int:
    stage = argv[0] if argv else 'connect'
    if stage == 'nets':
        return stage_nets()
    if stage not in ('connect', 'warmup-regression'):
        print(f'connect.py has no stage {stage!r}')
        return 2

    a1, a2, a4, inv = read_json(A1), read_json(A2), read_json(A4), read_json(INV)
    per_um = int(inv['gds']['dbu_per_um'])
    conductors = sorted({p for r in a2['pairs'] for p in r['connects']})
    cuts = sorted({r['cut'] for r in a2['pairs']})

    # ---- 1. the warm-up's 230 placements, under the B1 convention ------------------
    placements, m_bbox = warmup_instances(per_um)
    print(f'warm-up placements (SATD cells)     : {len(placements)}')
    print(f'  cell types                        : {len({p["master"] for p in placements})}')

    # ---- 2. configure the engine from our derived data only ------------------------
    ly = db.Layout()
    ly.read(str(WU_GDS))
    top = ly.top_cell()

    def lnum(pair):
        l, dt = (int(x) for x in pair.split('/'))
        return ly.layer(l, dt)

    l2n = db.LayoutToNetlist(db.RecursiveShapeIterator(
        ly, top, [lnum(p) for p in conductors + cuts]))
    reg = {p: l2n.make_layer(lnum(p)) for p in conductors + cuts}
    print(f'\nengine: KLayout LayoutToNetlist on {1/ly.dbu:.0f} dbu/um')
    for c in conductors:
        l2n.connect(reg[c])
    for r in a2['pairs']:
        a, b = r['connects']
        l2n.connect(reg[a], reg[r['cut']])
        l2n.connect(reg[r['cut']], reg[b])
    print(f'  conductors connected              : {len(conductors)}')
    print(f'  via rules applied (as 2 connects) : {len(a2["pairs"])}')
    l2n.extract_netlist()
    nl = l2n.netlist()
    print(f'  netlist extracted                 : {nl is not None} '
          f'({sum(1 for _ in nl.each_circuit())} circuits)')

    # ---- 3. terminals from A4, probed through the engine --------------------------
    # Probe points come from the pin's **label positions**, not from the centre of its
    # bounding rect. Measured: `clkbuf_16`'s X pin is a 68-point comb whose bounding rect
    # centre lies inside no polygon at all, so a bbox-centre probe finds nothing; every
    # label sits inside the pin geometry by construction (it is how A4 seeded the trace).
    probes, missing, probe_errors, conflicts = [], 0, [], []
    for pl in placements:
        pins = a4['masters'][pl['master']]['pins']
        sx, sy = pl['transform']
        ax, ay = pl['origin_dbu']
        for pin, info in sorted(pins.items()):
            if pin in SUPPLY:
                continue
            cands = sorted({s['pair'] for s in info['shapes']} & set(conductors))
            if not cands:
                missing += 1
                continue
            found = None
            for pos in (info.get('positions_um') or []):
                lx, ly = (I.dbu(v, per_um) for v in pos)
                pt = (sx * lx + ax, sy * ly + ay)
                seen = set()
                for pair in cands:
                    try:
                        net = l2n.probe_net(reg[pair], db.Point(*pt))
                    except Exception as exc:        # noqa: BLE001
                        probe_errors.append(f'{pl["id"]}.{pin}@{pair}: {exc}')
                        continue
                    if net is not None:
                        seen.add(net.cluster_id)
                if len(seen) > 1:
                    conflicts.append({'instance': pl['id'], 'pin': pin,
                                      'point_dbu': list(pt), 'clusters': sorted(seen)})
                if seen and found is None:
                    found = (sorted(seen)[0], pair, pt)
            probes.append({'instance': pl['id'], 'pin': pin,
                           'layer': found[1] if found else None,
                           'point_dbu': list(found[2]) if found else None,
                           'cluster': found[0] if found else None})
    print(f'\nfunctional pins probed              : {len(probes)}')
    print(f'  pins with no conductor geometry   : {missing}')
    print(f'  probe errors                      : {len(probe_errors)}')
    print(f'  label points disagreeing on a net  : {len(conflicts)}')
    for c in conflicts[:4]:
        print(f'      {c["instance"]}.{c["pin"]} -> clusters {c["clusters"]}')
    unassigned = [p for p in probes if p['cluster'] is None]
    print(f'  pins the engine left unconnected  : {len(unassigned)}')
    for p in unassigned[:5]:
        print(f'      {p["instance"]}.{p["pin"]}')

    # our partition: net identity -> terminals. Pins the engine could not place are NOT
    # lumped into a pseudo-net -- that would silently merge 32 unrelated pins into one
    # signature, which is exactly how the first run of this spike misreported itself.
    ours = defaultdict(set)
    for p in probes:
        if p['cluster'] is not None:
            ours[p['cluster']].add((p['instance'], p['pin']))

    # ---- 4. the verification: partition equivalence with 01_netlist.v --------------
    net_text = WU_NET.read_text(encoding='utf-8')
    insts, order = parse_netlist(net_text)
    def_place = parse_def_placements(WU_DEF.read_text(encoding='utf-8'))
    print(f'\nreference netlist 01_netlist.v      : {len(insts)} instances, {len(order)} lines')
    print(f'  DEF placements                    : {len(def_place)}')

    # Instance identity: netlist name -> DEF (cell, lower-left) -> a warm-up placement.
    # The two files agree on the hierarchical name ("add0/_32_"); the netlist just escapes
    # it with a leading backslash. So the bridge is exactly `lstrip('\\')` -- neither
    # matching raw (154 of 230, physical cells only) nor stripping the hierarchy
    # (`split('/')[-1]`) works; both leave the 76 functional instances out and produce a
    # comparison that "passes" over almost nothing. The coverage guard below exists because
    # of that.
    by_ll = {(pl['master'], pl['lower_left_dbu']): pl['id'] for pl in placements}
    id_map, unmapped = {}, []
    for name, info in insts.items():
        leaf = name.lstrip('\\')
        if leaf not in def_place:
            unmapped.append(name)
            continue
        cell, dx, dy = def_place[leaf]
        key = (cell, (dx, dy))
        if key not in by_ll:
            unmapped.append(name)
            continue
        id_map[name] = by_ll[key]
    print(f'  netlist instance -> GDS placement : {len(id_map)} mapped, {len(unmapped)} unmapped')
    if unmapped:
        print(f'    unmapped: {unmapped[:6]}')

    theirs = defaultdict(set)
    for name, info in insts.items():
        if name not in id_map:
            continue
        for pin, net in info['pins'].items():
            if pin in SUPPLY:
                continue
            theirs[net].add((id_map[name], pin))

    ref_terminals = {t for v in theirs.values() for t in v}
    our_terminals = {t for v in ours.values() for t in v}
    extra = sorted(our_terminals - ref_terminals)
    absent = sorted(ref_terminals - our_terminals)
    print(f'  our terminals not connected by the reference : {len(extra)}')
    if extra:
        print(f'    pin names involved: {sorted({p for _, p in extra})}')
    print(f'  reference terminals we could not place       : {len(absent)}')
    if absent:
        print(f'    {absent[:8]}')

    # Restricted comparison. The reference netlist simply omits pins it leaves unused (an
    # unconnected `A_N`, say), so comparing full terminal sets would fail for a reason that
    # has nothing to do with connectivity. Both sides are therefore restricted to the
    # terminals the reference connects; a *mis*-connection still shows up, because it
    # changes which of those terminals share a net.
    def canon(part):
        return Counter(frozenset(t) for t in part.values())

    ours_f = {k: {t for t in v if t in ref_terminals} for k, v in ours.items()}
    ours_f = {k: v for k, v in ours_f.items() if v}
    a_can, b_can = canon(ours_f), canon(theirs)
    print()
    print('partition comparison (nets as sets of (instance, pin), canonicalised)')
    print(f'  our nets          : {len(ours_f)}')
    print(f'  netlist nets      : {len(theirs)}')
    print(f'  our terminals     : {sum(len(v) for v in ours_f.values())}')
    print(f'  netlist terminals : {sum(len(v) for v in theirs.values())}')
    only_ours = a_can - b_can
    only_theirs = b_can - a_can
    print(f'  net signatures only in ours   : {sum(only_ours.values())}')
    print(f'  net signatures only in the ref : {sum(only_theirs.values())}')
    for sig in list(only_theirs)[:4]:
        print(f'      ref-only {sorted(sig)}')
    for sig in list(only_ours)[:4]:
        print(f'      ours-only {sorted(sig)}')

    # A coverage guard, because a comparison over almost nothing passes for the wrong
    # reason: the first corrected attempt compared 4 nets and reported PASS. Every terminal
    # the reference connects must be present on our side and be compared.
    compared = {t for v in ours_f.values() for t in v}
    coverage = len(compared) / max(1, len(ref_terminals))
    print(f'  reference terminals compared  : {len(compared)} / {len(ref_terminals)}'
          f'  ({coverage:.1%})')
    equivalent = a_can == b_can and coverage == 1.0
    result = {
        'generated_by': 'tools/puzzle/connect.py::main',
        'source': {'path': 'asic-puzzle-2026/warmup/04_final.gds', 'sha256': sha256(WU_GDS)},
        'engine': {'name': 'klayout LayoutToNetlist', 'version': db.__version__
                   if hasattr(db, '__version__') else 'n/a',
                   'configured_from': ['recon/derived/layers.json', 'recon/derived/via_pairs.json'],
                   'conductors': conductors, 'via_rules': len(a2['pairs'])},
        'totals': {'placements': len(placements), 'pins_probed': len(probes),
                   'pins_without_conductor_geometry': missing,
                   'probe_errors': probe_errors,
                   'pins_unconnected_by_engine': len(unassigned),
                   'our_nets': len(ours_f), 'reference_nets': len(theirs),
                   'our_terminals': sum(len(v) for v in ours_f.values()),
                   'reference_terminals': sum(len(v) for v in theirs.values())},
        'comparison': {'equivalent': equivalent,
                       'signatures_only_in_ours': sum(only_ours.values()),
                       'signatures_only_in_reference': sum(only_theirs.values()),
                       'only_in_reference': [sorted(s) for s in list(only_theirs)[:20]],
                       'only_in_ours': [sorted(s) for s in list(only_ours)[:20]]},
        'our_partition': {str(k): sorted(v) for k, v in sorted(
            ours_f.items(), key=lambda kv: -len(kv[1]))},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=False) + '\n',
                   encoding='utf-8', newline='\n')
    print(f'\nwritten: {OUT.relative_to(ROOT)}')
    print()
    print('B2 CONNECTIVITY SPIKE: ' + ('PASS -- the engine reproduces the reference '
                                     'partition' if equivalent
                                     else 'FAIL -- see the differing signatures above'))
    return 0 if equivalent else 1


# ---------------------------------------------------------------------------------
# B3 -- full connectivity on the puzzle
# ---------------------------------------------------------------------------------
def stage_nets() -> int:
    """B3: the same engine, on the real thing.

    Pins are NOT mapped to nets here (that is B4). What B3 establishes is that the
    extraction is *complete and attributable*: every conductor polygon on the die belongs
    to exactly one net, and the largest nets are identified as supply **by the pin names
    they carry**, not by being the biggest -- the plan's stronger method. Identification by
    size alone would be satisfied by two huge signal nets.
    """
    a1, a2 = read_json(A1), read_json(A2)
    inv = read_json(INV)
    pins = read_json(A4)
    insts = read_json(ROOT / 'recon' / 'derived' / 'instances.json')
    per_um = int(inv['gds']['dbu_per_um'])
    conductors = sorted({p for r in a2['pairs'] for p in r['connects']})
    cuts = sorted({r['cut'] for r in a2['pairs']})

    t0 = time.time()
    ly = db.Layout()
    ly.read(str(ROOT / 'asic-puzzle-2026' / 'puzzle.gds'))
    top = ly.top_cell()

    def lnum(pair):
        l, dt = (int(x) for x in pair.split('/'))
        return ly.layer(l, dt)

    l2n = db.LayoutToNetlist(db.RecursiveShapeIterator(
        ly, top, [lnum(p) for p in conductors + cuts]))
    reg = {p: l2n.make_layer(lnum(p)) for p in conductors + cuts}
    for c in conductors:
        l2n.connect(reg[c])
    for r in a2['pairs']:
        a, b = r['connects']
        l2n.connect(reg[a], reg[r['cut']])
        l2n.connect(reg[r['cut']], reg[b])
    l2n.extract_netlist()
    nl = l2n.netlist()
    t_extract = time.time() - t0
    print(f'design                             : {top.name}, {ly.dbu and 1/ly.dbu} dbu/um')
    print(f'conductor layers / via rules        : {len(conductors)} / {len(a2["pairs"])}')
    print(f'extraction                          : {t_extract:.1f}s, '
          f'{sum(1 for _ in nl.each_circuit())} circuits')

    tops = [c for c in nl.each_circuit() if c.name == top.name]
    if len(tops) != 1:
        print(f'FAIL: expected exactly one circuit named {top.name!r}, got {len(tops)}')
        return 1
    tc = tops[0]
    nets = list(tc.each_net())
    print(f'top circuit                         : {tc.name!r}, {len(nets)} nets, '
          f'{sum(1 for _ in tc.each_subcircuit())} subcircuits')

    # ---- 1. every conductor shape belongs to exactly one net ---------------------
    # Done by probing every individual shape, which is what the plan actually asks for.
    # The tempting shortcut -- compare a sum of per-net polygon counts against the layer's
    # polygon count -- does not work: `shapes_of_net` returns *per-net merged* regions
    # while a layer's region is not merged the same way, so the two counts are not
    # comparable (measured: 13029 vs 16869 on 67/20, and a set-difference attempt across
    # the hierarchy flattened the regions and reported thousands of phantom orphans).
    # Probing needs an interior point of each shape, and a bounding-box centre is not one
    # -- B2 already lost 32 pins to exactly that -- so the search below tries the centre,
    # then the shoelace centroid, then points stepped in from each vertex.
    def interior_point(poly):
        bb = poly.bbox()
        centre = bb.center()
        if poly.inside(centre):
            return centre
        pts = [(p.x, p.y) for p in poly.each_point_hull()]
        # shoelace centroid
        a2 = cx = cy = 0
        for i in range(len(pts)):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % len(pts)]
            cr = x0 * y1 - x1 * y0
            a2 += cr
            cx += (x0 + x1) * cr
            cy += (y0 + y1) * cr
        if a2:
            cand = db.Point(int(cx / (3 * a2)), int(cy / (3 * a2)))
            if poly.inside(cand):
                return cand
            # step from each vertex toward the centroid
            gx, gy = int(cx / (3 * a2)), int(cy / (3 * a2))
            for vx, vy in pts:
                for f in (200, 100, 50, 20, 10, 5):
                    cand = db.Point(vx + (gx - vx) // f, vy + (gy - vy) // f)
                    if poly.inside(cand):
                        return cand
        return None

    layer_total, layer_assigned, orphan_shapes, no_point = {}, {}, [], []
    per_net_shapes = {p: Counter() for p in conductors}
    t_shapes = time.time()
    for p in conductors:
        idx = lnum(p)
        n_shapes = assigned_here = 0
        it = top.begin_shapes_rec(idx)
        while not it.at_end():
            sh = it.shape()
            t = it.trans()
            # A recursive shape's geometry is in its own cell's coordinates; without the
            # iterator's transform every subcell shape would be probed at local coordinates
            # (measured: 31844 phantom "orphans" whose points were all under 1 um).
            poly = sh.polygon
            if poly is not None and not t.is_unity():
                poly = poly.transformed(t)
            box = sh.bbox().transformed(t)
            n_shapes += 1
            pt = interior_point(poly) if poly is not None else box.center()
            if pt is None:
                no_point.append(f'{p}@{box}')
            else:
                net = l2n.probe_net(reg[p], pt)
                if net is None:
                    orphan_shapes.append({'layer': p, 'point_dbu': [pt.x, pt.y],
                                          'bbox': str(box)})
                else:
                    assigned_here += 1
                    per_net_shapes[p][net.cluster_id] += 1
            it.next()
        layer_total[p], layer_assigned[p] = n_shapes, assigned_here
    t_shapes = time.time() - t_shapes
    print()
    print('every conductor shape probed for its net')
    print(f"  {'layer':<9}{'shapes':>9}{'assigned':>10}{'orphan':>8}")
    recon_ok = True
    for p in conductors:
        orph = layer_total[p] - layer_assigned[p]
        recon_ok &= orph == 0
        print(f'  {p:<9}{layer_total[p]:>9}{layer_assigned[p]:>10}{orph:>8}')
    print(f'  total conductor shapes            : {sum(layer_total.values())}')
    print(f'  shapes with no interior point     : {len(no_point)}  (unprobeable, not orphans)')
    for s in no_point[:4]:
        print(f'      {s}')
    print(f'  orphan shapes (in no net)         : {len(orphan_shapes)}')
    for s in orphan_shapes[:4]:
        print(f'      {s}')
    print(f'  probed in                         : {t_shapes:.1f}s')
    print(f'  every conductor shape is in exactly one net : {recon_ok}')

    # ---- 2. identify the supply nets by pin name, not by size --------------------
    ALLOW = {'rot000': (1, 1), 'rot000_mirror': (1, -1),
             'rot180': (-1, -1), 'rot180_mirror': (-1, 1)}
    by_cluster = defaultdict(Counter)
    unassigned_by_key: Counter = Counter()
    probed = unassigned = 0
    t1 = time.time()
    for pl in insts['instances']:
        sx, sy = ALLOW[pl['kind']]
        ax, ay = pl['origin_dbu']
        for pin, info in pins['masters'][pl['master']]['pins'].items():
            if pin not in SUPPLY:
                continue
            cands = sorted({s['pair'] for s in info['shapes']} & set(conductors))
            got = None
            for pos in (info.get('positions_um') or []):
                lx, lyy = (I.dbu(v, per_um) for v in pos)
                pt = (sx * lx + ax, sy * lyy + ay)
                for pair in cands:
                    n = l2n.probe_net(reg[pair], db.Point(*pt))
                    if n is not None:
                        got = n.cluster_id
                        break
                if got is not None:
                    break
            probed += 1
            if got is None:
                unassigned += 1
                unassigned_by_key[(pin, pl['master'].replace(STD, ''))] += 1
            else:
                by_cluster[got][pin] += 1
    t_probe = time.time() - t1
    print()
    print(f'supply pins probed                  : {probed} in {t_probe:.1f}s '
          f'({unassigned} unassigned)')
    # A5 predicted exactly two reasons a pin has no routeable geometry: the VPB well ties
    # (68 per master kind) and the antenna diode's supplies. Anything outside those two
    # classes would be a real gap, so the two are separated rather than lumped.
    known_gap = {k: v for k, v in unassigned_by_key.items()
                 if k[0] == 'VPB' or 'diode_2' in k[1]}
    unexplained = {k: v for k, v in unassigned_by_key.items() if k not in known_gap}
    by_pin_name: Counter = Counter()
    for (pin, _m), c in unassigned_by_key.items():
        by_pin_name[pin] += c
    if unassigned:
        print(f'  by pin                            : {dict(by_pin_name)}')
    print(f'  in A5\'s two known gap classes      : {sum(known_gap.values())}'
          f'  (VPB well ties {sum(v for k, v in known_gap.items() if k[0] == "VPB")}, '
          f'diode supplies {sum(v for k, v in known_gap.items() if k[0] != "VPB")})')
    print(f'  outside the known gap classes      : {sum(unexplained.values())}'
          f'{" " + str(unexplained) if unexplained else ""}')

    ranked = sorted(nets, key=lambda n: -n.subcircuit_pin_count())
    top_nets = []
    print()
    print(f"  {'cluster':>8}{'subckt pins':>12}{'polygons':>10}  pin names carried")
    for n in ranked[:6]:
        names = by_cluster.get(n.cluster_id, Counter())
        polys = sum(per_net_shapes[p].get(n.cluster_id, 0) for p in conductors)
        top_nets.append({'cluster': n.cluster_id, 'subcircuit_pins': n.subcircuit_pin_count(),
                         'polygons': polys,
                         'supply_pins': dict(sorted(names.items())),
                         'only_supply_pins': set(names) <= SUPPLY and bool(names)})
        print(f'  {n.cluster_id:>8}{n.subcircuit_pin_count():>12}{polys:>10}  '
              f'{dict(sorted(names.items())) if names else "(no supply pin sampled)"}')

    biggest = [t for t in top_nets if t['only_supply_pins']]
    # the two largest nets must be supply nets, and carry no functional pin name
    largest_two_are_supply = all(t['only_supply_pins'] for t in top_nets[:2])
    supply_names = sorted({p for t in top_nets[:2] for p in t['supply_pins']})
    print()
    print(f'  the two largest nets are supply nets : {largest_two_are_supply}')
    print(f'  supply pin names they carry          : {supply_names}')

    result = {
        'generated_by': 'tools/puzzle/connect.py::stage_nets',
        'source': {'path': 'asic-puzzle-2026/puzzle.gds', 'sha256': sha256(
            ROOT / 'asic-puzzle-2026' / 'puzzle.gds')},
        'engine': {'name': 'klayout LayoutToNetlist', 'conductors': conductors,
                   'via_rules': len(a2['pairs']),
                   'configured_from': ['recon/derived/layers.json',
                                       'recon/derived/via_pairs.json']},
        'totals': {'nets': len(nets), 'subcircuits': sum(1 for _ in tc.each_subcircuit()),
                   'conductor_shapes': sum(layer_total.values()),
                   'shapes_assigned_to_a_net': sum(layer_assigned.values()),
                   'orphan_shapes': len(orphan_shapes),
                   'shapes_without_interior_point': len(no_point),
                   'every_shape_in_exactly_one_net': recon_ok,
                   'supply_pins_probed': probed, 'supply_pins_unassigned': unassigned,
                   'supply_unassigned_by_pin': dict(by_pin_name),
                   'supply_unassigned_in_known_gap_classes': sum(known_gap.values()),
                   'supply_unassigned_unexplained': sum(unexplained.values())},
        'per_layer_shapes': {p: {'shapes': layer_total[p], 'assigned': layer_assigned[p]}
                             for p in conductors},
        'orphan_examples': orphan_shapes[:10],
        'unprobeable_examples': no_point[:10],
        'largest_nets': top_nets,
        'supply_identification': {
            'method': 'probe every supply pin of every instance and read the pin names the '
                      'net carries, rather than dropping the two biggest nets',
            'largest_two_are_supply': largest_two_are_supply,
            'supply_pin_names': supply_names,
            'nets_carrying_only_supply_pins': len(biggest),
        },
    }
    OUT_NETS.parent.mkdir(parents=True, exist_ok=True)
    OUT_NETS.write_text(json.dumps(result, indent=2, sort_keys=False) + '\n',
                        encoding='utf-8', newline='\n')
    print(f'\nwritten: {OUT_NETS.relative_to(ROOT)}')
    print()
    ok = recon_ok and largest_two_are_supply and not unexplained
    print('B3 FULL CONNECTIVITY: ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
