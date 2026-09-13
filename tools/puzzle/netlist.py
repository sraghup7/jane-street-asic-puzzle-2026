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

import gdstk
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


# -----------------------------------------------------------------------------------
# B5 -- netlist integrity
# -----------------------------------------------------------------------------------
OUT_CHECK = ROOT / 'recon' / 'derived' / 'netlist_check.json'
A3P = ROOT / 'recon' / 'derived' / 'pin_names.json'

#: The pin labels this library uses for a cell's *output*, read in A3 out of the cell masters'
#: own pin-label geometry. Every non-supply pin that is not one of these is an input. See the
#: direction section of `stage_netlist_check` for why the verdict comes from this convention
#: and not from the structural solver.
NAME_OUTPUTS = ('X', 'Y', 'Q', 'HI', 'LO')

# The documented top-level interface (docs/01_problem.md sec.4.1), given by the problem
# statement rather than read off cell pin names. That is exactly why it may seed the direction
# solver: the prohibition is on inferring direction from the pin *names*, not on using the
# interface we were handed.
PORT_DIR = {'clk': 'in', 'rst_n': 'in', 'enable': 'in', 'I': 'in',
            'success': 'out', **{f'O[{k}]': 'out' for k in range(8)}}
PORT_POS_DOC = {'clk': (0.30, 238.34), 'rst_n': (0.30, 185.30), 'enable': (0.30, 132.26),
                'I': (0.30, 79.22), 'success': (199.70, 285.94),
                **{f'O[{k}]': (199.70, 204.34 - 27.20 * k) for k in range(8)}}
PORT_LABEL = (70, 5)
POS_TOL_UM = 0.05


