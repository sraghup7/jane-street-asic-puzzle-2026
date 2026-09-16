#!/usr/bin/env python3
"""check_stepF6.py -- the F6 gate: is the undriven-net finding still true, and still packaged?

F6's verify clause is "the artifact regenerates byte-identically and every claim in it is re-derived by
the gate". Three claims matter, and each is re-measured here rather than read:

* **the net is recovered, not transcribed.** The engine is rebuilt from A1/A2 -- the same call the
  pipeline makes -- and the two pin coordinates B4 recorded are probed again. If a future change moved
  a terminal or renumbered nets, the artifact's cluster id would no longer be reproducible.
* **the decisive refutation is re-run.** Every *cut* overlapping the wire is enumerated and filed by
  net. A cut over the wire belonging to another net hides a driver only if it also touches a wire shape
  on a conductor **its own A2 rule joins**; the check asserts no such cut exists, which is the whole
  difference between "the layout leaves the net floating" and "our extraction dropped a merge". The
  earlier draft of this gate tested against every wire shape regardless of layer, which is how a legal
  underlap would have been mis-read as a merge -- the filter is the check.
* **the message tie is re-asked.** The E2 family is re-derived, its first usable board is fed to the
  evaluator with net 806 forced to 0 and to 1, and both ties must differ from the published string and
  from each other. A tie that started printing `TWO NOT TOUCH` would make `docs/net806.md` and the
  plan's E2 outcome block false, and this is where that fires.

It also holds the artifact to the pipeline's conventions: byte-identical regeneration into scratch (so a
stale artifact fails rather than certifying itself), no timings, and the cross-checks that tie it to
B3's census and B5's enumeration.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from klayout import db                                    # noqa: E402
from tools.checks import _regen                           # noqa: E402
from tools.puzzle import connect as C                     # noqa: E402
from tools.puzzle import confirm as K                     # noqa: E402
from tools.puzzle import net806 as N                      # noqa: E402
from tools.puzzle import verdict as V                     # noqa: E402

ART = ROOT / 'recon' / 'derived' / 'net806.json'
D = ROOT / 'recon' / 'derived'
WINDOW_MARGIN_DBU = 20000      # the pins' bbox grown by 20 um: the window the second census reads
MIN_SHAPES = 8                 # floors, so an empty or tiny net cannot satisfy the checks below
MIN_LAYERS = 4
MIN_NET_TO_PIN_DBU = 1000      # a tie would have to *touch* a 4.4 um wire, not sit a micron away

checks: list[dict] = []


def check(name: str, passed: bool, detail: str = '') -> None:
    checks.append({'check': name, 'passed': bool(passed), 'detail': detail})


def window_of(points: list[list[int]]):
    """The box a second, independent census searches: the pins' extent grown by a fixed margin."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs) - WINDOW_MARGIN_DBU, min(ys) - WINDOW_MARGIN_DBU,
            max(xs) + WINDOW_MARGIN_DBU, max(ys) + WINDOW_MARGIN_DBU)


def census_in_window(top, ly, reg, l2n, layers: list[str], window, net: int) -> list[dict]:
    """Shapes inside the window the engine files under `net`, found by a different route.

    The stage files *every* shape on the die; this reads only a window around the two pins, skipping a
    shape whose bbox misses it without probing. The two methods agree only if the net's geometry is
    entirely inside the window -- which is exactly what the equality assertions test -- and the window
    is derived from the pins by a fixed rule, not from the answer.
    """
    win = db.Box(*[int(v) for v in window])
    found: list[dict] = []
    for p in layers:
        it = top.begin_shapes_rec(C.lindex(ly, p))
        while not it.at_end():
            sh = it.shape()
            if not sh.bbox().overlaps(win):
                it.next()
                continue
            poly = sh.polygon
            pt = N.interior_point(poly) if poly is not None else sh.bbox().center()
            n = l2n.probe_net(reg[p], pt)
            if n is not None and int(n.cluster_id) == net:
                found.append({'layer': p, 'bbox_dbu': N.box_list(sh.bbox()), 'poly': poly})
            it.next()
    return found


