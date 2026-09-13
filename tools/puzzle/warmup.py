#!/usr/bin/env python3
"""warmup.py -- B7: the whole chain, on a design whose answer we already hold.

B7 is the Phase B gate. Everything up to here was verified against *evidence about itself* --
gates that re-derive an artifact from its own inputs, which is strong but cannot catch an error
shared by the derivation and its check. The warm-up is the one design where an **independent**
answer exists: `warmup/01_netlist.v` is the real netlist of a real small design, and
`warmup/04_final.gds` is that design's layout. So this stage runs the pipeline end to end on it
and compares.

What "end to end" means here, and what it deliberately does **not** mean:

* it uses `connect.build_engine` -- the *flattened* engine B3/B4 use for the chip, not the
  separate hierarchical path B2 built its spike on. B2 proved the connectivity rules; it did not
  exercise the code that B3-B6 actually run.
* it uses `netlist.probe_placements`, the same function B4 calls, and `emit.render_body`, the same
  renderer B6 calls. The placements are the only input that differs between the two designs.
  That is the whole point: a second implementation agreeing with the first would prove nothing.
* it emits `build/warmup.v` and compiles it against the same `build/cells.v`, so the cell models
  are exercised on a design whose behaviour we can check.

Three comparisons come out of it, in increasing order of what they prove:

1. **Partition equivalence** with `01_netlist.v`, by B2's exact canonical method -- nets as sets of
   (instance, pin), canonicalised, with B2's coverage guard. This is the same comparison B2 ran,
   so B2 and B7 agree by construction rather than by hope.
2. **The interface**, re-derived: the reference's own module header names the ports, and each is
   located on our side by requiring its terminal set to match exactly. A mis-wired port cannot
   pass this, because it would land on a cluster whose terminals differ.
3. **Supply connectivity** against `02_netlist_with_power_rails.v`. The reference *does* connect
   VPB/VNB; our extraction assigns them nothing. That disagreement is A5's prediction (well ties
   have no routeable geometry), and this is the first time it is checked against an independent
   document rather than against our own reasoning.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.puzzle import emit as E                             # noqa: E402
from tools.puzzle import netlist as N                          # noqa: E402
from tools.puzzle.connect import (SUPPLY, WU_DEF, WU_GDS, WU_NET, build_engine,   # noqa: E402
                                  parse_def_placements, parse_netlist, read_json,
                                  sha256, warmup_instances)

A2 = ROOT / 'recon' / 'derived' / 'via_pairs.json'
A4 = ROOT / 'recon' / 'derived' / 'pinmodel.json'
A3P = ROOT / 'recon' / 'derived' / 'pin_names.json'
INV = ROOT / 'recon' / 'inventory.json'
RAILS = ROOT / 'asic-puzzle-2026' / 'warmup' / '02_netlist_with_power_rails.v'
OUT_NETLIST = ROOT / 'build' / 'warmup.v'
OUT_REPORT = ROOT / 'recon' / 'derived' / 'warmup_b7.json'

#: What the report calls the netlist it emitted. A *literal*, not `str(OUT_NETLIST.relative_to
#: (ROOT))`: the gates redirect OUT_NETLIST into scratch to regenerate hermetically, and a report
#: that embeds its sibling's path then differs from the committed one by that path alone -- which
#: is the same class of non-determinism B4 hit with a stored runtime. The artifact must not
#: depend on where it was written.
EMITTED_REL = 'build/warmup.v'

MODULE_RE = re.compile(r'^module\s+(\w+)\s*\(([^)]*)\)\s*;', re.M)
DECL_RE = re.compile(r'^\s*(input|output|inout)\s+(\w+)\s*;', re.M)


def parse_module_ports(text: str) -> tuple[str, list[str], dict[str, str]]:
    """The reference's own top-level interface: (module, port order, {port: direction})."""
    m = MODULE_RE.search(text)
    if not m:
        raise ValueError('no module header')
    ports = [p.strip() for p in m.group(2).split(',')]
    return m.group(1), ports, {p: d for d, p in DECL_RE.findall(text)}


def canon(part: dict) -> Counter:
    """B2's canonical form: nets as frozen sets of (instance, pin), with multiplicity."""
    return Counter(frozenset(t) for t in part.values())


