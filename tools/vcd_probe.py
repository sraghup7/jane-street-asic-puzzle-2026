#!/usr/bin/env python3
"""vcd_probe.py -- minimal, dependency-free VCD analyser for the Jane Street puzzle.

Reconstructs the top-level stimulus protocol from `example_inputs.vcd` by sampling
every signal on each rising clock edge, then reports:

  * timescale, clock period, total cycles
  * the per-cycle state of rst_n / enable / I
  * how many cycles `enable` is held high and which input bits are shifted in
  * every change of the 8-bit O bus, decoded as ASCII bytes with the cycle at which
    it appears
  * whether `success` ever asserts

Usage:
    python tools/vcd_probe.py asic-puzzle-2026/example_inputs.vcd [--csv out.csv]
"""
import argparse
import re
import sys
from bisect import bisect_right
from collections import OrderedDict

DIRECTIVES = ('$comment', '$date', '$end', '$enddefinitions', '$scope', '$timescale',
              '$upscope', '$var', '$version', '$dumpall', '$dumpvars', '$dumpoff',
              '$dumpon', '$dumpflush')


def parse_vcd(path):
    """Return (timescale, vars, changes, maxtime).

    vars    : OrderedDict  vcd_id -> {'name','width'}
    changes : dict        vcd_id -> [(time, value), ...]  value is str ('0','1','x','z')
                          or (base, bits_str) for vectors
    """
    timescale = None
    var_defs = OrderedDict()
    changes = {}
    t = 0
    maxtime = 0
    pending_scope = []

    with open(path, 'r', errors='replace') as fh:
        raw = fh.read()

    # ---- header: $var definitions -------------------------------------------------
    for m in re.finditer(r'\$var\s+(\S+)\s+(\d+)\s+(\S+)\s+([^\s$]+)\s*(?:\[[^\]]*\])?\s*\$end',
                         raw):
        kind, width, ident, name = m.group(1), int(m.group(2)), m.group(3), m.group(4)
        var_defs[ident] = {'name': name, 'width': width, 'kind': kind}
    m = re.search(r'\$timescale\s+(\S+)\s+\$end', raw)
    if m:
        timescale = m.group(1)

    # ---- body: everything after $enddefinitions -----------------------------------
    body = raw.split('$enddefinitions', 1)[1]
    for line in body.splitlines():
        line = line.strip()
        if not line or line in DIRECTIVES:
            continue
        if line.startswith('#'):
            t = int(line[1:])
            maxtime = max(maxtime, t)
            continue
        if line.startswith('b'):
            # vector change: b<bits><space><id>
            val, ident = line[1:].split(None, 1)
            ident = ident.strip()
            changes.setdefault(ident, []).append((t, ('b', val)))
            continue
        # scalar change: <value><id>  (value in 01xzXZ...)
        val, ident = line[0].lower(), line[1:].strip()
        if val in '01xz' and ident:
            changes.setdefault(ident, []).append((t, val))
    for v in changes.values():
        v.sort(key=lambda x: x[0])
    return timescale, var_defs, changes, maxtime


def value_at(changes, ident, when):
    """Value of `ident` at time `when` (last change at or before `when`)."""
    seq = changes.get(ident)
    if not seq:
        return None
    times = [c[0] for c in seq]
    i = bisect_right(times, when) - 1
    return seq[i][1] if i >= 0 else None


