#!/usr/bin/env python3
"""model_faults.py -- would the gate suite notice a wrong cell-model logic function?

A **diagnostic, not a gate**: `run_all.py` does not discover this file, and nothing else depends
on its exit code. `fault_inject.py` measures whether the gates notice a corrupted *artifact*; this
measures whether the *experiments the gates run* would notice a wrong *model* -- the internal
evaluator's implementation of a standard-cell's logic function -- if that model computed the wrong
thing. C1's power report (`recon/derived/c1_power.json`) already flags which combinational cell
models leave the 312-cycle reference replay unchanged when their output is complemented
("C1-silent"). This script complements each such model for real, everywhere it is instantiated in
the evaluator, and re-runs every experiment a gate performs, comparing against the unmutated
baseline:

  R      the reference replay, 312 cycles                           (C1/C4/C5/E1/E2 gates)
  E1     the winning vector at offsets 4..8, plus the four wrong classes    (E1 gate, in full)
  E2g    E2 gate's live stratified sample: 23 usable + 20 over-cap (every 8th) + 10 not-adjacent
  E2s    E2 stage's full set: all 189 boards of the swap family
  C5     the C5 gate's regenerated look-alike boards                        (C5 gate, in full)
  C4g    the C4 gate's trigger-set sample: cells 0, 10, .., 120
  C4f    all 121 single-star trigger sets                                   (C4 stage only)

`gate-detected` is true iff R, E1, E2g, C5 or C4g changed -- exactly the experiments a gate in the
suite performs. `stage-detected` also counts E2s and C4f, which only a *stage* (not a gate) runs in
full, so it is a weaker, informational signal, not a claim about what the suite would catch.

It uses the derived board and derived feed (`verdict.derived_board()` / `derived_feed()`, Task 6),
never `tools.target`, so this measurement is exactly as answer-free as the pipeline it is testing.

**Run it in a scratch clone.** Like `fault_inject.py`, this is a diagnostic meant to be run
deliberately, not part of the routine suite, and it is slow: measured, a shard takes several
minutes per family. Recorded results (4 shards, 2026-09-16) are in `docs/verification.md` §16.

Usage (from the repo root):

    .venv/Scripts/python tools/checks/model_faults.py --shard I N

Splits the C1-silent combinational families into N shards and measures shard I (0-indexed), so the
full sweep can run in parallel across N processes. Prints one RESULT line per family as it
completes, then a summary table over just this shard's families.
"""
from __future__ import annotations

import ast
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.puzzle import confirm as CF   # noqa: E402
from tools.puzzle import verdict as V    # noqa: E402


def parse_args(argv: list[str]) -> tuple[int, int]:
    if len(argv) != 3 or argv[0] != '--shard':
        print('usage: model_faults.py --shard I N', file=sys.stderr)
        raise SystemExit(2)
    shard, nshards = int(argv[1]), int(argv[2])
    if not (0 <= shard < nshards):
        print(f'--shard {shard} is not in range [0, {nshards})', file=sys.stderr)
        raise SystemExit(2)
    return shard, nshards


def family(nl, inst: str) -> str:
    return nl.names[nl.insts[inst]['master']]['type']


def pat(grid) -> list[int]:
    return [1 if ch == '*' else 0 for ch in ''.join(grid)]


