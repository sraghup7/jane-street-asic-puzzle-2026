#!/usr/bin/env python3
"""net806.py -- F6: the chip's one undriven net as a *stage*, not a paragraph.

**Why this step exists.** Net 806 was investigated after the Step-5 freeze, in the throwaway tree, and
the result was written into `docs/net806.md` with its scripts in `recon/scratch/`. That is the one
place in this repository where a claim had no gate, no regenerable artifact and no path a fresh clone
could follow -- the packaging F1/F2 exist to prevent. F6 promotes the experiment into the pipeline:
this module re-derives the geometry and the message tie from the committed artifacts and the upstream
layout, writes `recon/derived/net806.json`, and `check_stepF6.py` re-derives what the artifact asserts.
The conclusion is unchanged; what changes is that it is now checkable.

**The question.** On the `TWO NOT TOUCH` path one printed character is indeterminate, and it follows
this net. Two explanations had to be separated: the layout really does leave the net floating, or our
extraction missed something (a constant tie, a mis-assigned pin, a via merge it did not make). Three
hypotheses, each with a geometric test, none of them answerable by reading the artifact alone:

  1. **a constant cell ties it** -- measured as the nearest `conb` output pin's distance to the net's
     own pins, in DBU;
  2. **our pin geometry mis-assigns its terminals** -- the engine is rebuilt from A1/A2 and the net is
     recovered by probing the two recorded pin coordinates, so the number in `pin_net.json` is
     reproduced by a fresh run rather than trusted;
  3. **a missed via merge hides a driver** -- every *cut* shape overlapping the wire is enumerated and
     filed by the net the engine gives it. A cut landing on the wire and belonging to another net is
     the only defect that could hide a driver, and the decisive test is whether that cut shares a
     conductor with the wire through a rule A2 proved: if it lies on layers the wire does not occupy,
     it is a legitimate underlap and there is nothing to merge.

**What is not claimed.** That the published string is wrong. The two ties are one character off in
opposite directions, so no single value of this net prints it -- which is itself the finding: the
character is decided by a floating node, and the evidence now says so rather than leaving it open.

    python -m tools.puzzle net806          # F6 -> recon/derived/net806.json
"""
from __future__ import annotations

import hashlib
import json
import sys
from math import hypot
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from klayout import db                                    # noqa: E402
from tools.puzzle import connect as C                     # noqa: E402
from tools.puzzle import confirm as K                     # noqa: E402
from tools.puzzle import verdict as V                     # noqa: E402

OUT = ROOT / 'recon' / 'derived' / 'net806.json'
PUZZLE = ROOT / 'asic-puzzle-2026' / 'puzzle.gds'
A1 = ROOT / 'recon' / 'derived' / 'layers.json'
A2 = ROOT / 'recon' / 'derived' / 'via_pairs.json'
PINNET = ROOT / 'recon' / 'derived' / 'pin_net.json'
CHECK = ROOT / 'recon' / 'derived' / 'netlist_check.json'
E2ART = ROOT / 'recon' / 'derived' / 'e2_messages.json'
WIRE_LAYER = '67/20'                                      # the layer the net's own pins sit on (A3/A5)
NET = 806                                                 # B5's one structurally undriven net
PUBLISHED = 'TWO NOT TOUCH'                               # the message this net perturbs (E2)


def read_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding='utf-8'))


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def box_list(box) -> list[int]:
    """A KLayout box as plain DBU integers, so the artifact is JSON and diffable."""
    return [int(box.left), int(box.bottom), int(box.right), int(box.top)]