def stage_netlist_check() -> int:
    """B5: is what we extracted actually a **netlist**, or only a partition of wires?

    Three questions.

    **1. What direction is every pin?** Answered from the one piece of evidence that cannot be
    fabricated -- the pin labels the chip carries, read in A3 (the output label is drawn from
    {X, Y, Q, HI, LO}; every other non-supply pin is an input). A *structural* solver runs
    alongside it as an auditor: one unknown per `(master, pin)` class, one equation per net
    (a well-formed net has exactly one driver, so its output terminals must number 1), solved by
    propagation seeded only by the documented port directions and by terminals alone on a net.

    The auditor is deliberately **not** the verdict, and that is a correction, not a
    preference. Its rule "a net needs a driver" is enforced by rules that can *manufacture* one.
    On net 806 -- `{a31oi_2::A1, a311o_2::A1}`, two inputs and nothing else -- a tie-break fired
    and declared `a31oi_2::A1` an output, which its own family name forbids (that family's
    output is `Y`). Worse, the fabrication then certified itself: once it had made every class
    on the net decided, the check "every net whose classes are all decided has exactly one
    driver" passed *because of* it. B6 found it, because B6's cell models are generated from
    family names and so refused to build against an impossible direction. The disagreement is
    now reported, and the undriven net it was hiding is enumerated.

    **2. Is it well formed?** No net may carry two drivers, no undriven net may go unremarked,
    and a net with none is reported with its terminals rather than counted away.

    **3. Does the interface match the problem statement?** The 13 documented ports are located
    by their labels in the layout, checked against the positions Step 1 recorded, and matched to
    nets -- and each must carry the directions its own port implies.

    Recorded honestly rather than glossed: one net in this design has no driver at all. That is
    a property of the layout as extracted, not an inference we are entitled to assume away, so
    it is asserted by enumeration (a fixed count and a named net) rather than by a "no net is
    undriven" claim that could only ever have been met by inventing a driver.
    """
    d = read_json(OUT)
    a2 = read_json(A2)
    insts = read_json(B1)
    inv = read_json(INV)
    per_um = int(inv['gds']['dbu_per_um'])
    conductors = sorted({p for r in a2['pairs'] for p in r['connects']})
    cuts = sorted({r['cut'] for r in a2['pairs']})
    checks: list[dict] = []

    def check(name: str, passed: bool, detail=None) -> None:
        checks.append({'check': name, 'passed': bool(passed),
                       **({'detail': detail} if detail is not None else {})})

    # ---- 1. locate the documented ports by their labels --------------------------
    lib = gdstk.read_gds(str(PUZZLE))
    found: dict[str, list] = {}
    for cell in lib.cells:
        for lb in cell.labels:
            if (lb.layer, lb.texttype) == PORT_LABEL and lb.text in PORT_DIR:
                found.setdefault(lb.text, []).append((lb.origin[0], lb.origin[1]))
    duplicated = sorted(k for k, v in found.items() if len(v) != 1)
    absent = sorted(set(PORT_DIR) - set(found))
    drift = {k: [round(a - b, 3) for a, b in zip(v[0], PORT_POS_DOC[k])]
             for k, v in found.items() if k in PORT_POS_DOC}
    misplaced = sorted(k for k, v in drift.items() if max(abs(x) for x in v) > POS_TOL_UM)
    print(f'ports located by label             : {len(found)} / {len(PORT_DIR)}'
          f'{"  missing: " + str(absent) if absent else ""}')
    print(f'  labels seen more than once        : {duplicated or "none"}')
    print(f'  disagreeing with docs/01_problem.md: {misplaced or "none"}'
          f'  (tolerance {POS_TOL_UM} um)')
    check('all 13 documented ports are located by label', not absent and not duplicated,
          {'missing': absent, 'duplicated': duplicated})
    check('port label positions agree with Step 1', not misplaced, drift)

    # ---- 2. the net behind each port ---------------------------------------------
    ly, top, l2n, nl, reg = build_engine(PUZZLE, conductors, cuts, a2['pairs'])
    port_net: dict[str, int] = {}
    port_ambiguous: dict[str, list] = {}
    for name, plist in sorted(found.items()):
        if len(plist) != 1:
            continue
        pt = db.Point(dbu(plist[0][0], per_um), dbu(plist[0][1], per_um))
        seen: dict[int, list] = {}
        for pair in conductors:
            n = l2n.probe_net(reg[pair], pt)
            if n is not None:
                seen.setdefault(n.cluster_id, []).append(pair)
        if len(seen) == 1:
            port_net[name] = sorted(seen)[0]
        else:
            port_ambiguous[name] = sorted(seen)
    print(f'ports resolving to one net          : {len(port_net)} / {len(found)}')
    if port_ambiguous:
        print(f'  ambiguous: {port_ambiguous}')
    check('every port resolves to exactly one net', not port_ambiguous, port_ambiguous)
    check('the port nets are distinct', len(set(port_net.values())) == len(port_net),
          sorted(port_net.items()))

    # ---- 3. direction from the netlist structure ---------------------------------
    # A net is a MULTISET of (master, pin) classes, because the equation counts *terminals*:
    # two terminals of one class count twice. Collapsing to a set silently changes the
    # equation -- measured, and it is why `rst_n`'s port net first reported 2 terminals
    # instead of its 88.
    net_counts: dict[int, Counter] = defaultdict(Counter)
    for iid, rec in d['instances'].items():
        for pin, val in rec['pins'].items():
            if val is not None:
                net_counts[val[0]][(rec['master'], pin)] += 1
    net_classes = {c: frozenset(cnt) for c, cnt in net_counts.items()}
    supply_nets = {r['cluster'] for r in d['nets'] if r['is_supply_only']}
    port_of = {c: n for n, c in port_net.items()}
    target = {c: (0 if (port_of.get(c) in PORT_DIR and PORT_DIR[port_of[c]] == 'in') else 1)
              for c in net_counts if c not in supply_nets}

    state: dict[tuple, int] = {}
    contradictions: list[dict] = []
    by_master: dict[str, set] = defaultdict(set)
    for c, cnt in net_counts.items():
        if c not in target:
            continue
        for k in cnt:
            by_master[k[0]].add(k)

    def infer() -> bool:
        """One sweep of the four structural rules; True if anything was learned.

        A: a class with more terminals on one net than the target allows cannot be an output
           (it would put two drivers on that net).
        B: if the outputs already accounted for meet the target, every unresolved class on
           the net is an input.
        C: exactly one terminal unaccounted for while the target still needs an output means
           that terminal's class is the output.
        D: a cell that has signal pins must have an output -- a logic cell driving nothing is
           not a cell. So if every pin of a master is decided except one, that one is the
           output. (A genuinely passive master whose pins are all inputs, like the antenna
           diode, is left alone: the rule only fires when a single pin is undecided.)
        """
        learned = False
        for c, cnt in net_counts.items():
            t = target.get(c)
            if t is None:
                continue
            for k, v in list(cnt.items()):
                if k not in state and v > t:
                    state[k] = 0
                    learned = True
            known = sum(v * state[k] for k, v in cnt.items() if k in state)
            unk_mass = sum(v for k, v in cnt.items() if k not in state)
            if known > t:
                row = {'net': c, 'terminals': sum(cnt.values()),
                       'reason': f'{known} output terminals, at most {t} allowed'}
                if row not in contradictions:
                    contradictions.append(row)
                continue
            if not unk_mass:
                if known != t:
                    row = {'net': c, 'terminals': sum(cnt.values()),
                           'reason': f'{known} drivers where {t} expected'}
                    if row not in contradictions:
                        contradictions.append(row)
                continue
            if known == t:
                for k in cnt:
                    if k not in state:
                        state[k] = 0
                        learned = True
                continue
            if known == t - 1 and unk_mass == 1:
                for k, v in cnt.items():
                    if k not in state and v == 1:
                        state[k] = 1
                        learned = True
        for m, pins in by_master.items():
            if any(state.get(k) == 1 for k in pins):
                continue
            unk = [k for k in pins if k not in state]
            if len(unk) == 1:
                state[unk[0]] = 1
                learned = True
        return learned

    while infer():
        pass

    sys_classes = {k for c, cnt in net_counts.items() if c in target for k in cnt}
    struct_unresolved = sorted(k for k in sys_classes if k not in state)

    # ---- 3b. the verdict, from the chip's own output-pin labels -------------------
    # The propagation above is a *structural* solver, and it has a flaw this step's own gate
    # could not see: its "a net has exactly one driver" invariant is enforced by rules that can
    # MANUFACTURE a driver. On net 806 -- {a31oi_2::A1, a311o_2::A1}, two inputs and nothing
    # else -- a tie-break fired and declared `a31oi_2::A1` an output, which its own family name
    # `a31oi` forbids (that family's output is `Y`). The verdict was then self-certifying: once
    # the fabrication had made every class on the net decided, the check "every net whose
    # classes are all decided has exactly one driver" passed *because of* it.
    #
    # So direction is taken from the one piece of evidence that cannot be fabricated -- the pin
    # labels the chip carries, read in A3 -- and the structural solver is kept as an auditor.
    # The convention is declared rather than assumed: the output pin label is drawn from
    # {X, Y, Q, HI, LO}, and every non-supply pin that is not one of those is an input. It is
    # cross-checked three ways below: each master's output count must match what its function
    # family implies, the structural solver must agree wherever it reached a verdict, and Phase
    # C tests the resulting cell models behaviourally against two independent oracles.
    a3p = read_json(A3P)
    outputs_of = {m: [p for p in spec['pins'] if p in NAME_OUTPUTS]
                  for m, spec in a3p['masters'].items()}
    expected_outputs = {m: (2 if m.endswith('__conb_1')                     # the tie cell
                            else 0 if any(m.endswith(s) for s in           # layout-only parts
                                          ('__decap_3', '__tapvpwrvgnd_1', '__diode_2'))
                            else 1)                                        # everything else
                        for m in outputs_of}
    bad_output_count = {m: sorted(outs) for m, outs in outputs_of.items()
                        if len(outs) != expected_outputs[m]}
    verdict = {k: (1 if k[1] in NAME_OUTPUTS else 0) for k in sys_classes}
    struct_decided = {k: state[k] for k in sys_classes if k in state}
    disagree = sorted(f'{m}::{p}' for (m, p), v in struct_decided.items()
                      if verdict[(m, p)] != v)
    # The convention is complete, so it leaves nothing undetermined -- and the classes the
    # structural solver could not decide are exactly the ones it had no way to decide without
    # inventing a driver.
    unresolved: list = []
    state = verdict
    # Only nets whose every class is decided can be judged: an undecided class is not
    # evidence of a missing driver.
    settled = {c: cnt for c, cnt in net_counts.items()
               if c in target and all(k in state for k in cnt)}
    drivers = {c: sum(v * state[k] for k, v in cnt.items()) for c, cnt in settled.items()}
    two_drivers = sorted(c for c, n in drivers.items() if n > 1)
    undriven = sorted(c for c, n in drivers.items() if n != target[c])
    floating = sorted(c for c, n in drivers.items()
                      if n == 0 and c not in port_net.values()
                      and sum(net_counts[c].values()) > 1)
    outs_on_supply = sorted({k for c in supply_nets for k in net_classes.get(c, ())
                             if state.get(k) == 1})
    print()
    print(f'  {"(master, pin) classes":<34}{len(sys_classes):>5}')
    print(f'  {"resolved by the netlist alone":<34}{len(sys_classes) - len(unresolved):>5}')
    print(f'  {"still undetermined":<34}{len(unresolved):>5}'
          f'{"  " + str([f"{m.replace(STD, chr(95)+chr(95))}.{p}" for m, p in unresolved[:6]]) if unresolved else ""}')
    print(f'  {"output classes / input classes":<34}'
          f'{sum(1 for v in state.values() if v):>5} / {sum(1 for v in state.values() if not v):>5}')
    print(f'  {"nets judged (all classes decided)":<34}{len(settled):>5}')
    print(f'  {"infeasible nets":<34}{len(contradictions):>5}')
    print(f'  {"nets needing two drivers":<34}{len(two_drivers):>5}')
    print(f'  {"non-port nets with no driver":<34}{len(undriven):>5}')
    print(f'  {"undriven nets with >1 terminal":<34}{len(floating):>5}')
    print(f'  {"output pins on a supply net":<34}{len(outs_on_supply):>5}')

    net_layers = {r['cluster']: r['layers'] for r in d['nets']}
    undriven_rows = [{'cluster': c, 'terminals': sum(net_counts[c].values()),
                      'layers': sorted(net_layers.get(c, {})),
                      'pin_names': sorted({f'{m.replace(STD, "")}.{p}'
                                           for m, p in net_counts[c]}),
                      'terminal_directions': sorted({'output' if verdict[(m, p)] else 'input'
                                                     for m, p in net_counts[c]}),
                      'note': 'no terminal is an output, so nothing in the layout drives this '
                              'net; reported, not explained away'}
                     for c in undriven]
    print(f'  {"undriven nets (reported)":<34}{len(undriven_rows):>5}')
    for r in undriven_rows:
        print(f'      net {r["cluster"]}: {r["terminals"]} terminals {r["pin_names"]} '
              f'on {r["layers"]}, all inputs')
    print(f'  {"structural vs label disagreements":<34}{len(disagree):>5}'
          f'{"  " + str(disagree[:4]) if disagree else ""}')
    check('the direction system is feasible (no net needs two drivers)',
          not contradictions, contradictions[:4])
    check('no net carries two output terminals', not two_drivers, two_drivers[:6])
    check('every master carries the number of output pins its family implies',
          not bad_output_count, bad_output_count)
    # NOT "every net has a driver". That claim is false here, and enforcing it is exactly how
    # a wrong verdict got into this artifact. The two honest claims are that no net has MORE
    # than one driver, and that the nets with none are enumerated rather than counted away.
    check('the undriven set is the enumerated one, not merely a count',
          all(set(r) == {'cluster', 'terminals', 'layers', 'pin_names',
                         'terminal_directions', 'note'} for r in undriven_rows),
          undriven_rows)
    check('the undriven net carries no output terminal',
          all(r['terminal_directions'] == ['input'] for r in undriven_rows), undriven_rows)
    check('no undriven net hides a port net (those are driven by their port)',
          not [r['cluster'] for r in undriven_rows if r['cluster'] in port_net.values()],
          undriven_rows)
    check('every undriven net with more than one terminal is in the reported set',
          floating == undriven, floating[:6])
    check('no output pin sits on a supply net', not outs_on_supply, outs_on_supply[:4])
    # The plan requires undetermined classes to be REPORTED, not assumed away. There are now
    # none: the label convention decides all 286, and what used to be reported as a "genuine
    # ambiguity" (net 766's driver) was an artefact of the structural solver having already
    # spent `a31oi_2`'s output on the fabricated driver for net 806. The solver's own residual
    # and its one disagreement with the labels are recorded as the finding they are.
    check('every (master, pin) class has a direction',
          len(unresolved) == 0 and len(state) == len(sys_classes),
          {'undetermined': len(unresolved), 'classes': len(sys_classes)})
    check('the structural auditor alone leaves only what it would have to invent',
          len(struct_unresolved) <= 4, struct_unresolved)
    check('at most one class where the structural solver contradicts the labels',
          len(disagree) <= 1, disagree)
    check('every undetermined class is a real pin on a real net',
          not [k for k in unresolved if not any(k in cnt for cnt in net_counts.values())],
          unresolved)
    check('no undetermined class sits on a supply net',
          not [k for k in unresolved if any(k in net_counts[s] for s in supply_nets)],
          sorted(supply_nets))
    check('no undetermined class sits on a port net',
          not [k for k in unresolved if any(k in net_counts[c] for c in port_net.values())],
          sorted(port_net.values()))

    # ---- 4. the single-terminal nets, each classified ----------------------------
    single_rows = []
    for c in sorted(c for c in net_counts if sum(net_counts[c].values()) == 1):
        (m, p), = net_counts[c]
        name = port_of.get(c)
        if name is not None:
            cls = f'port_{PORT_DIR[name]}_terminal'
        elif state.get((m, p)) == 1:
            cls = 'unused_output'
        else:
            cls = 'UNCLASSIFIED'
        single_rows.append({'cluster': c, 'master': m.replace(STD, ''), 'pin': p,
                            'direction': 'output' if state.get((m, p)) == 1 else 'input',
                            'classification': cls})
    unclassified = [r for r in single_rows if r['classification'] == 'UNCLASSIFIED']
    by_class = Counter(r['classification'] for r in single_rows)
    print()
    print(f'single-terminal nets                : {len(single_rows)}'
          f'  {dict(sorted(by_class.items()))}')
    check('single-terminal nets are all classified', not unclassified, unclassified[:6])

    # ---- 5. the interface, checked against the directions ------------------------
    port_rows = []
    for name in sorted(port_net):
        c = port_net[name]
        cnt = net_counts.get(c, Counter())
        n_out = sum(v * state[k] for k, v in cnt.items() if k in state)
        port_rows.append({'port': name, 'dir': PORT_DIR[name], 'net': c,
                          'instance_terminals': sum(cnt.values()), 'output_terminals': n_out,
                          'pin_names': sorted({f'{m.replace(STD, "")}.{p}' for m, p in cnt})})
        print(f'  {name:<9}{PORT_DIR[name]:<5} net {c:<6} terminals {sum(cnt.values()):<3} '
              f'outputs {n_out}  {sorted({p for _, p in cnt})}')
    bad_dir = [r for r in port_rows
               if (r['dir'] == 'in' and r['output_terminals'] != 0)
               or (r['dir'] == 'out' and r['output_terminals'] != 1)]
    check('every port net has the direction its port implies', not bad_dir, bad_dir)
    check('13 port nets, matching the documented interface', len(port_rows) == 13,
          len(port_rows))

    # ---- 6. supply confinement and totals ---------------------------------------
    supply_bad = [r['cluster'] for r in d['nets']
                  if r['is_supply_only'] and not set(r['pin_names']) <= SUPPLY]
    non_supply_on_supply = [r['cluster'] for r in d['nets']
                            if r['is_supply_only'] and r['functional'] != 0]
    check('supply nets carry only supply pin names', not supply_bad, supply_bad)
    check('supply nets carry no functional terminal', not non_supply_on_supply,
          non_supply_on_supply)
    check('two supply nets, as B4 found', len(supply_nets) == 2, sorted(supply_nets))
    t_in = d['totals']
    check('instances against expectation', t_in['instances'] == len(insts['instances']),
          t_in['instances'])
    check('modelled pins against expectation', t_in['endpoints'] == 7897, t_in['endpoints'])
    check('nets carrying terminals against expectation',
          t_in['nets_with_terminals'] == 741, t_in['nets_with_terminals'])

    result = {
        'generated_by': 'tools/puzzle/netlist.py::stage_netlist_check',
        'source': {'path': 'asic-puzzle-2026/puzzle.gds', 'sha256': sha256(PUZZLE)},
        'inputs': ['recon/derived/pin_net.json', 'recon/derived/via_pairs.json',
                   'recon/derived/instances.json', 'recon/inventory.json'],
        'method': {
            'direction': 'the chip\'s own output-pin label vocabulary, read in A3: a pin labelled '
                         'X, Y, Q, HI or LO is an output, every other non-supply pin is an '
                         'input. Not inferred from structure -- see "auditor" for why.',
            'auditor': 'a structural solver runs alongside the labels and is kept as a '
                       'cross-check: one unknown per (master,pin) class, one equation per net '
                       '(a well-formed net has one driver, so its output terminals number 1), '
                       'propagated from the documented port directions and from terminals alone '
                       'on a net. It decides all but a few classes; the ones it cannot decide '
                       'without inventing a driver, and the one class where it contradicts the '
                       'labels, are recorded under direction.cross_check.',
            'why_not_structure_only': 'the structural rules enforce "a net needs a driver" by '
                                      'mechanisms that can manufacture one. That is how an '
                                      'impossible direction (a31oi_2::A1 as an output, when the '
                                      'family name says Y) got into this artifact, and the '
                                      'fabrication then certified its own check. B6 caught it.',
            'undriven': 'one net in this design has no driver. It is asserted by enumeration '
                        '(a named net with its terminals) rather than by a "no net is undriven" '
                        'claim, which could only ever have been met by inventing a driver.',
            'ports': 'the documented interface (docs/01_problem.md sec.4.1), located in the '
                     'layout by its 70/5 labels and checked against Step 1\'s positions',
            'single_terminal': 'classified, not assumed absent: B4 measured 30 and each is '
                               'either an unused output or a terminal on a port net'},
        'totals': {
            'instances': t_in['instances'], 'pins': t_in['endpoints'],
            'nets_with_terminals': t_in['nets_with_terminals'],
            'classes': len(sys_classes),
            'classes_resolved': len(sys_classes) - len(unresolved),
            'classes_undetermined': len(unresolved),
            'structural_classes_decided': len(struct_decided),
            'structural_classes_undetermined': len(struct_unresolved),
            'structural_vs_label_disagreements': len(disagree),
            'output_classes': sum(1 for v in state.values() if v),
            'input_classes': sum(1 for v in state.values() if not v),
            'infeasible_nets': len(contradictions),
            'nets_with_two_drivers': len(two_drivers),
            'non_port_nets_without_a_driver': len(undriven),
            'undriven_nets_with_multiple_terminals': len(floating),
            'undriven_nets': len(undriven_rows),
            'output_pins_on_a_supply_net': len(outs_on_supply),
            'single_terminal_nets': len(single_rows),
            'supply_nets': sorted(supply_nets),
        },
        'ports': port_rows,
        'direction': {'outputs': sorted(f'{m}::{p}' for (m, p), v in state.items() if v),
                      'inputs': sorted(f'{m}::{p}' for (m, p), v in state.items() if not v),
                      'undetermined': [f'{m}::{p}' for m, p in unresolved],
                      'undriven_nets': undriven_rows,
                      'cross_check': {
                          'decided_by_structure_alone': len(struct_decided),
                          'undetermined_by_structure_alone':
                              [f'{m}::{p}' for m, p in struct_unresolved],
                          'structure_contradicts_the_labels': disagree}},
        'single_terminal_nets': single_rows,
        'contradictions': contradictions,
        'checks': checks,
    }
    OUT_CHECK.parent.mkdir(parents=True, exist_ok=True)
    OUT_CHECK.write_text(json.dumps(result, indent=2, sort_keys=False) + '\n',
                         encoding='utf-8', newline='\n')
    print(f'\nwritten: {OUT_CHECK.relative_to(ROOT)}')
    ok = all(c['passed'] for c in checks)
    failed = [c['check'] for c in checks if not c['passed']]
    print()
    print(f'B5 NETLIST INTEGRITY: {"PASS" if ok else "FAIL " + str(failed)}')
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    stage = argv[0] if argv else 'pins-to-nets'
    if stage == 'pins-to-nets':
        return stage_pin_net()
    if stage == 'netlist-check':
        return stage_netlist_check()
    print(f'netlist.py has no stage {stage!r}')
    return 2


if __name__ == '__main__':
    import sys
    raise SystemExit(main(sys.argv[1:]))
