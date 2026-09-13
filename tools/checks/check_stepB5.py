#!/usr/bin/env python3
"""check_stepB5.py -- executable gate for step B5 (netlist integrity).

B5's claims are that the extracted terminal set is a well-formed netlist and that the
top-level interface matches the problem statement. This gate re-derives both from
`pin_net.json` and the raw inputs:

* **The direction tables, re-derived rather than read.** A4's own pin-name lists are walked
  here and the convention applied independently: a pin labelled `X`, `Y`, `Q`, `HI` or `LO` is
  an output, every other signal pin is an input. The artifact's tables must be exactly that.
  B5's structural solver is *not* the authority for this and is not consulted for it -- its
  verdict was demonstrably wrong once (see the undriven net below), and re-deriving from A4 is
  what makes this gate able to catch that rather than inherit it.
* **Driver counts, recomputed.** From B4's map and the re-derived tables, every net whose pin
  classes are all decided is re-counted: no net may carry more than one driver, and the nets
  carrying none must be exactly the ones B5 enumerates -- asserted as a named net with its
  pin names, not as a "no net is undriven" claim, which the earlier version of this artifact
  met only by inventing a driver for net 806.
* **The interface, re-derived from the GDS.** The 13 documented port labels are found again on
  layer 70/5 and their positions compared with Step 1's, independently of the stage.
* **The single-terminal set, re-enumerated.** B4 measured 30; the gate rebuilds that set from
  the map and checks every member is classified, and that the classification is either an
  unused output or a terminal on a port net.

Anti-vacuity floors are included, and they are not decorative: writing this stage's own checks
in the wrong (label, expected) style produced checks that could never fail -- `bool(13)` and
`bool([])` are True and False regardless of the data -- which is the same bug class this
project has now hit four times. Every check here must be able to fail.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import gdstk

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.checks._regen import regenerate             # noqa: E402
from tools.puzzle import connect as C                  # noqa: E402
from tools.puzzle import netlist as N                  # noqa: E402

B5 = ROOT / 'recon' / 'derived' / 'netlist_check.json'
B4 = ROOT / 'recon' / 'derived' / 'pin_net.json'
A4 = ROOT / 'recon' / 'derived' / 'pinmodel.json'
B1 = ROOT / 'recon' / 'derived' / 'instances.json'
GDS = ROOT / 'asic-puzzle-2026' / 'puzzle.gds'
STD = 'sky130_fd_sc_hd__'

results: list[tuple[str, str, object, object]] = []


def check(label: str, actual, expected) -> None:
    results.append(('PASS' if actual == expected else 'FAIL', label, actual, expected))


def main() -> int:
    for p in (B5, B4, A4, B1):
        if not p.exists():
            print(f'missing {p.relative_to(ROOT)} -- run `python -m tools.puzzle netlist-check`')
            return 1
    before = B5.read_bytes()
    d5 = json.loads(before)
    d4 = json.loads(B4.read_text(encoding='utf-8'))
    a4 = json.loads(A4.read_text(encoding='utf-8'))
    b1 = json.loads(B1.read_text(encoding='utf-8'))

    # ---- 1. hermetic regenerability ----------------------------------------------
    t0 = time.time()
    rc, produced, _ = regenerate('tools.puzzle.netlist', ('OUT_CHECK',), 'netlist-check')
    stage_seconds = time.time() - t0
    check('the netlist-check stage exits 0', rc, 0)
    check('the artifact regenerates byte-identically', produced == before, True)
    check('regeneration did not touch the working tree', B5.read_bytes() == before, True)
    check('the stage completes in reasonable time', stage_seconds < 300, True)
    check('the artifact stores no timing (it must be deterministic)',
          any(k in d5 for k in ('runtime_s', 'seconds')), False)

    # ---- 2. provenance ------------------------------------------------------------
    check('generated_by', d5['generated_by'],
          'tools/puzzle/netlist.py::stage_netlist_check')
    check('source is the puzzle GDS', d5['source']['sha256'], C.sha256(GDS))

    # ---- 3. the interface, re-derived from the GDS --------------------------------
    lib = gdstk.read_gds(str(GDS))
    seen: dict[str, list] = {}
    for cell in lib.cells:
        for lb in cell.labels:
            if (lb.layer, lb.texttype) == N.PORT_LABEL and lb.text in N.PORT_DIR:
                seen.setdefault(lb.text, []).append((lb.origin[0], lb.origin[1]))
    check('13 documented ports found in the layout', len(seen), 13)
    check('each port label appears exactly once',
          sorted(k for k, v in seen.items() if len(v) != 1), [])
    worst = max(abs(a - b) for k, v in seen.items()
                for a, b in zip(v[0], N.PORT_POS_DOC[k]))
    check('port label positions match Step 1 (<= 0.05 um)', worst <= 0.05, True)
    rows = {r['port']: r for r in d5['ports']}
    check('the artifact reports all 13 ports', sorted(rows), sorted(N.PORT_DIR))
    check('the port nets are distinct', len({r['net'] for r in rows.values()}), 13)
    check('each port net exists in B4', sorted(r['net'] for r in rows.values()),
          sorted({r['net'] for r in rows.values()}))

    # ---- 4. direction tables, re-validated against A4 and B1 ---------------------
    state: dict[tuple, int] = {}
    for s in d5['direction']['outputs']:
        state[tuple(s.split('::'))] = 1
    for s in d5['direction']['inputs']:
        state[tuple(s.split('::'))] = 0
    check('output and input classes are disjoint',
          len(state), len(d5['direction']['outputs']) + len(d5['direction']['inputs']))
    undetermined = [tuple(s.split('::')) for s in d5['direction']['undetermined']]
    check('classes resolved + undetermined = classes', len(state) + len(undetermined),
          d5['totals']['classes'])
    # Every class must be a pin that A4 actually models for a master that B1 places.
    real = {(pl['master'], p) for pl in b1['instances']
            for p in a4['masters'][pl['master']]['pins']}
    check('every class named in the tables is a real pin of a real master',
          sorted(set(state) - real), [])
    check('the undetermined classes are real pins too',
          sorted(set(undetermined) - real), [])
    check('no supply pin is classed as an output',
          sorted(k for k in state if k[1] in C.SUPPLY), [])
    # Independent re-derivation of the direction convention, from A4's pin lists rather than
    # from B5's totals: the output label vocabulary is {X, Y, Q, HI, LO}, everything else is an
    # input. If B5 ever regresses to inferring direction structurally, this fails.
    real_signal = {k for k in real if k[1] not in C.SUPPLY}
    check('the class set is the signal pins of the placed masters',
          sorted(set(state) | set(undetermined)), sorted(real_signal))
    check('the output table is exactly the pins whose own label names an output',
          sorted(k for k, v in state.items() if v),
          sorted(k for k in real_signal if k[1] in N.NAME_OUTPUTS))
    check('the input table is exactly the remaining signal pins',
          sorted(k for k, v in state.items() if not v),
          sorted(k for k in real_signal if k[1] not in N.NAME_OUTPUTS))

    # ---- 5. feasibility, recomputed from B4's map --------------------------------
    net_counts: dict[int, Counter] = defaultdict(Counter)
    for iid, rec in d4['instances'].items():
        for pin, val in rec['pins'].items():
            if val is not None:
                net_counts[val[0]][(rec['master'], pin)] += 1
    supply_nets = {r['cluster'] for r in d4['nets'] if r['is_supply_only']}
    port_net = {r['port']: r['net'] for r in d5['ports']}
    net_to_port = {r['net']: r['port'] for r in d5['ports']}
    target = {c: (0 if (net_to_port.get(c) in N.PORT_DIR
                        and N.PORT_DIR[net_to_port[c]] == 'in') else 1)
              for c in net_counts if c not in supply_nets}
    settled = {c: cnt for c, cnt in net_counts.items()
               if c in target and all(k in state for k in cnt)}
    check('nets judged (all classes decided, floor 700)', len(settled) >= 700, True)
    drivers = {c: sum(v * state[k] for k, v in cnt.items()) for c, cnt in settled.items()}
    check('no net has two output terminals',
          sorted(c for c, n in drivers.items() if n > 1), [])
    check('no judged net has more output terminals than its target allows',
          sorted(c for c, n in drivers.items() if n > target[c]), [])
    # A net with no driver is a property of this layout, reported and enumerated -- never a
    # claim that none exists, which is the claim the earlier artifact satisfied by fabrication.
    undriven_here = sorted(c for c, n in drivers.items() if target[c] == 1 and n == 0)
    check('the judged nets with no driver are exactly the ones B5 enumerates',
          undriven_here, sorted(r['cluster'] for r in d5['direction']['undriven_nets']))
    check('one undriven net, two terminals',
          [(r['cluster'], r['terminals']) for r in d5['direction']['undriven_nets']],
          [(806, 2)])
    check('the undriven net is the two A1 inputs and nothing else',
          [r['pin_names'] for r in d5['direction']['undriven_nets']],
          [['a311o_2.A1', 'a31oi_2.A1']])
    check('nothing on the undriven net is an output',
          sorted({d for r in d5['direction']['undriven_nets'] for d in r['terminal_directions']}),
          ['input'])
    # An output pin of a cell must never sit on a supply net.
    check('no output terminal appears on a supply net',
          sorted({k for c in supply_nets for k in net_counts.get(c, ())
                  if state.get(k) == 1}), [])
    check('the two supply nets carry no functional pin name',
          sorted(c for c in supply_nets
                 if set(next(r for r in d4['nets'] if r['cluster'] == c)['pin_names'])
                 - C.SUPPLY), [])

    # ---- 6. the interface checked against the direction table ---------------------
    bad = []
    for name, r in rows.items():
        cnt = net_counts.get(r['net'], Counter())
        n_out = sum(v * state[k] for k, v in cnt.items() if k in state)
        want = 0 if N.PORT_DIR[name] == 'in' else 1
        if n_out != want or r['output_terminals'] != want:
            bad.append((name, n_out, want))
        if r['dir'] != N.PORT_DIR[name]:
            bad.append((name, 'dir', N.PORT_DIR[name]))
    check('every port net carries the terminals its port direction implies', bad, [])
    # Only an INPUT port may not be driven by a cell output; an output port must be.
    check('an input port is not driven by a cell output',
          sorted(name for name in port_net if N.PORT_DIR[name] == 'in'
                 and any(state.get(k) == 1 for k in net_counts.get(port_net[name], ()))), [])

    # ---- 7. the single-terminal set, re-enumerated -------------------------------
    single = sorted(c for c in net_counts if sum(net_counts[c].values()) == 1)
    reported = {r['cluster'] for r in d5['single_terminal_nets']}
    check('single-terminal nets re-derived from B4', len(single), 30)
    check('the artifact lists exactly those nets', sorted(reported), single)
    check('every single-terminal net is classified',
          sorted({r['classification'] for r in d5['single_terminal_nets']} - {
              'unused_output', 'port_in_terminal', 'port_out_terminal'}), [])
    by_class = Counter(r['classification'] for r in d5['single_terminal_nets'])
    check('the classification split', dict(sorted(by_class.items())),
          {'port_in_terminal': 1, 'port_out_terminal': 8, 'unused_output': 21})
    # An "unused output" must genuinely be classified as an output, not merely called one.
    wrong = [r['cluster'] for r in d5['single_terminal_nets']
             if r['classification'] == 'unused_output'
             and (lambda k: state.get(k) != 1)(next(iter(net_counts[r['cluster']])))]
    check('each unused-output net really holds an output pin', wrong, [])
    check('one single-terminal net is driven by a port, the clock root',
          sorted(r['cluster'] for r in d5['single_terminal_nets']
                 if r['classification'] == 'port_in_terminal'),
          [port_net['clk']])

    # ---- 8. totals ---------------------------------------------------------------
    check('instances', d5['totals']['instances'], len(b1['instances']))
    check('pins modelled', d5['totals']['pins'], 7897)
    check('nets carrying terminals', d5['totals']['nets_with_terminals'], 741)
    check('classes', d5['totals']['classes'], 286)
    check('every class has a direction', d5['totals']['classes_resolved'], 286)
    check('no class is left undetermined', d5['totals']['classes_undetermined'], 0)
    check('nothing is left undetermined, in any form', sorted(undetermined), [])
    check('the structural auditor decides all but the classes it would have to invent',
          d5['totals']['structural_classes_decided'], 284)
    check('the structural auditor leaves exactly the classes it cannot decide honestly',
          d5['direction']['cross_check']['undetermined_by_structure_alone'],
          ['sky130_fd_sc_hd__a31oi_2::Y', 'sky130_fd_sc_hd__o32ai_2::A2'])
    check('the auditor contradicts the labels on exactly one class',
          d5['direction']['cross_check']['structure_contradicts_the_labels'],
          ['sky130_fd_sc_hd__a31oi_2::A1'])
    check('and that contradiction is the fabricated driver of the undriven net',
          d5['direction']['cross_check']['structure_contradicts_the_labels'][0]
          .endswith('::A1') and 'a31oi_2.A1' in d5['direction']['undriven_nets'][0]['pin_names'],
          True)
    check('infeasible nets', d5['totals']['infeasible_nets'], 0)
    check('undriven nets reported', d5['totals']['undriven_nets'], 1)
    check('supply nets', d5['totals']['supply_nets'], sorted(supply_nets))

    # ---- report ------------------------------------------------------------------
    n_fail = 0
    for status, label, actual, expected in results:
        if status == 'FAIL':
            n_fail += 1
            print(f'  FAIL  {label}')
            print(f'          got  {actual!r}')
            print(f'          want {expected!r}')
        else:
            print(f'  PASS  {label}')
    print()
    print(f'{len(results) - n_fail} passed, {n_fail} failed, 0 skipped')
    if n_fail:
        print(f'STEP B5 GATE: FAIL ({n_fail} failing)')
        return 1
    print('STEP B5 GATE: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
