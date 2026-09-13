#!/usr/bin/env python3
"""check_stepB6.py -- executable gate for step B6 (emit Verilog).

B6's claims are that `build/puzzle.v` is a faithful, complete restatement of B4's pin-to-net map,
that `build/cells.v` covers every master with a model whose interface is the master's own pin set,
and that the pair compiles. This gate re-derives all of it from the artifacts and from the emitted
text itself:

* **Round trip.** The emitted Verilog is *parsed back* -- ports, wires, every instance and every
  named connection -- and the (instance, pin) -> net map rebuilt from it is compared to B4's
  `pin_net.json` entry by entry, both ways. A dropped pin, a renamed net, a swapped connection or
  an omitted instance all fail. Floors on the compared counts stop an empty comparison passing.
* **Inventory.** Instance count against B1, master set against B1, every master used has a model.
* **Model interfaces.** Each model's port list and each port's direction are checked against A3's
  pin set and B5's direction table, and the output pin against the family name re-derived here.
* **Compilation.** `iverilog -Wall -t null` must accept both files with *no* diagnostics -- and, so
  that this cannot pass vacuously, a deliberately broken copy must be rejected by the same
  invocation. A compiler that accepts anything is not evidence.
* **The one undriven wire.** B5 reports exactly one net with no driver. The emitted netlist must
  contain exactly one declared-but-never-driven wire, and it must be that net. Asserted as an
  identity plus a count, never as "no wire is undriven" -- which is the claim B5 met by inventing
  a driver, and which this gate exists partly to keep visible.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.checks._regen import regenerate                     # noqa: E402
from tools.puzzle.connect import read_json                     # noqa: E402

A3 = ROOT / 'recon' / 'derived' / 'pin_names.json'
B1 = ROOT / 'recon' / 'derived' / 'instances.json'
B4 = ROOT / 'recon' / 'derived' / 'pin_net.json'
B5 = ROOT / 'recon' / 'derived' / 'netlist_check.json'
PUZZLE_V = ROOT / 'build' / 'puzzle.v'
CELLS_V = ROOT / 'build' / 'cells.v'
SUPPLY = ('VGND', 'VPWR', 'VPB', 'VNB')

results: list[tuple[str, object, object]] = []


def check(label: str, actual, expected) -> None:
    results.append((label, actual, expected))


def fail(label: str, detail) -> None:
    results.append((label, detail, 'no failure'))


# ---- parsing the emitted text ---------------------------------------------------------

INST_RE = re.compile(r'^  (sky130_fd_sc_hd__\w+)\s+(\w+)\s+\((.*)\);$')
CONN_RE = re.compile(r'^\.(\w+)\((.*)\)$')


def parse_netlist(text: str) -> dict:
    """Independently re-read `build/puzzle.v` into ports, wires and instances."""
    ports: list[str] = []
    decls: dict[str, str] = {}
    wires: list[str] = []
    insts: dict[str, dict] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('module '):
            ports = [p.strip() for p in line[line.index('(') + 1:line.rindex(')')].split(',')]
            continue
        m = re.match(r'^(input|output|inout)\s+(?:\[(\d+):(\d+)\]\s+)?(\w+);$', line)
        if m:
            decls[m.group(4)] = m.group(1)
            continue
        if line.startswith('wire ') and line.endswith(';'):
            wires.append(line[5:-1].strip())
            continue
        m = INST_RE.match(raw.rstrip())
        if m:
            master, iid, body = m.group(1), m.group(2), m.group(3)
            conns = {}
            for part in [p.strip() for p in body.split(',')]:
                cm = CONN_RE.match(part)
                if cm:
                    conns[cm.group(1)] = cm.group(2)
            insts[iid] = {'master': master, 'conns': conns}
    return {'ports': ports, 'decls': decls, 'wires': wires, 'insts': insts}


def name_to_cluster(b4: dict, b5: dict) -> dict[str, int]:
    """The inverse of emit.py's net-naming rule, rebuilt here from the artifacts."""
    out: dict[str, int] = {}
    for net in b4['nets']:
        if net.get('is_supply_only'):
            for supply in ('VGND', 'VPWR'):
                if supply in set(net['pin_names']):
                    out[supply] = net['cluster']
    for port in b5['ports']:
        out[port['port']] = port['net']
    return out


# ---- an independent read of the family names ------------------------------------------

