#!/usr/bin/env python3
"""check_stepB7.py -- executable gate for step B7 (warm-up end-to-end regression).

B7 is the Phase B gate: the pipeline must reproduce a design whose real netlist we hold. The
oracle is `warmup/01_netlist.v`, and the comparison itself is B2's canonical partition comparison
-- B7's job is to have run the *same* machinery as B3-B6 (flattened engine, B4's prober, B6's
renderer) rather than the separate hierarchical path B2's spike used.

What this gate checks, and what it deliberately does not:

* **It regenerates.** Both B7 outputs are regenerated hermetically and compared byte for byte,
  so the committed report and `build/warmup.v` are exactly what the committed code produces from
  the committed upstream artifacts. That is the strongest statement available here, and it is
  re-run every time the suite runs.
* **It re-parses the reference itself.** Module name, port list and directions, instance count
  and the reference's own net/terminal counts are re-derived from `01_netlist.v` in this file, and
  the report's numbers must match them.
* **It re-parses our own output.** `build/warmup.v` is read back: the module header must equal the
  reference's, the master multiset must equal the reference's, and the shared structural
  invariants (how many nets carry two or more signal terminals) must agree with the reference.
* **It does not re-implement the partition comparison.** That comparison is B2's, and B2's own
  gate independently re-derives it; duplicating it here would be a second implementation that
  could only ever agree or confuse. What is checked here is that B7 ran *that* method, on the
  flattened engine, and that its floors are not vacuous.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.checks._regen import regenerate_many                  # noqa: E402
from tools.puzzle import warmup as W                             # noqa: E402
from tools.puzzle.connect import (SUPPLY, WU_DEF, WU_NET, parse_def_placements,   # noqa: E402
                                  parse_netlist, read_json)

A3P = ROOT / 'recon' / 'derived' / 'pin_names.json'
CELLS_V = ROOT / 'build' / 'cells.v'

results: list[tuple[str, object, object]] = []


def check(label: str, actual, expected) -> None:
    results.append((label, actual, expected))


def main() -> int:
    report_before = W.OUT_REPORT.read_bytes()
    v_before = W.OUT_NETLIST.read_bytes()
    report = read_json(W.OUT_REPORT)
    a3p = read_json(A3P)

    # ---- 1. regenerate both outputs hermetically ----------------------------------
    rc, produced, _ = regenerate_many(
        'tools.puzzle.warmup', ('OUT_NETLIST', 'OUT_REPORT'), 'warmup-regression',
        names={'OUT_NETLIST': 'warmup.v', 'OUT_REPORT': 'warmup_b7.json'})
    check('the stage runs clean', rc, 0)
    check('build/warmup.v regenerates byte for byte', produced['OUT_NETLIST'], v_before)
    check('the report regenerates byte for byte', produced['OUT_REPORT'], report_before)
    check('regeneration did not write into the tree',
          (W.OUT_NETLIST.read_bytes(), W.OUT_REPORT.read_bytes()),
          (v_before, report_before))

    # ---- 2. the reference, re-parsed here ----------------------------------------
    ref_text = WU_NET.read_text(encoding='utf-8')
    module, ports, port_dir = W.parse_module_ports(ref_text)
    ref_insts, _ = parse_netlist(ref_text)
    def_place = parse_def_placements(WU_DEF.read_text(encoding='utf-8'))
    ref_signal = {name: {p: n for p, n in info['pins'].items() if p not in SUPPLY}
                  for name, info in ref_insts.items()}
    ref_nets = {n for v in ref_signal.values() for n in v.values()}
    ref_terms = sum(len(v) for v in ref_signal.values())
    check('the reference is a 230-instance design', len(ref_insts), 230)
    check('and a DEF with 230 components', len(def_place), 230)
    check('the reference module and ports', (module, ports),
          ('adder_demo', ['A', 'B', 'S', 'clk', 'en', 'rst_n']))
    check('the reference port directions',
          {p: port_dir[p] for p in ports},
          {'A': 'input', 'B': 'input', 'S': 'output', 'clk': 'input',
           'en': 'input', 'rst_n': 'input'})
    check('the report quotes the reference net count', report['comparison']['reference_nets'],
          len(ref_nets))
    check('the report quotes the reference terminal count',
          report['comparison']['reference_terminals'], ref_terms)

    # ---- 3. our own emitted netlist, re-parsed here ------------------------------
    text = W.OUT_NETLIST.read_text(encoding='utf-8')
    m = re.search(r'^module\s+(\w+)\s*\(([^)]*)\);', text, re.M)
    our_headers = ([p.strip() for p in m.group(2).split(',')] if m else [])
    our_insts = {}
    for line in text.splitlines():
        mm = re.match(r'^  (sky130_fd_sc_hd__\w+)\s+(\w+)\s+\((.*)\);$', line)
        if mm:
            conns = {}
            for part in [p.strip() for p in mm.group(3).split(',')]:
                cm = re.match(r'^\.(\w+)\((.*)\)$', part)
                if cm:
                    conns[cm.group(1)] = cm.group(2)
            our_insts[mm.group(2)] = {'master': mm.group(1), 'conns': conns}
    check('our module is named as the reference is', m.group(1) if m else None, module)
    # The reference's ports in the reference's own order, then the supply ports we emit. They must
    # be *in the list*: a body-level `inout VGND;` for a name the header omits is not a port at
    # all, so the file would advertise an interface it does not have. C2 found exactly that by
    # instantiating the netlist -- and `02_netlist_with_power_rails.v` lists VPWR and VGND the
    # same way, so this is the reference's convention too.
    check('our port list is the reference s, followed by the supplies we emit',
          (our_headers[:len(ports)], sorted(set(our_headers) - set(ports))),
          (ports, ['VGND', 'VPWR']))
    check('we emit 230 instances', len(our_insts), 230)
    check('we place the same multiset of cells as the reference',
          Counter(i['master'] for i in our_insts.values()),
          Counter(i['cell'] for i in ref_insts.values()))
    check('every instance carries its master pin set',
          sorted({iid for iid, rec in our_insts.items()
                  if sorted(rec['conns']) != sorted(a3p['masters'][rec['master']]['pins'])}),
          [])
    # The structural invariant of the partition that is comparable without a name mapping: how
    # many signal nets carry two or more terminals. A mis-connection changes a net's size, and
    # single-terminal nets are excluded because the reference simply omits pins it left unused.
    # Supply pins are dropped from our side too: `01_netlist.v` is the pre-rails netlist, so
    # comparing our VGND/VPWR nets against it would be comparing two different things.
    def big_nets(part: dict[str, dict]) -> int:
        sizes = Counter()
        for rec in part.values():
            for pin, net in rec.items():
                if net and pin not in SUPPLY:
                    sizes[net] += 1
        return sum(1 for n, k in sizes.items() if k >= 2)

    check('the same number of multi-terminal signal nets as the reference',
          big_nets({iid: r['conns'] for iid, r in our_insts.items()}),
          big_nets(ref_signal))

    # ---- 4. the models cover a design they were not generated for -----------------
    cells_text = CELLS_V.read_text(encoding='utf-8')
    models = {c.split(' ', 1)[0].split('(', 1)[0].strip()
              for c in cells_text.split('\nmodule ')[1:]}
    used = {i['master'] for i in our_insts.values()}
    check('every warm-up cell type has a model in build/cells.v', sorted(used - models), [])
    check('the warm-up uses a strict subset of the chip models', used <= models, True)
    check('and it is a real subset, not the whole library (floor 10)', len(used) >= 10, True)

    # ---- 5. it compiles, and the compiler can fail --------------------------------
    iverilog = shutil.which('iverilog')
    if iverilog is None:
        check('iverilog is on PATH', None, 'iverilog')
    else:
        r = subprocess.run([iverilog, '-Wall', '-t', 'null', str(W.OUT_NETLIST), str(CELLS_V)],
                           capture_output=True, text=True)
        check('iverilog -Wall -t null accepts the warm-up netlist with its models',
              (r.returncode, r.stderr.strip()), (0, ''))
        # Compiling the file proves it is well formed; it does not prove its interface is real.
        # This is the check that would have caught the missing supply ports: a caller must be able
        # to connect every port this netlist is *documented* to have -- the reference's ports plus
        # the supplies B7 emits, both 230/230 in the supply table below -- and it must use that
        # list, not the file's own header, or the check would agree with whatever the header says.
        with tempfile.TemporaryDirectory() as td:
            wrap = Path(td) / 'wrap.v'
            intended = [*ports, 'VGND', 'VPWR']
            decls = '\n'.join(f'  wire {p};' for p in intended)
            conns = ', '.join(f'.{p}({p})' for p in intended)
            wrap.write_text(f'module wrap;\n{decls}\n  {module} u({conns});\nendmodule\n',
                            encoding='utf-8', newline='\n')
            rw = subprocess.run([iverilog, '-t', 'null', str(wrap), str(W.OUT_NETLIST),
                                 str(CELLS_V)], capture_output=True, text=True)
            check('a caller can connect every port the netlist is documented to have',
                  (rw.returncode, rw.stderr.strip()[:300]), (0, ''))
        with tempfile.TemporaryDirectory() as td:
            broken = Path(td) / 'broken.v'
            broken.write_text('module broken; sky130_fd_sc_hd__nope u ();\nendmodule\n',
                              encoding='utf-8', newline='\n')
            rb = subprocess.run([iverilog, '-t', 'null', str(broken)], capture_output=True)
            check('the same invocation rejects a broken file', rb.returncode != 0, True)

    # ---- 6. the report's own claims, and their floors -----------------------------
    checks = {c['check']: c['passed'] for c in report['checks']}
    check('no report check is failing', sorted(k for k, v in checks.items() if not v), [])
    check('the partition comparison is claimed equivalent',
          report['comparison']['equivalent'], True)
    check('and it covered the reference completely', report['comparison']['coverage'], 1.0)
    check('coverage is not vacuous (floor 200 terminals)',
          report['comparison']['coverage_terminals'] >= 200, True)
    check('the comparison is over a real number of nets (floor 40)',
          report['comparison']['reference_nets'] >= 40, True)
    check('our partition has the same net count as the reference',
          report['comparison']['our_nets'], report['comparison']['reference_nets'])
    check('no signature exists on one side only',
          (report['comparison']['signatures_only_in_ours'],
           report['comparison']['signatures_only_in_reference']), (0, 0))
    check('every functional pin was assigned on the warm-up',
          report['totals']['unassigned_functional'], 0)
    check('no probe conflicts or errors',
          (report['totals']['probe_conflicts'], report['totals']['probe_errors']), (0, 0))
    check('every reference instance mapped to one of our placements',
          report['totals']['instances_unmapped'], 0)
    check('the flattened engine gave one circuit with unique net identity',
          report['engine']['net_identity_unique'], True)

    # ---- 7. the interface, located by terminal-set identity -----------------------
    check('all 6 ports were located on our side',
          sorted(report['interface']['located']), sorted(ports))
    check('with no port problems', report['interface']['problems'], [])
    check('the ports landed on 6 distinct nets',
          len(set(report['interface']['located'].values())), len(ports))

    # ---- 8. supply: the one place we are expected to disagree with the reference ---
    ref_supply = report['supply']['reference']
    our_supply = report['supply']['ours']
    # The claim is narrow on purpose. We DO recover the rails and VNB; the one pin we cannot see
    # is VPB, which is A5's predicted class (a well tie with no routeable geometry). Asserting
    # "VPB/VNB are both invisible" -- the first version of this check -- was simply wrong about
    # our own extraction, and B7's own stage output is what showed it.
    check('the only supply pin we cannot see is VPB, and everything else agrees',
          {p: (ref_supply.get(p, 0), our_supply.get(p, 0)) for p in sorted(ref_supply)},
          {'VGND': (230, 230), 'VNB': (137, 137), 'VPB': (137, 0), 'VPWR': (230, 230)})
    check('the reference does connect VPB, so the omission is ours to explain',
          ref_supply.get('VPB', 0) > 0, True)
    check('and we recover VNB, which is the control for that explanation',
          our_supply.get('VNB', 0), ref_supply.get('VNB', 0))

    # ---- 9. anti-vacuity ----------------------------------------------------------
    n_checks = len(results) + 1
    check('anti-vacuity: at least 30 checks ran', n_checks >= 30, True)

    n_fail = 0
    for label, actual, expected in results:
        if actual == expected:
            print(f'  PASS  {label}')
        else:
            n_fail += 1
            print(f'  FAIL  {label}')
            print(f'          got  {actual!r}')
            print(f'          want {expected!r}')
    print()
    print(f'{len(results) - n_fail} passed, {n_fail} failed, 0 skipped')
    if n_fail:
        print(f'STEP B7 GATE: FAIL ({n_fail} failing)')
        return 1
    print('STEP B7 GATE: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