def main(argv: list[str] | None = None) -> int:
    shard, nshards = parse_args(sys.argv[1:] if argv is None else argv)
    t0 = time.time()

    power = json.loads((ROOT / 'recon' / 'derived' / 'c1_power.json').read_text(encoding='utf-8'))
    silent = sorted({re.sub(r'_\d+$', '', r['model'].split('__')[-1]) for r in power['per_model']
                     if not r['caught'] and r['mutation'] is not None})

    m_rep = V.Machine(cycles=312)
    m_msg = V.Machine(cycles=V.MESSAGE_CYCLES)
    m_c4 = V.Machine(cycles=V.C4_CYCLES)
    machines = (m_rep, m_msg, m_c4)
    nl0 = m_msg.nl

    comb_fams = {family(nl0, g[0]) for g in nl0.comb}
    targets = [f for f in silent if f in comb_fams]
    skipped = [f for f in silent if f not in comb_fams]
    mine = targets[shard::nshards]
    print(f'silent families in c1_power: {len(silent)}; combinational and present: {len(targets)}; '
          f'not combinational here (skipped): {skipped}')
    print(f'shard {shard}/{nshards}: {mine}', flush=True)

    class_of, src = V.region_partition()
    fam = CF.swap_family(class_of, len(src['flops']))
    e2g_sample = fam['usable'] + fam['violating'][::8] + fam['not_adjacent']
    e2s_sample = CF.family_boards(fam)
    c5_boards = [b for b in V.two_switch_boards(40)
                 if b['all_ok'] and set(map(tuple, b['cells'])) != V.derived_board()]

    feed = [int(b) for b in V.derived_feed()]

    def msg(pattern: list[int]) -> tuple[str, int | None]:
        r = m_msg.message(pattern)
        return (r['text'], r['success_cycle_0based'])

    def trig(cells) -> list[tuple]:
        out = []
        for p in cells:
            final = m_c4.run([1 if i == p else 0 for i in range(121)])['final']
            out.append(tuple(sorted(k for k, q in final.items() if q == 1)))
        return out

    def measure() -> dict:
        res = {}
        res['R'] = m_rep.reference_replay()['mismatches']
        e1 = []
        for off in (4, 5, 6, 7, 8):
            m_msg.offset = off
            e1.append(msg(feed))
        m_msg.offset = V.WIN_FEED_OFFSET
        for v in ('0' * 121, '1' * 121, V.other_wrong_vector(), V.adjacent_vector()):
            e1.append(msg([int(b) for b in v]))
        res['E1'] = e1
        res['E2g'] = [msg(pat(b['grid'])) for b in e2g_sample]
        res['E2s'] = [msg(pat(b['grid'])) for b in e2s_sample]
        res['C5'] = [msg(V.pattern_from_cells(b['cells'])) for b in c5_boards]
        res['C4g'] = trig(range(0, 121, 10))
        res['C4f'] = res['C4g'] + trig([p for p in range(121) if p % 10])
        return res

    base = measure()
    print(f'baseline measured ({time.time() - t0:.0f}s): replay mismatches {base["R"]}, '
          f'C5 boards {len(c5_boards)}, E2g {len(e2g_sample)}, E2s {len(e2s_sample)}', flush=True)

    orig = [list(mm.nl.comb) for mm in machines]
    rows = []
    for f in mine:
        count = 0
        for mm in machines:
            new = []
            for inst, op, tree, ins in mm.nl.comb:
                if family(mm.nl, inst) == f:
                    tree = ast.Expression(body=ast.UnaryOp(op=ast.Invert(), operand=tree.body))
                    count += 1
                new.append((inst, op, tree, ins))
            mm.nl.comb[:] = new
        got = measure()
        for mm, o in zip(machines, orig):
            mm.nl.comb[:] = o
        det = {k: got[k] != base[k] for k in base}
        gate = det['R'] or det['E1'] or det['E2g'] or det['C5'] or det['C4g']
        stage = gate or det['E2s'] or det['C4f']
        rows.append((f, count // len(machines), det, gate, stage))
        print(f'RESULT {f:10s} instances {count // len(machines):3d}  '
              + ' '.join(f'{k}={"X" if v else "."}' for k, v in det.items())
              + f'  gate-detected={gate}  stage-detected={stage}  ({time.time() - t0:.0f}s)', flush=True)

    print()
    header = f'{"family":<12} {"instances":>9}  ' + '  '.join(f'{k:>4}' for k in base) \
        + f'  {"gate":>5}  {"stage":>5}'
    print(header)
    print('-' * len(header))
    for f, count, det, gate, stage in rows:
        print(f'{f:<12} {count:>9}  ' + '  '.join(f'{"X" if det[k] else ".":>4}' for k in base)
              + f'  {"X" if gate else ".":>5}  {"X" if stage else ".":>5}')
    caught = sum(1 for *_, gate, _stage in rows if gate)
    print(f'\nshard {shard}/{nshards}: {caught} of {len(rows)} families gate-detected '
          f'({time.time() - t0:.0f}s total)')
    print('SHARD DONE')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