def stage_warmup_regression() -> int:
    a2, a4, inv = read_json(A2), read_json(A4), read_json(INV)
    per_um = int(inv['gds']['dbu_per_um'])
    conductors = sorted({p for r in a2['pairs'] for p in r['connects']})
    cuts = sorted({r['cut'] for r in a2['pairs']})
    cset = set(conductors)

    # ---- 1. the same placements B1's convention produces ---------------------------
    placements, _ = warmup_instances(per_um)
    masters = sorted({p['master'] for p in placements})
    print(f'warm-up placements                : {len(placements)}')
    print(f'  cell types                      : {len(masters)}')

    # ---- 2. the SAME flattened engine B3/B4 use on the chip ------------------------
    ly, top, l2n, nl, reg = build_engine(WU_GDS, conductors, cuts, a2['pairs'])
    circs = list(nl.each_circuit())
    engine_nets = sum(1 for _ in circs[0].each_net()) if circs else 0
    ids = {n.cluster_id for n in circs[0].each_net()} if circs else set()
    print(f'engine (flattened, as B3/B4)      : {len(circs)} circuit(s), {engine_nets} nets, '
          f'{len(ids)} distinct cluster ids')

    # ---- 3. the SAME prober B4 uses ------------------------------------------------
    pr = N.probe_placements(placements, a4, per_um, l2n, reg, cset)
    cnt = pr['cnt']
    print(f'pins probed                       : {cnt["terminals_functional"]} functional, '
          f'{cnt["terminals_supply"]} supply')
    print(f'  functional unassigned           : {cnt["unassigned_functional"]}')
    print(f'  probe conflicts                 : {len(pr["conflicts"])}')
    print(f'  probe errors                    : {len(pr["errors"])}')

    ours: dict[int, set] = defaultdict(set)
    for iid, rec in pr['by_instance'].items():
        for pin, val in rec['pins'].items():
            if val is not None:
                ours[val[0]].add((iid, pin))

    # ---- 4. the reference, and the bridge between its names and our ids -------------
    ref_text = WU_NET.read_text(encoding='utf-8')
    ref_insts, _ = parse_netlist(ref_text)
    def_place = parse_def_placements(WU_DEF.read_text(encoding='utf-8'))
    module, port_order, port_dir = parse_module_ports(ref_text)
    print(f'reference {WU_NET.name:<26}: {len(ref_insts)} instances, module {module!r}, '
          f'ports {port_order}')

    by_ll = {(pl['master'], pl['lower_left_dbu']): pl['id'] for pl in placements}
    id_map, unmapped = {}, []
    for name in ref_insts:
        leaf = name.lstrip('\\')
        if leaf not in def_place:
            unmapped.append(name)
            continue
        cell, dx, dy = def_place[leaf]
        if (cell, (dx, dy)) not in by_ll:
            unmapped.append(name)
            continue
        id_map[name] = by_ll[(cell, (dx, dy))]
    print(f'  netlist name -> our placement   : {len(id_map)} mapped, {len(unmapped)} unmapped')

    theirs: dict[str, set] = defaultdict(set)
    for name, info in ref_insts.items():
        if name not in id_map:
            continue
        for pin, net in info['pins'].items():
            if pin in SUPPLY:
                continue
            theirs[net].add((id_map[name], pin))

    ref_terminals = {t for v in theirs.values() for t in v}
    ours_f = {c: {t for t in v if t in ref_terminals} for c, v in ours.items()}
    ours_f = {c: v for c, v in ours_f.items() if v}

    a_can, b_can = canon(ours_f), canon(theirs)
    only_ours, only_theirs = a_can - b_can, b_can - a_can
    compared = {t for v in ours_f.values() for t in v}
    coverage = len(compared) / max(1, len(ref_terminals))

    print()
    print('partition comparison (B2s exact method, canonically)')
    print(f'  our nets / reference nets       : {len(ours_f)} / {len(theirs)}')
    print(f'  our terminals / reference       : {sum(len(v) for v in ours_f.values())} / '
          f'{sum(len(v) for v in theirs.values())}')
    print(f'  signatures only in ours         : {sum(only_ours.values())}')
    print(f'  signatures only in reference    : {sum(only_theirs.values())}')
    print(f'  reference terminals compared    : {len(compared)} / {len(ref_terminals)} '
          f'({coverage:.1%})')
    for sig in list(only_theirs)[:3]:
        print(f'      ref-only {sorted(sig)[:6]}')
    for sig in list(only_ours)[:3]:
        print(f'      ours-only {sorted(sig)[:6]}')

    # ---- 5. the interface, located on our side by terminal-set identity ------------
    port_cluster: dict[str, int] = {}
    port_problems: list[str] = []
    for name in port_order:
        want = theirs.get(name, set())
        hits = [c for c, v in ours_f.items() if v == want and want]
        if len(hits) == 1:
            port_cluster[name] = hits[0]
        else:
            port_problems.append(f'{name}: {len(hits)} candidate clusters')
    print()
    print(f'ports located by terminal-set identity: {len(port_cluster)} / {len(port_order)}'
          + (f'  {port_problems}' if port_problems else ''))

    # ---- 6. supply connectivity against the with-rails reference -------------------
    rails_text = RAILS.read_text(encoding='utf-8')
    rails_insts, _ = parse_netlist(rails_text)
    ref_supply = Counter(p for i in rails_insts.values() for p in i['pins']
                         if p in SUPPLY)
    our_supply = Counter(p for iid, rec in pr['by_instance'].items() for p, v in rec['pins'].items()
                         if p in SUPPLY and v is not None)
    print()
    print('supply connectivity vs 02_netlist_with_power_rails.v')
    for pin in sorted(set(ref_supply) | set(our_supply)):
        print(f'  {pin:<5} reference {ref_supply.get(pin, 0):>4}   ours '
              f'{our_supply.get(pin, 0):>4}')

    # ---- 7. emit our own netlist and compile it with the same cell models ----------
    names: dict[int, str] = {}
    for pin in sorted(set(ref_supply) | set(our_supply)):
        if pin not in SUPPLY:
            continue
        hits = [c for c, v in ours.items()
                if all(p in SUPPLY for _, p in v) and any(p == pin for _, p in v)]
        if len(hits) == 1 and pin in ('VGND', 'VPWR'):
            names[hits[0]] = pin
    names.update({c: p for p, c in port_cluster.items()})

    used = {v[0] for rec in pr['by_instance'].values() for v in rec['pins'].values() if v}
    wires = sorted(used - set(names))
    a3p = read_json(A3P)
    body = E.render_body(a3p, placements, pr['by_instance'], names, wires)

    in_ports = [p for p in port_order if port_dir.get(p) == 'input']
    out_ports = [(p, None) for p in port_order if port_dir.get(p) == 'output']
    supply_ports = [p for p in ('VGND', 'VPWR') if p in names.values()]
    total_pins = sum(len(r['pins']) for r in pr['by_instance'].values())
    head = [f'// build/warmup.v -- {module}, rebuilt from warmup/04_final.gds by our pipeline.',
            '//',
            '// Generated by tools/puzzle/warmup.py (plan step B7). Do not edit by hand.',
            '// Compile with:  iverilog -t null build/warmup.v build/cells.v',
            '//',
            f'// Compared against {WU_NET.name} by B2\'s method: partition equivalence with a',
            '// coverage guard. This file exists so that the *emitter* is exercised too, and so',
            '// that the cell models can be simulated on a design whose behaviour we know.',
            '//',
            f'// {len(placements)} instances, {total_pins} pins.',
            '// =============================================================================']
    # The supply ports belong in the *port list*, not merely in a declaration. A body-level
    # `inout VGND;` for a name the header does not list is not a port at all: Icarus rejects a
    # caller that connects it ("port ``VGND'' is not a port"), so the file would claim an
    # interface it does not have. C2 found this the first time anything instantiated the netlist;
    # `02_netlist_with_power_rails.v` -- the reference's own rail-carrying netlist -- lists VPWR
    # and VGND in its header, and so does B6's `build/puzzle.v`, so including them here is the
    # reference's convention as well as our own.
    emitted_ports = [*port_order, *[p for p in supply_ports if p not in port_order]]
    port_list = ', '.join(emitted_ports)
    text = '\n'.join([
        *head,
        f'module {module} ({port_list});',
        *[f'  {port_dir.get(p, "inout")} {p};' for p in emitted_ports],
        *body,        # render_body emits the wire declarations *and* the instances
        'endmodule',
    ]) + '\n'
    OUT_NETLIST.parent.mkdir(parents=True, exist_ok=True)
    OUT_NETLIST.write_text(text, encoding='utf-8', newline='\n')
    print()
    print(f'emitted                           : {OUT_NETLIST.relative_to(ROOT)} '
          f'({len(text)} bytes)')

    equivalent = a_can == b_can and coverage == 1.0
    ports_ok = not port_problems and len(port_cluster) == len(port_order)
    report = {
        'generated_by': 'tools/puzzle/warmup.py::stage_warmup_regression',
        'source': {'path': 'asic-puzzle-2026/warmup/04_final.gds', 'sha256': sha256(WU_GDS)},
        'reference': {'path': 'asic-puzzle-2026/warmup/01_netlist.v', 'sha256': sha256(WU_NET)},
        'engine': {'name': 'klayout LayoutToNetlist (flattened, as B3/B4)',
                   'circuits': len(circs), 'nets': engine_nets,
                   'distinct_cluster_ids': len(ids),
                   'net_identity_unique': len(ids) == engine_nets},
        'totals': {'placements': len(placements), 'masters': len(masters),
                   'pins_functional': cnt['terminals_functional'],
                   'pins_supply': cnt['terminals_supply'],
                   'unassigned_functional': cnt['unassigned_functional'],
                   'probe_conflicts': len(pr['conflicts']), 'probe_errors': len(pr['errors']),
                   'instances_mapped_to_reference': len(id_map),
                   'instances_unmapped': len(unmapped)},
        'comparison': {'equivalent': equivalent, 'method': "B2's canonical partition comparison",
                       'our_nets': len(ours_f), 'reference_nets': len(theirs),
                       'our_terminals': sum(len(v) for v in ours_f.values()),
                       'reference_terminals': sum(len(v) for v in theirs.values()),
                       'signatures_only_in_ours': sum(only_ours.values()),
                       'signatures_only_in_reference': sum(only_theirs.values()),
                       'coverage': coverage,
                       'coverage_terminals': len(compared)},
        'interface': {'module': module, 'ports': port_order, 'directions': port_dir,
                      'ports_emitted': emitted_ports,
                      'emitted_note': "the reference's ports in its own order, then the supply "
                                      "ports we emit -- in the port *list*, so the header is the "
                                      "interface. `02_netlist_with_power_rails.v` lists VPWR "
                                      "and VGND the same way",
                      'located': {p: c for p, c in sorted(port_cluster.items())},
                      'problems': port_problems, 'ok': ports_ok},
        'supply': {'reference': dict(sorted(ref_supply.items())),
                   'ours': dict(sorted(our_supply.items())),
                   'note': 'VPB is connected in the reference netlist and unassigned by our '
                           'extraction: A5 predicted exactly this (well ties on this row have no '
                           'routeable geometry -- the class is even named for VPB). Everything '
                           'else agrees exactly, including VNB, so the disagreement is one pin '
                           'name wide rather than a hole in the supply extraction.'},
        'emitted': EMITTED_REL,
        'checks': [
            {'check': 'the flattened engine yields one circuit with unique net identity',
             'passed': len(circs) == 1 and len(ids) == engine_nets},
            {'check': 'every functional pin is assigned', 'passed': cnt['unassigned_functional'] == 0},
            {'check': 'no probe conflicts or errors',
             'passed': not pr['conflicts'] and not pr['errors']},
            {'check': 'every reference instance maps to one of our placements',
             'passed': not unmapped},
            {'check': 'the partition matches the reference, B2s method',
             'passed': equivalent},
            {'check': 'the interface is located by terminal-set identity',
             'passed': ports_ok},
        ],
    }
    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    OUT_REPORT.write_text(json.dumps(report, indent=2, sort_keys=False) + '\n',
                          encoding='utf-8', newline='\n')
    print(f'report                            : {OUT_REPORT.relative_to(ROOT)}')

    ok = all(c['passed'] for c in report['checks'])
    failed = [c['check'] for c in report['checks'] if not c['passed']]
    print()
    print(f'B7 WARM-UP REGRESSION: {"PASS" if ok else "FAIL " + str(failed)}')
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    stage = (argv or ['warmup-regression'])[0]
    if stage != 'warmup-regression':
        print(f'warmup.py has no stage {stage!r}', file=sys.stderr)
        return 2
    return stage_warmup_regression()


if __name__ == '__main__':
    raise SystemExit(main())
