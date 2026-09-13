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


if __name__ == '__main__':
    import sys
    raise SystemExit(main(sys.argv[1:]))