def extent(boxes: list[list[int]]):
    """The union extent of DBU boxes, as a list of ints -- no KLayout Box arithmetic involved."""
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def main() -> int:
    if not ART.exists():
        print(f'missing {ART.relative_to(ROOT).as_posix()}')
        print('run: python -m tools.puzzle net806')
        return 1
    art = json.loads(ART.read_text(encoding='utf-8'))
    before = ART.read_bytes()

    # ---- the artifact is what the stage produces -------------------------------------------
    rc, produced, _out = _regen.regenerate('tools.puzzle.net806', ('OUT',), 'net806')
    check('the stage runs and writes an artifact', rc == 0 and produced is not None,
          f'exit {rc}, {len(produced or b"")} bytes')
    check('the committed artifact regenerates byte-identically',
          produced == before,
          f'committed {len(before)} bytes, regenerated {len(produced or b"")}'
          + ('' if produced == before else ' -- stale: re-run python -m tools.puzzle net806'))
    check('regenerating left the committed artifact untouched', ART.read_bytes() == before,
          'the hermetic scratch redirect worked')

    missing = [p for p in art['inputs'] if not (ROOT / p).exists()]
    check('every input the artifact names exists', not missing,
          ', '.join(missing) or f'{len(art["inputs"])} inputs')

    # ---- the net, recovered again from the geometry ----------------------------------------
    net = art['net']['cluster']
    points = [t['point_dbu'] for t in art['net']['terminals']]
    pin_net = json.loads((D / 'pin_net.json').read_text(encoding='utf-8'))
    on_net = N.pins_on_net(pin_net)
    recorded = {(t['instance'], t['pin'], t['layer'], t['x'], t['y']) for t in on_net}
    in_art = {(t['instance'], t['pin'], t['layer'], t['point_dbu'][0], t['point_dbu'][1])
              for t in art['net']['terminals']}
    check('the artifact records exactly the terminals B4 placed on the net',
          recorded == in_art and len(recorded) == 2,
          f'{len(in_art)} recorded, {len(recorded)} in pin_net.json')

    a2 = json.loads((D / 'via_pairs.json').read_text(encoding='utf-8'))
    conductors = sorted({p for r in a2['pairs'] for p in r['connects']})
    cuts = sorted({r['cut'] for r in a2['pairs']})
    cuts_on = {r['cut']: r['connects'] for r in a2['pairs']}
    ly, top, l2n, _nl, reg = C.build_engine(ROOT / 'asic-puzzle-2026' / 'puzzle.gds',
                                            conductors, cuts, a2['pairs'])

    probed = set()
    for t in art['net']['terminals']:
        n = l2n.probe_net(reg[t['layer']], db.Point(t['point_dbu'][0], t['point_dbu'][1]))
        probed.add(None if n is None else int(n.cluster_id))
    check('probing the recorded coordinates reproduces the same net',
          probed == {net}, f'{sorted(probed, key=str)} for net {net}')

    outputs = set(json.loads((D / 'netlist_check.json').read_text(encoding='utf-8'))
                  ['direction']['outputs'])
    live_terminals = N.terminals(on_net, outputs)
    check('no terminal of the net is a driver by the chip s own label vocabulary',
          live_terminals == art['net']['terminals']
          and not art['net']['drivers_by_label_vocabulary'],
          f'{len(live_terminals)} terminals, '
          f'{len(art["net"]["drivers_by_label_vocabulary"])} drivers')

    # ---- the net's own geometry, by a second method ----------------------------------------
    found = census_in_window(top, ly, reg, l2n, conductors + cuts, window_of(points), net)
    by_layer = {p: sum(1 for f in found if f['layer'] == p) for p in sorted({f['layer'] for f in found})}
    bbox = extent([f['bbox_dbu'] for f in found]) if found else None
    sh = art['net']['shapes']
    check('the net s geometry is re-measured, in a window around its pins',
          len(found) == sh['total'] and by_layer == sh['by_layer'] and bbox == sh['bbox_dbu'],
          f'{len(found)} shapes {by_layer} bbox {bbox} vs artifact '
          f'{sh["total"]} {sh["by_layer"]} {sh["bbox_dbu"]}')
    check('floors: the net is a routed wire, not a stub, and it spans several layers',
          len(found) >= MIN_SHAPES and len(by_layer) >= MIN_LAYERS and len(sh['own_cuts']) >= 2,
          f'{len(found)} shapes on {len(by_layer)} layers, {len(sh["own_cuts"])} own vias')

    # ---- the decisive test: no cut over the wire could have hidden a driver -----------------
    wire = db.Region()
    for f in found:
        if f['poly'] is not None:
            wire.insert(f['poly'])
    live_overlaps, could_merge = [], []
    for cut in cuts:
        inter = reg[cut] & wire
        if inter.is_empty():
            continue
        for poly in inter.each():
            n = l2n.probe_net(reg[cut], N.interior_point(poly))
            got = None if n is None else int(n.cluster_id)
            # A merge is possible only through a conductor this cut's own A2 rule joins: a via of
            # another layer pair passing inside the wire's rectangle has no shared layer to merge on.
            joins = set(cuts_on.get(cut, []))
            shares = any(not (db.Region(poly) & db.Region(f['poly'])).is_empty()
                         for f in found if f['poly'] is not None and f['layer'] in joins)
            entry = {'layer': cut, 'bbox_dbu': N.box_list(poly.bbox()), 'net': got,
                     'same_net': got == net,
                     'rule_conductors': sorted(joins),
                     'wire_shapes_on_those_conductors_overlap_it': shares,
                     'merges_with_a_wire_shape': shares}
            live_overlaps.append(entry)
            if got != net and shares:
                could_merge.append(entry)

    def ident(o: dict) -> str:
        """Which cut, where, on which net -- the identity of an overlap, without the rule fields."""
        return json.dumps([o['layer'], o['bbox_dbu'], o['net']])

    art_same = sorted(ident(o) for o in art['cut_overlap']['overlapping'] if o['same_net'])
    live_same = sorted(ident(o) for o in live_overlaps if o['same_net'])
    art_else = sorted(ident(o) for o in art['cut_overlap']['overlapping'] if not o['same_net'])
    live_else = sorted(ident(o) for o in live_overlaps if not o['same_net'])
    check('every cut overlapping the wire is re-filed by net',
          live_same == art_same and live_else == art_else,
          f'{len(live_overlaps)} cuts live ({len(live_same)} same-net, {len(live_else)} elsewhere) vs '
          f'{len(art["cut_overlap"]["overlapping"])} in the artifact')
    check('NO cut over the wire could merge, so no missed merge hides a driver',
          not could_merge and not art['cut_overlap']['merges_that_could_hide_a_driver'],
          f'{len(could_merge)} merging cuts found live; the documented one is on '
          f'{[sorted(o["rule_conductors"]) for o in art["cut_overlap"]["overlapping"] if not o["same_net"]]}'
          f' -- layers the wire does not occupy there')

    # ---- the constant-tie hypothesis, re-measured -------------------------------------------
    tie = N.constant_tie(pin_net, on_net, outputs)['nearest_tie']
    check('the nearest constant cell is too far away to be a tie, and the distance is reproducible',
          tie['distance_dbu'] == art['constant_tie']['nearest_tie']['distance_dbu']
          and tie['distance_dbu'] >= MIN_NET_TO_PIN_DBU,
          f'{tie["constant_cell"]}.{tie["constant_pin"]} at {tie["distance_dbu"]} dbu '
          f'(artifact {art["constant_tie"]["nearest_tie"]["distance_dbu"]})')

    # ---- the message tie, asked live --------------------------------------------------------
    class_of, src = V.region_partition()
    fam = K.swap_family(class_of, len(src['flops']))
    usable = fam['usable']
    check('the E2 family is re-derived and has usable boards', len(usable) >= 1,
          f'{fam["candidates"]} candidates, {len(usable)} usable')
    grid = usable[0]['grid'] if usable else art['message_tie']['board_grid']
    check('the artifact s board is the first usable board of that family',
          grid == art['message_tie']['board_grid'], 'same board, re-derived')

    m = V.Machine(cycles=V.MESSAGE_CYCLES)
    rep = m.reference_replay()
    check('the evaluator still reproduces example_inputs.vcd',
          rep['mismatches'] == 0 and rep['unknown_bits'] == 0,
          f'{rep["mismatches"]} mismatches, {rep["unknown_bits"]} unknown bits')
    reading = K.ask(m, grid)
    mt = art['message_tie']
    check('the unforced reading and its unknown positions are re-derived',
          reading['text'] == mt['unforced_reading']
          and sorted(reading['forced']['positions_varying']) == sorted(mt['positions']),
          f'{reading["text"]!r} unknown at {sorted(reading["forced"]["positions_varying"])}')
    tie0, tie1 = reading['forced']['0'], reading['forced']['1']
    check('the two ties are re-derived and match the artifact',
          [tie0, tie1] == [mt['tie_0'], mt['tie_1']], f'806=0 {tie0!r}, 806=1 {tie1!r}')
    check('both constant ties differ from the published string (a constant cannot be the lost wire)',
          tie0 != mt['published_string'] and tie1 != mt['published_string'], f'{tie0!r} / {tie1!r}')

    # ---- an existing signal, not a constant: re-derived, and rewired live once more -----------
    ns = art['message_tie']['nearby_signals']
    check('at least one nearby existing signal reproduces TWO NOT TOUCH on every within-cap board',
          len(ns['reproduce_on_all_boards']) >= 1 and 789 in ns['reproduce_on_all_boards'],
          f"{len(ns['candidates'])} candidates within {ns['radius_um']} um; "
          f"all-board reproducers {ns['reproduce_on_all_boards']}")
    check('the recorded conclusion is that the layout does not determine the net',
          art['conclusion'].startswith('undriven in the layout'), art['conclusion'])

    consumers = [(k, g) for k, g in enumerate(m.nl.comb) if any(x == N.NET for _, x in g[3])]
    saved = [g for _k, g in consumers]
    for k, (inst, op, tree, ins) in consumers:
        m.nl.comb[k] = (inst, op, tree, [(p, 789 if x == N.NET else x) for p, x in ins])
    rewired = K.ask(m, grid)
    for (k, _g), orig in zip(consumers, saved):
        m.nl.comb[k] = orig
    check('live re-derivation: rewiring 806 to n789 reproduces TWO NOT TOUCH exactly',
          rewired['text'] == mt['published_string'] and not rewired['unknown_bytes'],
          f"{rewired['text']!r}")

    # ---- cross-checks against the artifacts that own those numbers --------------------------
    nets = json.loads((D / 'nets.json').read_text(encoding='utf-8'))['totals']
    check('the conductor census agrees with B3 s own count',
          art['die_census']['conductor_shapes'] == nets['conductor_shapes']
          and art['die_census']['nets'] == nets['nets'],
          f'{art["die_census"]["conductor_shapes"]} conductor shapes / {art["die_census"]["nets"]} '
          f'nets vs B3 {nets["conductor_shapes"]} / {nets["nets"]}')
    nc = json.loads((D / 'netlist_check.json').read_text(encoding='utf-8'))
    enumer = nc['direction']['undriven_nets']
    check('B5 enumerates exactly this net as the design s one undriven net',
          nc['totals']['undriven_nets'] == 1 and len(enumer) == 1
          and enumer[0]['cluster'] == net and enumer[0]['terminal_directions'] == ['input'],
          f'{nc["totals"]["undriven_nets"]} undriven net, cluster {enumer[0]["cluster"]}, '
          f'directions {enumer[0]["terminal_directions"]}')

    # ---- no clocks --------------------------------------------------------------------------
    clocks = [k for k in ('seconds', 'duration', 'elapsed', 'timestamp', 'runtime', 'time')
              if f'"{k}"' in json.dumps(art)]
    check('the artifact carries no timings (a clock would break byte-identical regeneration)',
          not clocks, ', '.join(clocks) or 'none')

    failed = [c for c in checks if not c['passed']]
    width = max(len(c['check']) for c in checks)
    for c in checks:
        print(f'  {"PASS" if c["passed"] else "FAIL"}  {c["check"]:<{width}}  {c["detail"]}')
    print()
    print(f'{len(checks) - len(failed)}/{len(checks)} checks passed')
    if failed:
        print('STEP F6 GATE: FAIL')
        return 1
    print('STEP F6 GATE: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