def decode(value):
    if value is None:
        return None
    if isinstance(value, tuple):
        _, bits = value
        return bits.lower()
    return value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('vcd')
    ap.add_argument('--csv')
    args = ap.parse_args()

    ts, var_defs, changes, maxtime = parse_vcd(args.vcd)
    by_name = {v['name']: i for i, v in var_defs.items()}

    print(f'file        : {args.vcd}')
    print(f'timescale   : {ts}')
    print(f'end time    : {maxtime} {ts}')
    print(f'signals     : ' + ', '.join(
        f'{v["name"]}[{v["width"]}]' for v in var_defs.values()))
    print()

    clk_id = by_name['clk']
    clk = changes[clk_id]
    # rising edges = transitions to '1'
    rises = [t for t, v in clk if v == '1']
    period = None
    if len(rises) > 1:
        deltas = {rises[i + 1] - rises[i] for i in range(len(rises) - 1)}
        period = min(deltas) if len(deltas) == 1 else f'mixed {sorted(deltas)}'
    print(f'clock       : {len(rises)} rising edges, first at t={rises[0]}, '
          f'last at t={rises[-1]}, period={period} {ts}')

    # cycle index: cycle 0 is the first rising edge
    def cycle_of(t):
        return bisect_right(rises, t) - 1

    # sample every signal just after each rising edge (setup is at the edge itself)
    keep = 1  # ps after the edge
    rows = []
    for i, t in enumerate(rises):
        rows.append({
            'cycle': i,
            't': t,
            'rst_n': decode(value_at(changes, by_name['rst_n'], t + keep)),
            'enable': decode(value_at(changes, by_name['enable'], t + keep)),
            'I': decode(value_at(changes, by_name['I'], t + keep)),
            'O': decode(value_at(changes, by_name['O'], t + keep)),
            'success': decode(value_at(changes, by_name['success'], t + keep)),
        })

    # --- protocol summary ---------------------------------------------------------
    def runs(key):
        out = []
        for r in rows:
            if out and out[-1][0] == r[key]:
                out[-1][2] = r['cycle']
            else:
                out.append([r[key], r['cycle'], r['cycle']])
        return out

    for key in ('rst_n', 'enable', 'I', 'success'):
        print(f'{key:8} runs  : ' + '  '.join(
            f'{v}@{a}-{b}' for v, a, b in runs(key)))
    print()

    en_hi = [r for r in rows if r['enable'] == '1']
    print(f'enable high : {len(en_hi)} cycles'
          + (f'  (cycles {en_hi[0]["cycle"]}..{en_hi[-1]["cycle"]})' if en_hi else ''))
    if en_hi:
        bits = ''.join(r['I'] or 'x' for r in en_hi)
        print(f'input bits  : {bits}')
        print(f'             : {len(bits)} bits, {bits.count("1")} ones')
    print()

    # --- O bus timeline -----------------------------------------------------------
    print('O bus changes (cycle : value : ascii):')
    prev = None
    bytes_seen = []
    for r in rows:
        if r['O'] is None or r['O'] == prev:
            continue
        prev = r['O']
        try:
            n = int(r['O'], 2)
            ch = chr(n) if 32 <= n < 127 else (f'\\x{n:02x}' if n else 'NUL')
        except ValueError:
            n, ch = None, '?'
        bytes_seen.append((r['cycle'], r['O'], n, ch))
        print(f'  cycle {r["cycle"]:4}  {r["O"]}  {n if n is not None else "?":>4}  {ch!r}')

    # --- assembled message(s): consecutive same-byte-per-cycle runs ---------------
    print()
    print('Assembled byte stream (one byte per cycle while changing):')
    stream = []
    for c, o, n, ch in bytes_seen:
        stream.append(ch if n else '\\0')
    joined = ''.join(stream)
    print(f'  {joined!r}')

    succ = [r['cycle'] for r in rows if r['success'] == '1']
    print()
    print(f'success high cycles: {succ if succ else "NEVER (consistent with a wrong input)"}')

    if args.csv:
        import csv
        with open(args.csv, 'w', newline='') as fh:
            # lineterminator='\n': csv defaults to \r\n, which would make this artifact
            # differ by platform while .gitattributes pins the repo to LF.
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), lineterminator='\n')
            w.writeheader()
            w.writerows(rows)
        print(f'\nper-cycle table written to {args.csv}')


if __name__ == '__main__':
    main()
