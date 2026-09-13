#!/usr/bin/env python3
"""netlist.py -- B4/B5: the netlist.

    python -m tools.puzzle pins-to-nets   ->  recon/derived/pin_net.json      (B4)
    python -m tools.puzzle netlist-check                                (B5, later)

B3 proved the extraction is *complete* -- every conductor shape on the die is in exactly one
net. B4 is the step that turns that into a **netlist** by attaching terminals to those nets:
for all 1618 placements, every pin of every instance is assigned to the net it lives on.

Three decisions, each chosen against a cheaper alternative that does not work:

* **Terminals are found by asking the engine, not by matching geometry.** A5 established that
  no pin in any master carries geometry on met2-met5, that li1 holds 489 of the 560 modelled
  pins and met1 only 20, and A1 established that li1 carries no top-level routing at all --
  so a cell pin and the wire it joins are *never on the same layer*. They are joined by a via
  instance, and the via is part of the net (plan sec.6.5). Any "pin rect overlaps wire
  polygon" test therefore finds almost nothing; `probe_net(point)` is the only correct
  question, and it is what B2 validated on the warm-up.

* **Probe points are label positions, probed only on their own pin's layers.** Two measured
  traps: a bounding-rect centre is not inside a comb pin (B2 lost 32 pins that way, so points
  come from `positions_um`, which A4 seeded *from* the labels), and probing a position on
  every layer the pin happens to use can land inside a different net's wire on another layer.
  So each position is probed only on the pairs whose shape actually contains it.

* **A pin's positions must agree, and a disagreement is reported, not averaged.** Every label
  position of one pin is on one electrical node by construction; positions landing on
  different nets would mean the pin model (A4) or the extraction has merged two nets, which is
  a real defect. A winner is still chosen deterministically so the stage produces an artifact,
  but the conflict is recorded and fails the stage.

`unassigned` is the count the plan asks for, and it is split: **0 for functional pins**, and
for supply pins only the two causes A5 predicted (the `VPB` well ties, which have no routeable
geometry, and the antenna diode's supplies). Anything outside those classes fails.
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path

from klayout import db

from tools.puzzle.connect import SUPPLY, build_engine, read_json, sha256
from tools.puzzle.instances import dbu

ROOT = Path(__file__).resolve().parents[2]

PUZZLE = ROOT / 'asic-puzzle-2026' / 'puzzle.gds'
A1 = ROOT / 'recon' / 'derived' / 'layers.json'
A2 = ROOT / 'recon' / 'derived' / 'via_pairs.json'
A4 = ROOT / 'recon' / 'derived' / 'pinmodel.json'
B1 = ROOT / 'recon' / 'derived' / 'instances.json'
INV = ROOT / 'recon' / 'inventory.json'
OUT = ROOT / 'recon' / 'derived' / 'pin_net.json'

STD = 'sky130_fd_sc_hd__'

# A placement maps a local point as (sx*x + ox, sy*y + oy). B1 fixed this convention by
# requiring transform(master_bbox) == global_bbox on all 1618 instances, and proved it against
# the warm-up DEF's orientation tokens independently.
ALLOW = {'rot000': (1, 1), 'rot000_mirror': (1, -1),
         'rot180': (-1, -1), 'rot180_mirror': (-1, 1)}


def gap_class(master: str, pin: str) -> str:
    """Which of A5's two predicted gap classes an unplaced pin falls in, or 'unexplained'.

    A5 forecast exactly two reasons a pin has no net, and this classification must not be
    widened quietly: a third class passing as 'known' is precisely the failure this guards.
    """
    if pin == 'VPB':
        return 'vpb_well_tie_no_routeable_geometry'
    if 'diode_2' in master.replace(STD, ''):
        return 'antenna_diode_supply'
    return 'unexplained'


def stage_pin_net() -> int:
    a2 = read_json(A2)
    a4 = read_json(A4)
    insts = read_json(B1)
    inv = read_json(INV)
    per_um = int(inv['gds']['dbu_per_um'])
    conductors = sorted({p for r in a2['pairs'] for p in r['connects']})
    cuts = sorted({r['cut'] for r in a2['pairs']})
    cset = set(conductors)

    t0 = time.time()
    ly, top, l2n, nl, reg = build_engine(PUZZLE, conductors, cuts, a2['pairs'])
    t_engine = time.time() - t0

    circs = list(nl.each_circuit())
    if len(circs) != 1 or circs[0].name != top.name:
        print(f'FAIL: after flattening there must be exactly one circuit {top.name!r}, got '
              f'{[c.name for c in circs][:4]}')
        return 1
    tc = circs[0]
    all_nets = list(tc.each_net())
    engine_nets = len(all_nets)
    ids = {n.cluster_id for n in all_nets}
    # See connect.build_engine: this is the property the hierarchical extraction lacked, and
    # the only reason this stage may key its map on cluster_id at all. Without it, probes that
    # land inside different cells can report the same cluster_id for unrelated nets.
    id_ok = len(ids) == engine_nets
    print(f'engine                             : {engine_nets} nets, {len(ids)} distinct '
          f'cluster ids, {t_engine:.1f}s')

    # ---- assign every pin of every instance --------------------------------------------
    nets: dict[int, dict] = {}
    by_instance: dict[str, dict] = {}
    per_pin: dict[str, dict] = defaultdict(
        lambda: {'instances': 0, 'assigned': 0, 'unassigned': 0, 'class': ''})
    unassigned: list[dict] = []
    conflicts: list[dict] = []
    errors: list[str] = []
    cnt: Counter = Counter()
    layer_fallbacks = 0
    endpoints = 0
    t1 = time.time()
    for pl in insts['instances']:
        master = a4['masters'][pl['master']]
        sx, sy = ALLOW[pl['kind']]
        ax, ay = pl['origin_dbu']
        out: dict[str, list | None] = {}
        for pin in sorted(master['pins']):
            info = master['pins'][pin]
            klass = 'supply' if pin in SUPPLY else 'functional'
            endpoints += 1
            cnt[f'terminals_{klass}'] += 1
            stats = per_pin[pin]
            stats['instances'] += 1
            stats['class'] = klass
            rects = [(s['pair'], s['rect']) for s in info['shapes'] if s['pair'] in cset]

            seen: dict[int, list] = {}
            for pos in (info.get('positions_um') or []):
                px, py = float(pos[0]), float(pos[1])
                own = [pr for pr, r in rects
                       if r[0] <= px <= r[2] and r[1] <= py <= r[3]]
                if not own:
                    # The label should always sit inside the shape A4 traced it from; if it
                    # does not, the position is still worth probing on the pin's own layers,
                    # but the count is reported rather than hidden.
                    own = [pr for pr, _ in rects]
                    layer_fallbacks += 1
                pt = (sx * dbu(px, per_um) + ax, sy * dbu(py, per_um) + ay)
                for pair in sorted(own):
                    try:
                        net = l2n.probe_net(reg[pair], db.Point(*pt))
                    except Exception as exc:        # noqa: BLE001
                        errors.append(f'{pl["id"]}.{pin}@{pair}: {exc}')
                        continue
                    if net is not None:
                        seen.setdefault(net.cluster_id, []).append([pair, pt[0], pt[1]])

            if len(seen) > 1:
                conflicts.append({
                    'instance': pl['id'], 'pin': pin, 'master': pl['master'],
                    'clusters': sorted(seen),
                    'evidence': {str(c): sorted(v)[0] for c, v in sorted(seen.items())}})

            if seen:
                # Deterministic winner: most positions agreeing, then lowest cluster id.
                cid = sorted(seen, key=lambda c: (-len(seen[c]), c))[0]
                pair, x, y = sorted(seen[cid])[0]
                out[pin] = [cid, pair, x, y]
                stats['assigned'] += 1
                cnt[f'assigned_{klass}'] += 1
                n = nets.setdefault(cid, {'terminals': 0, 'functional': 0, 'supply': 0,
                                          'instances': set(), 'pin_names': Counter(),
                                          'layers': Counter()})
                n['terminals'] += 1
                n[klass] += 1
                n['instances'].add(pl['id'])
                n['pin_names'][pin] += 1
                n['layers'][pair] += 1
            else:
                out[pin] = None
                stats['unassigned'] += 1
                cnt[f'unassigned_{klass}'] += 1
                unassigned.append({
                    'instance': pl['id'], 'pin': pin, 'master': pl['master'],
                    'class': klass, 'gap_class': gap_class(pl['master'], pin),
                    'cause': ('no_routing_geometry' if not rects
                              else 'engine_left_it_unconnected')})
        by_instance[pl['id']] = {'master': pl['master'], 'pins': out}
    t_probe = time.time() - t1

    net_rows = [{'cluster': c, 'terminals': v['terminals'], 'functional': v['functional'],
                 'supply': v['supply'], 'instances': len(v['instances']),
                 'pin_names': dict(sorted(v['pin_names'].items())),
                 'layers': dict(sorted(v['layers'].items())),
                 'is_supply_only': set(v['pin_names']) <= SUPPLY}
                for c, v in sorted(nets.items(), key=lambda kv: -kv[1]['terminals'])]
    gap = Counter(u['gap_class'] for u in unassigned)
    gap_masters = Counter(u['master'].replace(STD, '') for u in unassigned)
    un_func = cnt['unassigned_functional']
    unexplained = gap['unexplained']
    largest_two_supply = all(r['is_supply_only'] for r in net_rows[:2])

    print(f'instances / pins modelled          : {len(insts["instances"])} / {endpoints}')
    print(f'net assignment                     : {t_probe:.1f}s')
    print(f'  terminals assigned               : {cnt["assigned_functional"] + cnt["assigned_supply"]}')
    print(f'    functional                     : {cnt["assigned_functional"]}'
          f' / {cnt["terminals_functional"]}')
    print(f'    supply                         : {cnt["assigned_supply"]}'
          f' / {cnt["terminals_supply"]}')
    print(f'  terminals unassigned             : {un_func + cnt["unassigned_supply"]}')
    print(f'    functional                     : {un_func}   <-- must be 0')
    print(f'    supply                         : {cnt["unassigned_supply"]}')
    print(f'  pins labelled with two nets      : {len(conflicts)}   <-- must be 0')
    print(f'  probe errors                     : {len(errors)}')
    print(f'  positions falling back to layers : {layer_fallbacks}')
    print(f'  nets carrying at least one pin   : {len(net_rows)} of {engine_nets} extracted')
    print(f'  nets with a single terminal      : {sum(1 for r in net_rows if r["terminals"] == 1)}')
    print()
    print('unassigned supply pins, by A5 gap class  (0 outside these two)')
    for k, v in sorted(gap.items()):
        print(f'  {k:<38}{v:>6}')
    print('  by master:')
    for k, v in gap_masters.most_common():
        print(f'    {k:<34}{v:>6}')
    print()
    print(f"  {'cluster':>8}{'terms':>7}{'func':>6}{'supply':>7}{'insts':>7}  pin names")
    for r in net_rows[:6]:
        print(f'  {r["cluster"]:>8}{r["terminals"]:>7}{r["functional"]:>6}{r["supply"]:>7}'
              f'{r["instances"]:>7}  {r["pin_names"]}')
    print(f'  the two largest nets are supply nets : {largest_two_supply}')

    result = {
        'generated_by': 'tools/puzzle/netlist.py::stage_pin_net',
        'source': {'path': 'asic-puzzle-2026/puzzle.gds', 'sha256': sha256(PUZZLE)},
        'inputs': ['recon/derived/layers.json', 'recon/derived/via_pairs.json',
                   'recon/derived/pinmodel.json', 'recon/derived/instances.json',
                   'recon/inventory.json'],
        'engine': {'name': 'klayout LayoutToNetlist', 'conductors': conductors,
                   'via_rules': len(a2['pairs']),
                   'configured_from': ['recon/derived/layers.json',
                                       'recon/derived/via_pairs.json']},
        'method': {
            'pin_to_net': 'probe_net(point) on the pin\'s own conductor layers -- a pin and '
                          'the wire it joins are never on the same layer, they are joined by '
                          'a via instance (plan sec.6.5)',
            'probe_points': 'A4 positions_um (label origins), transformed by the B1 affine '
                            'map; never a bounding-rect centre',
            'position_layers': 'each position is probed only on the pairs whose shape '
                               'contains it, so it cannot land on another net\'s wire',
            'net_identity': 'KLayout cluster_id -- stable within one engine configuration',
            'disagreement': 'a pin whose positions land on more than one net is a defect and '
                            'is recorded in conflicts; the winner is the cluster with the '
                            'most agreeing positions, ties broken by lowest cluster id',
            'supply_note': 'supply pins are modelled and probed like any other pin; 972 of '
                           'them have no net for the two reasons A5 predicted'},
        'totals': {
            'instances': len(insts['instances']),
            'endpoints': endpoints,
            'terminals_functional': cnt['terminals_functional'],
            'terminals_supply': cnt['terminals_supply'],
            'assigned': cnt['assigned_functional'] + cnt['assigned_supply'],
            'assigned_functional': cnt['assigned_functional'],
            'assigned_supply': cnt['assigned_supply'],
            'unassigned': un_func + cnt['unassigned_supply'],
            'unassigned_functional': un_func,
            'unassigned_supply': cnt['unassigned_supply'],
            'unassigned_supply_unexplained': unexplained,
            'nets_with_terminals': len(net_rows),
            'engine_nets': engine_nets,
            'engine_distinct_cluster_ids': len(ids),
            'net_identity_unique': id_ok,
            'nets_with_one_terminal': sum(1 for r in net_rows if r['terminals'] == 1),
            'nets_carrying_only_supply_pins': sum(1 for r in net_rows if r['is_supply_only']),
            'largest_two_nets_are_supply': largest_two_supply,
            'pins_labelled_with_two_nets': len(conflicts),
            'probe_errors': len(errors),
            'position_layer_fallbacks': layer_fallbacks,
        },
        'gap_classes': {'vpb_well_tie_no_routeable_geometry':
                        gap['vpb_well_tie_no_routeable_geometry'],
                        'antenna_diode_supply': gap['antenna_diode_supply'],
                        'unexplained': unexplained,
                        'by_master': dict(sorted(gap_masters.items()))},
        'by_pin_name': {k: dict(v) for k, v in sorted(per_pin.items())},
        'nets': net_rows,
        'instances': by_instance,
        'unassigned': unassigned,
        'conflicts': conflicts,
        'probe_errors': errors,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=False) + '\n',
                   encoding='utf-8', newline='\n')
    print(f'\nwritten: {OUT.relative_to(ROOT)}')
    ok = (un_func == 0 and unexplained == 0 and not conflicts and not errors
          and largest_two_supply and id_ok)
    print()
    print('B4 PIN-TO-NET MAP: ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    stage = argv[0] if argv else 'pins-to-nets'
    if stage == 'pins-to-nets':
        return stage_pin_net()
    print(f'netlist.py has no stage {stage!r}')
    return 2


if __name__ == '__main__':
    import sys
    raise SystemExit(main(sys.argv[1:]))
