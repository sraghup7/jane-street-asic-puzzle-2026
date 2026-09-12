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


def main(argv: list[str]) -> int:
    stage = argv[0] if argv else 'pin-names'
    if stage != 'pin-names':
        print(f'pins.py does not provide stage {stage!r} yet', file=sys.stderr)
        return 2

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