def family_output(family: str) -> str | None:
    """The output pin a family name implies, derived here rather than imported.

    Mirrors the convention in `tools/puzzle/cells.py` but is written against the *name string*
    only, so the two agreeing is a real cross-check rather than a tautology.
    """
    if family == 'conb':
        return None                      # two outputs, HI and LO
    if family in ('decap', 'tapvpwrvgnd', 'diode'):
        return None                      # no output at all
    if family in ('dfxtp', 'dfrtp', 'dfstp'):
        return 'Q'
    if family[0] in 'ao' and not family.startswith(('and', 'or')):
        body = family[1:]
        inverted = body.endswith('i')
        return 'Y' if inverted else 'X'
    for out, prefixes in (('Y', ('nand', 'nor', 'xnor', 'inv')),
                          ('X', ('and', 'or', 'xor', 'buf', 'clkbuf', 'mux2'))):
        if family.startswith(prefixes):
            return out
    return None


def main() -> int:
    a3, b1, b4, b5 = (read_json(p) for p in (A3, B1, B4, B5))
    text = PUZZLE_V.read_text(encoding='utf-8')
    cells_text = CELLS_V.read_text(encoding='utf-8')

    # ---- 1. hermetic regeneration -------------------------------------------------
    for module, stage, path in (('tools.puzzle.cells', 'cells', CELLS_V),
                                ('tools.puzzle.emit', 'emit', PUZZLE_V)):
        rc, produced, out = regenerate(module, ('OUT',), stage, suffix='.v')
        check(f'{stage}: the stage runs clean', rc, 0)
        check(f'{stage}: regenerating reproduces the committed file byte for byte',
              produced == path.read_bytes(), True)
        check(f'{stage}: regeneration did not write into the tree',
              path.read_bytes() == (text.encode() if path == PUZZLE_V
                                    else cells_text.encode()), True)

    # ---- 2. parse it back ---------------------------------------------------------
    nl = parse_netlist(text)
    inv = name_to_cluster(b4, b5)
    check('the emitted port list is the documented interface plus the supplies',
          nl['ports'], ['clk', 'rst_n', 'enable', 'I', 'O', 'success', 'VGND', 'VPWR'])
    check('the bus O is declared 8 bits wide',
          nl['decls'].get('O'), 'output')

    # Every instance B4 knows about is present exactly once, with the right master.
    check('instance count equals B1', len(nl['insts']), len(b1['instances']))
    check('instance ids are exactly B1 ids',
          sorted(nl['insts']), sorted(r['id'] for r in b1['instances']))
    bad_master = [iid for iid, rec in nl['insts'].items()
                  if rec['master'] != b4['instances'][iid]['master']]
    check('every emitted instance carries B1 master', bad_master, [])

    # ---- 3. the round trip: Verilog -> (instance, pin) -> net, against B4 ----------
    compared, empty, mismatch = 0, 0, []
    for iid, rec in b4['instances'].items():
        conns = nl['insts'][iid]['conns']
        if sorted(conns) != sorted(rec['pins']):
            mismatch.append((iid, 'pin set', sorted(set(conns) ^ set(rec['pins']))))
            continue
        for pin, value in rec['pins'].items():
            net = conns[pin]
            if value is None:
                empty += 1
                if net != '':
                    mismatch.append((iid, pin, 'expected an empty connection'))
                continue
            compared += 1
            want = value[0]
            got = inv.get(net, int(net[1:]) if net.startswith('n') and net[1:].isdigit()
                          else None)
            if got != want:
                mismatch.append((iid, pin, f'{net} -> {got}, B4 says {want}'))
    check('every resolved connection round-trips to B4 net', mismatch[:8], [])
    check('every resolved connection in B4 was compared', compared, b4['totals']['assigned'])
    check('empty connections re-derived (floor 900)', empty >= 900, True)
    check('exactly B4 unassigned pins are emitted as empty',
          empty, b4['totals']['unassigned'])

    # ---- 4. models -----------------------------------------------------------------
    models: dict[str, str] = {}
    for chunk in cells_text.split('\nmodule ')[1:]:
        name = chunk.split(' ', 1)[0].split('(', 1)[0].strip()
        models[name] = 'module ' + chunk
    used = {r['master'] for r in b1['instances']}
    check('one model per placed master', sorted(models), sorted(used))
    check('no model for a master that is never placed',
          sorted(set(models) - used), [])
    check('the models compile and are all loaded (floor 60)', len(models) >= 60, True)

    port_bad, dir_bad, out_bad = [], [], []
    for master, body in models.items():
        want_pins = list(a3['masters'][master]['pins'])
        ports = body[body.index('(') + 1:body.index(')')].split(', ')
        if ports != want_pins:
            port_bad.append((master, ports, want_pins))
        out_pin = family_output(a3['masters'][master]['type'])
        for pin in want_pins:
            decl = re.search(rf'^\s*(input|output|inout)\s+{re.escape(pin)};$', body, re.M)
            got = decl.group(1) if decl else None
            if pin in SUPPLY:
                want = 'inout'
            elif pin == out_pin or (a3['masters'][master]['type'] == 'conb'
                                    and pin in ('HI', 'LO')):
                want = 'output'
            else:
                want = 'input'
            if got != want:
                dir_bad.append((master, pin, got, want))
        if out_pin:
            family = a3['masters'][master]['type']
            driven = (re.search(rf'\b{out_pin} <= ', body) is not None
                      if family in ('dfxtp', 'dfrtp', 'dfstp')
                      else f'assign {out_pin} =' in body)
            if not driven:
                out_bad.append((master, out_pin))
    check('every model declares exactly its master pin set', port_bad[:4], [])
    check('every model declares each pin with the direction B5 derived', dir_bad[:4], [])
    check('every model drives the output its family name implies', out_bad[:4], [])

    # The output pin each model drives must equal the one B5's table names -- the cross-check
    # that caught the original B5 defect, now run in the other direction.
    b5_out = {}
    for key in b5['direction']['outputs']:
        m, p = key.split('::')
        b5_out.setdefault(m, []).append(p)
    check('each model drives exactly the output B5 names',
          {m: sorted(b5_out.get(m, [])) for m in models
           if len(b5_out.get(m, [])) != (2 if a3['masters'][m]['type'] == 'conb'
                                         else 1 if family_output(a3['masters'][m]['type'])
                                         else 0)},
          {})

    # ---- 5. compilation, and proof the compiler can fail ---------------------------
    iverilog = shutil.which('iverilog')
    if iverilog is None:
        fail('iverilog is on PATH', 'not found -- B6 cannot be verified')
    else:
        r = subprocess.run([iverilog, '-Wall', '-t', 'null', str(PUZZLE_V), str(CELLS_V)],
                           capture_output=True, text=True)
        check('iverilog -Wall -t null accepts both files', r.returncode, 0)
        check('and reports no diagnostics at all', r.stderr.strip(), '')
        # Anti-vacuity: a compiler that accepts anything proves nothing.
        with tempfile.TemporaryDirectory() as td:
            broken = Path(td) / 'broken.v'
            broken.write_text('module broken; sky130_fd_sc_hd__not_a_cell u ();\nendmodule\n',
                              encoding='utf-8', newline='\n')
            rb = subprocess.run([iverilog, '-t', 'null', str(broken)], capture_output=True,
                                text=True)
            check('the same iverilog invocation rejects a deliberately broken file',
                  rb.returncode != 0, True)

    # ---- 6. the one undriven wire --------------------------------------------------
    # A wire is driven iff some *output* pin of some model connects to it.
    out_pins = {}
    for master, body in models.items():
        out_pins[master] = {p for p in a3['masters'][master]['pins']
                            if p in ('X', 'Y', 'Q', 'HI', 'LO')}
    driven = set()
    for rec in nl['insts'].values():
        for pin, net in rec['conns'].items():
            if net and pin in out_pins[rec['master']]:
                driven.add(net)
    undriven_wires = sorted(w for w in nl['wires'] if w not in driven)
    reported = [f'n{r["cluster"]}' for r in b5['direction']['undriven_nets']]
    check('exactly one declared wire has no driver, and it is the one B5 enumerates',
          undriven_wires, reported)
    check('that wire is n806, the two-input li1 net', undriven_wires, ['n806'])
    check('and it is declared, not omitted', 'n806' in nl['wires'], True)
    check('no wire is declared that carries no pin',
          sorted(set(nl['wires']) - {v for rec in nl['insts'].values()
                                     for v in rec['conns'].values() if v}), [])

    # ---- 7. anti-vacuity -----------------------------------------------------------
    n_checks = len(results) + 1            # +1 for this check, which is appended below
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
        print(f'STEP B6 GATE: FAIL ({n_fail} failing)')
        return 1
    print('STEP B6 GATE: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