def interior_point(poly):
    """A point strictly inside a polygon, for `probe_net` -- never a bounding-rect centre.

    B2 lost 32 pins to centre probes that landed in a comb's gap, so this tries the centre, then a
    hull vertex nudged toward it. `connect.stage_nets` carries the same idea nested inside a stage
    function, where it cannot be imported; this is the module-level copy, and it is the only reason
    two implementations of it exist.
    """
    if poly is None:
        return None
    centre = poly.bbox().center()
    if poly.inside(centre):
        return centre
    for pt in poly.each_point_hull():
        cand = db.Point((pt.x * 3 + centre.x) // 4, (pt.y * 3 + centre.y) // 4)
        if poly.inside(cand):
            return cand
    return centre


def terminals(on_net: list[dict], outputs: set[str]) -> list[dict]:
    """The net's terminals, with each pin's direction from the chip's own label vocabulary (A3/B5)."""
    out = []
    for rec in on_net:
        out.append({
            'instance': rec['instance'], 'master': rec['master'], 'pin': rec['pin'],
            'layer': rec['layer'], 'point_dbu': [rec['x'], rec['y']],
            'direction': 'output' if f"{rec['master']}::{rec['pin']}" in outputs else 'input',
        })
    return sorted(out, key=lambda t: (t['instance'], t['pin']))


def pins_on_net(pin_net: dict) -> list[dict]:
    """Every pin `pin_net.json` places on net 806, as (instance, master, pin, layer, x, y)."""
    found = []
    for inst, rec in pin_net['instances'].items():
        for pin, v in rec['pins'].items():
            if v is None:
                continue
            cluster, layer, x, y = v
            if cluster == NET:
                found.append({'instance': inst, 'master': rec['master'], 'pin': pin,
                              'layer': layer, 'x': int(x), 'y': int(y)})
    return sorted(found, key=lambda t: (t['instance'], t['pin']))


def constant_tie(pin_net: dict, on_net: list[dict], outputs: set[str]) -> dict:
    """Hypothesis 1: is the net tied to a constant cell? Measured as the nearest `conb` output pin.

    Distance is pin-to-pin in DBU, the metric `docs/net806.md` reports, so the number in that note
    and the number here are the same measurement rather than two similar ones. A tie would have to be
    a *connection*, and a connection between two pins of a 4.4 um wire cannot be a micron away.
    """
    conbs = []
    for inst, rec in pin_net['instances'].items():
        if not rec['master'].replace('sky130_fd_sc_hd__', '').startswith('conb'):
            continue
        for pin, v in rec['pins'].items():
            if v is None or f"{rec['master']}::{pin}" not in outputs:
                continue
            conbs.append({'instance': inst, 'pin': pin, 'point_dbu': [int(v[2]), int(v[3])]})
    best = None
    for c in conbs:
        for t in on_net:
            d = hypot(c['point_dbu'][0] - t['x'], c['point_dbu'][1] - t['y'])
            if best is None or d < best['distance_dbu']:
                best = {'distance_dbu': round(d), 'constant_cell': c['instance'],
                        'constant_pin': c['pin'], 'net_pin': f"{t['instance']}.{t['pin']}"}
    return {'constant_cells': len({c['instance'] for c in conbs}), 'constant_outputs': len(conbs),
            'nearest_tie': best}


def measure() -> dict:
    """Rebuild the engine, recover the net by probing, and run the three tests on it."""
    a2 = read_json(A2)
    pin_net = read_json(PINNET)
    check = read_json(CHECK)
    outputs = set(check['direction']['outputs'])
    on_net = pins_on_net(pin_net)
    if not on_net:
        raise SystemExit(f'no pin of recon/derived/pin_net.json is on net {NET}')
    conductors = sorted({p for r in a2['pairs'] for p in r['connects']})
    cuts = sorted({r['cut'] for r in a2['pairs']})
    cuts_on = {r['cut']: r['connects'] for r in a2['pairs']}

    ly, top, l2n, nl, reg = C.build_engine(PUZZLE, conductors, cuts, a2['pairs'])

    # ---- 1. recover the net from the geometry, at the coordinates B4 recorded -------------
    probed = []
    for t in on_net:
        n = l2n.probe_net(reg[t['layer']], db.Point(t['x'], t['y']))
        probed.append({'instance': t['instance'], 'pin': t['pin'],
                       'cluster': None if n is None else int(n.cluster_id)})
    if len({p['cluster'] for p in probed}) != 1 or probed[0]['cluster'] != NET:
        raise SystemExit(f'probing the recorded pin coordinates did not reproduce net {NET}: {probed}')

    # ---- 2. file every shape on the die by the net the engine gives it ---------------------
    # Counted by category as well as in total: B3 measured 40 360 *conductor* shapes, so a census
    # that adds the cut layers (which B3 does not probe) must say which part is which, or the two
    # numbers look like a disagreement instead of a cross-check.
    members, nets_seen = [], set()
    census = {'conductor_shapes': 0, 'cut_shapes': 0}
    for p in conductors + cuts:
        key = 'conductor_shapes' if p in conductors else 'cut_shapes'
        it = top.begin_shapes_rec(C.lindex(ly, p))
        while not it.at_end():
            sh = it.shape()
            poly = sh.polygon
            pt = interior_point(poly) if poly is not None else sh.bbox().center()
            n = l2n.probe_net(reg[p], pt)
            census[key] += 1
            if n is not None:
                nets_seen.add(int(n.cluster_id))
                if int(n.cluster_id) == NET:
                    members.append({'layer': p, 'bbox_dbu': box_list(sh.bbox()), 'poly': poly})
            it.next()
    census['shapes'] = census['conductor_shapes'] + census['cut_shapes']
    census['nets'] = len(nets_seen)

    wire = db.Region()
    for m in members:
        if m['poly'] is not None:
            wire.insert(m['poly'])
    by_layer = {p: sum(1 for m in members if m['layer'] == p) for p in sorted({m['layer'] for m in members})}

    # ---- 3. every cut overlapping the wire, and whether it could merge --------------------
    # The decisive test. `reg[cut] & wire` finds cuts whose area lands on the wire; probing one gives
    # the net the engine files it under. A cut on another net is only a *missed merge* if it also
    # touches a wire shape on a conductor that cut's own A2 rule joins -- otherwise it is a via of a
    # different layer pair passing under (or over) this wire, which is legal and has nothing to merge.
    overlaps, same_net, other_net = [], 0, 0
    for cut in cuts:
        inter = reg[cut] & wire
        if inter.is_empty():
            continue
        for poly in inter.each():
            n = l2n.probe_net(reg[cut], interior_point(poly))
            got = None if n is None else int(n.cluster_id)
            if got == NET:
                same_net += 1
                overlaps.append({'layer': cut, 'bbox_dbu': box_list(poly.bbox()), 'net': NET,
                                 'same_net': True, 'merges_with_a_wire_shape': True})
                continue
            other_net += 1
            conducts = cuts_on.get(cut, [])
            shares = any(not (db.Region(poly) & db.Region(m['poly'])).is_empty()
                         for m in members if m['poly'] is not None and m['layer'] in conducts)
            overlaps.append({'layer': cut, 'bbox_dbu': box_list(poly.bbox()), 'net': got,
                             'same_net': False, 'rule_conductors': sorted(conducts),
                             'wire_shapes_on_those_conductors_overlap_it': shares,
                             'merges_with_a_wire_shape': shares})
    own_cuts = [o for o in overlaps if o['same_net']]

    # ---- 4. conductors on other layers overlapping the wire (crossings, not merges) --------
    crossings = []
    for p in conductors:
        if p == WIRE_LAYER:
            continue
        inter = reg[p] & wire
        if inter.is_empty():
            continue
        ids = set()
        polys = list(inter.each())
        for poly in polys:
            n = l2n.probe_net(reg[p], interior_point(poly))
            ids.add(None if n is None else int(n.cluster_id))
        crossings.append({'layer': p, 'overlapping_polygons': len(polys),
                          'nets': sorted(i for i in ids if i is not None),
                          'carries_this_net': NET in ids})

    # ---- 5. the message tie, through the E2 instrument -------------------------------------
    e2 = read_json(E2ART)
    grid = e2['results'][0]['grid']
    m = V.Machine(cycles=V.MESSAGE_CYCLES)
    reading = K.ask(m, grid)
    positions = sorted(reading['forced']['positions_varying']) if reading['forced'] else []
    tie = {
        'board_grid': grid,
        'unforced_reading': reading['text'],
        'unknown_bytes': reading['unknown_bytes'],
        'positions': positions,
        'tie_0': reading['forced']['0'] if reading['forced'] else None,
        'tie_1': reading['forced']['1'] if reading['forced'] else None,
        'published_string': PUBLISHED,
        'byte_exact': reading['text'][:len(PUBLISHED)] == PUBLISHED,
        'same_as_the_e2_artifact': (reading['text'] == e2['results'][0]['reading']['text']
                                    and positions == sorted(e2['undriven_net_in_the_message']['positions'])),
    }

    return {
        'generated_by': 'tools/puzzle/net806.py::stage_net806',
        'source': {'path': 'asic-puzzle-2026/puzzle.gds', 'sha256': sha256(PUZZLE)},
        'inputs': ['recon/derived/layers.json', 'recon/derived/via_pairs.json',
                   'recon/derived/pin_net.json', 'recon/derived/netlist_check.json',
                   'recon/derived/e2_messages.json'],
        'method': {
            'engine': 'connect.build_engine -- the same KLayout extraction the pipeline uses, '
                      'configured from A1/A2 alone, on a flattened layout so cluster_id is a key',
            'recovery': 'the net is recovered by probing the two pin coordinates B4 recorded, on '
                        'their own layer; the id in pin_net.json is reproduced, not trusted',
            'census': 'every shape on the die is filed by the net the engine assigns it, so the '
                      'wire\'s own geometry is measured rather than read',
            'missed_merge': 'a cut overlapping the wire and belonging to another net is a missed '
                            'merge only if it also touches a wire shape on a conductor that cut\'s '
                            'own A2 rule joins; a cut on a layer pair the wire does not occupy is a '
                            'legal underlap',
            'constant_tie': 'pin-to-pin DBU distance from every conb output to the net\'s pins -- '
                            'the metric docs/net806.md reports',
            'message_tie': 'tools/puzzle/confirm.ask on the first E2 board: the unforced reading, '
                           'then the same board with net 806 forced to 0 and to 1',
        },
        'net': {
            'cluster': NET,
            'terminals': terminals(on_net, outputs),
            'terminal_layers': sorted({t['layer'] for t in on_net}),
            'drivers_by_label_vocabulary': [f"{t['instance']}.{t['pin']}"
                                            for t in terminals(on_net, outputs)
                                            if t['direction'] == 'output'],
            'probe_reproduced_the_recorded_cluster': probed,
            'shapes': {'total': len(members), 'by_layer': by_layer,
                       'bbox_dbu': box_list(wire.bbox()) if members else None,
                       'own_cuts': own_cuts},
        },
        'die_census': census,
        'cut_overlap': {'same_net': same_net, 'other_net': other_net, 'overlapping': overlaps,
                        'merges_that_could_hide_a_driver':
                            [o for o in overlaps if o['merges_with_a_wire_shape'] and not o['same_net']]},
        'crossings': crossings,
        'constant_tie': constant_tie(pin_net, on_net, outputs),
        'message_tie': tie,
    }


def stage_net806() -> int:
    """F6: measure the chip's one undriven net, write `recon/derived/net806.json`, and state it."""
    if not PUZZLE.exists():
        print(f'missing {PUZZLE.relative_to(ROOT).as_posix()} -- the upstream layout is an input')
        return 1
    r = measure()
    sh, tie = r['net']['shapes'], r['message_tie']
    print(f'net {r["net"]["cluster"]}: {len(r["net"]["terminals"])} terminals, '
          f'{len(r["net"]["drivers_by_label_vocabulary"])} of them drivers by the label vocabulary')
    for t in r['net']['terminals']:
        print(f'  {t["instance"]:<7} {t["master"].replace("sky130_fd_sc_hd__", ""):<12} {t["pin"]:<3} '
              f'{t["layer"]} at {t["point_dbu"]}  ({t["direction"]})')
    print(f'  geometry    : {sh["total"]} shapes on {sorted(sh["by_layer"])}, '
          f'bbox {sh["bbox_dbu"]}, {len(sh["own_cuts"])} of its own vias')
    print(f'  die census  : {r["die_census"]["conductor_shapes"]} conductor + '
          f'{r["die_census"]["cut_shapes"]} cut shapes, {r["die_census"]["nets"]} nets')
    co = r['cut_overlap']
    print(f'  cut overlap : {co["same_net"]} same-net, {co["other_net"]} belonging elsewhere, '
          f'{len(co["merges_that_could_hide_a_driver"])} of which could merge')
    for o in co['overlapping']:
        if not o['same_net']:
            print(f'    {o["layer"]} {o["bbox_dbu"]} -> net {o["net"]}, rule joins '
                  f'{o["rule_conductors"]}, a wire shape on those conductors overlaps it: '
                  f'{o["wire_shapes_on_those_conductors_overlap_it"]}')
    ct = r['constant_tie']['nearest_tie']
    print(f'  constant tie: {r["constant_tie"]["constant_cells"]} conb cells, nearest output '
          f'{ct["constant_cell"]}.{ct["constant_pin"]} {ct["distance_dbu"]} dbu away')
    print(f'  message     : {tie["unforced_reading"]!r}, unknown at {tie["positions"]}; '
          f'806=0 -> {tie["tie_0"]!r}, 806=1 -> {tie["tie_1"]!r}')
    print(f'  published {tie["published_string"]!r} reproduced byte-for-byte: {tie["byte_exact"]}')
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(r, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(f'\nwritten: {OUT.relative_to(ROOT).as_posix()}')
    print(f'F6: {"PASS" if not co["merges_that_could_hide_a_driver"] and not tie["byte_exact"] else "FAIL"}')
    return 0 if not co['merges_that_could_hide_a_driver'] and not tie['byte_exact'] else 1


def main(argv: list[str] | None = None) -> int:
    stage = (argv or ['net806'])[0]
    if stage != 'net806':
        print(f'net806.py has no stage {stage!r}', file=sys.stderr)
        return 2
    return stage_net806()


if __name__ == '__main__':
    raise SystemExit(main())
