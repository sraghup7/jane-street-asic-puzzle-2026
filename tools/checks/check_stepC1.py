#!/usr/bin/env python3
"""check_stepC1.py -- executable gate for step C1 (byte-exact replay of example_inputs.vcd).

C1 is the Phase C gate, and it is the second of the project's *independent* oracles: the first
was B7 on the warm-up. Every earlier check compared our artifacts with evidence about
themselves; here the reference is a simulation of the real chip that we did not write and cannot
influence, so a wrong netlist or a wrong cell model has to show up as a value that disagrees.

What this gate does independently, and what it does not
------------------------------------------------------
* **It re-reads the waveform itself**, one identifier at a time, straight out of the raw text
  (`raw_changes`), and derives the whole expectation from the *transition list* -- counting and
  ordering changes -- rather than by sampling a table the way the stage does. The reader is
  necessarily similar to the stage's; the derivation is not.
* **It samples at a different instant.** The stage samples 1 ps after the rising edge; this gate
  samples *at* the edge (t = 5000 + 10000k). Both must give the same table, which is a real
  statement about the convention rather than a restatement of it.
* **It re-runs the simulation.** Three runs -- unforced, forced 0, forced 1 -- from the committed
  `build/puzzle.v` and `build/cells.v`, compared against its own derivation. The report's "we
  matched" is not taken as evidence of anything.
* **It regenerates the harness hermetically** and requires byte equality, so the committed
  `build/replay_tb.v` is exactly what the committed code produces from the committed VCD.
* **It carries a negative control** (section 7): the same harness with the stimulus moved by one
  cycle must *fail* this gate's comparison. Without that, a comparison that silently compared
  nothing would look identical to a correct one -- the F1 lesson from the Phase B review.
* **It does not claim the comparison is strong in the way the bit count suggests.** The reference
  is 294 idle cycles around 18 message cycles whose content is one constant string. That
  constrains the design's *timing* tightly and its message *content* weakly, and the gate asserts
  both the counts and that shape, rather than letting a large number imply more than it proves.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.checks._regen import regenerate_many                    # noqa: E402
from tools.puzzle import power as P                                 # noqa: E402
from tools.puzzle import simulate as S                              # noqa: E402

VCD = ROOT / 'asic-puzzle-2026' / 'example_inputs.vcd'
NETLIST_CHECK = ROOT / 'recon' / 'derived' / 'netlist_check.json'

HALF = 5000          # ps, the reference's half clock period -- re-derived below, not trusted
CYC0 = 5000          # ps, the first rising edge
CYCLE = 2 * HALF

results: list[tuple[str, object, object]] = []


def check(label: str, actual, expected) -> None:
    results.append((label, actual, expected))


# ---------------------------------------------------------------------------------
# the waveform, read one identifier at a time
# ---------------------------------------------------------------------------------
def header_ids(text: str) -> dict[str, tuple[str, int]]:
    """{name: (identifier, width)} from the `$var` lines, read raw."""
    out = {}
    for m in re.finditer(r'\$var\s+\w+\s+(\d+)\s+(\S+)\s+([^$]+?)\s*\$end', text):
        width, ident, name = int(m.group(1)), m.group(2), m.group(3).strip()
        out[name.split()[0]] = (ident, width)
    return out


def raw_changes(text: str, ident: str) -> list[tuple[int, str]]:
    """Every (time, value) change of one identifier, read from the raw text."""
    out: list[tuple[int, str]] = []
    t = 0
    for line in text.split('$enddefinitions $end', 1)[1].splitlines():
        line = line.strip()
        if not line or line[0] == '$':
            continue
        if line[0] == '#':
            t = int(line[1:])
            continue
        if line[0] == 'b':
            val, i = line[1:].split()
        else:
            val, i = line[0], line[1:]
        if i == ident:
            out.append((t, val))
    return out


def value_at(changes: list[tuple[int, str]], t: int, width: int) -> str:
    cur = None
    for tt, vv in changes:
        if tt <= t:
            cur = vv
        else:
            break
    return S._normalise(cur, width)


def main() -> int:
    tb_before = S.OUT_TB.read_bytes()
    report_before = S.OUT_REPORT.read_bytes()
    cells_before = S.CELLS.read_bytes()
    emitted_before = S.EMITTED.read_bytes()
    text_vcd = VCD.read_text(encoding='utf-8', errors='replace')
    ids = header_ids(text_vcd)
    report = json.loads(report_before.decode('utf-8'))

    # ---- 1. regenerate the harness and the report hermetically --------------------
    rc, produced, _ = regenerate_many(
        'tools.puzzle.simulate', ('OUT_TB', 'OUT_REPORT'), 'vcd-replay',
        names={'OUT_TB': 'replay_tb.v', 'OUT_REPORT': 'vcd_replay.json'})
    check('the stage runs clean', rc, 0)
    check('build/replay_tb.v regenerates byte for byte', produced['OUT_TB'], tb_before)
    check('the report regenerates byte for byte', produced['OUT_REPORT'], report_before)
    check('regeneration did not write into the tree',
          (S.OUT_TB.read_bytes(), S.OUT_REPORT.read_bytes()), (tb_before, report_before))

    # ---- 2. the interface the reference declares ---------------------------------
    check('the reference declares exactly clk/rst_n/enable/I/O[7:0]/success',
          sorted(ids), ['I', 'O', 'clk', 'enable', 'rst_n', 'success'])
    check('with the widths the interface needs',
          {n: ids[n][1] for n in sorted(ids)},
          {'I': 1, 'O': 8, 'clk': 1, 'enable': 1, 'rst_n': 1, 'success': 1})

    # ---- 3. the clock, re-derived ------------------------------------------------
    clk = raw_changes(text_vcd, ids['clk'][0])
    times = [t for t, _ in clk]
    gaps = sorted({times[i + 1] - times[i] for i in range(len(times) - 1)})
    rises = [t for t, v in clk if v == '1']
    check('the clock has one edge spacing', gaps, [HALF])
    check('and 625 edges', len(clk), 625)
    check('with the first rise at 5000 ps', rises[0], CYC0)
    check('so the waveform is 312 cycles long', len(rises), 312)
    check('its rising edges are 10000 ps apart and start at 5000 ps',
          sorted({r - (CYC0 + CYCLE * k) for k, r in enumerate(rises)}), [0])

    # ---- 4. the stimulus, re-derived from the change times -----------------------
    stim_cycles, stim_times = {}, {}
    for name in ('rst_n', 'enable', 'I'):
        ch = raw_changes(text_vcd, ids[name][0])
        stim_times[name] = ch
        check(f'{name}: changes only on falling edges',
              sorted({t for t, _ in ch if t % CYCLE != 0}), [])
        stim_cycles[name] = [value_at(ch, CYC0 + CYCLE * k, 1) for k in range(312)]

    enable = stim_cycles['enable']
    runs, start = [], None
    for k, v in enumerate(enable):
        if v == '1' and start is None:
            start = k
        elif v != '1' and start is not None:
            runs.append([start, k - 1])
            start = None
    check('enable rises twice, for 121 cycles each', [b - a + 1 for a, b in runs], [121, 121])
    check('in the windows the plan names', runs, [[4, 124], [160, 280]])
    v1 = ''.join(stim_cycles['I'][k] for k in range(4, 125))
    v2 = ''.join(stim_cycles['I'][k] for k in range(160, 281))
    check('the two feeds are 121-bit vectors', [len(v1), len(v2)], [121, 121])
    check('and they differ (two attempts, not one repeated)', v1 != v2, True)

    # ---- 5. the reference's outputs, re-derived ----------------------------------
    o_ch = raw_changes(text_vcd, ids['O'][0])
    nz = [(t, v) for t, v in o_ch if v.strip('b') not in ('0', 'x', 'z')]
    check('the output changes to a nonzero byte exactly 18 times', len(nz), 18)
    check('at the cycles the plan names',
          [CYC0 + CYCLE * k for k in [*range(125, 134), *range(281, 290)]],
          [t for t, _ in nz])
    byte_cycles = {125 + i: int(v.strip('b'), 2) for i, (t, v) in enumerate(nz[:9])}
    byte_cycles.update({281 + i: int(v.strip('b'), 2) for i, (t, v) in enumerate(nz[9:])})
    s1 = ''.join(chr(byte_cycles[125 + i]) for i in range(9))
    s2 = ''.join(chr(byte_cycles[281 + i]) for i in range(9))
    check('the first stream reads TRY AGAIN', s1, 'TRY AGAIN')
    check('the second stream reads TRY AGAIN', s2, 'TRY AGAIN')
    check('and they are the same constant message, not two different ones', s1 == s2, True)

    # The expectation table, built from the transition list: zeros, then the 18 bytes placed at
    # the cycles their own timestamps name. Nothing here samples the stage's table.
    exp_O = ['00000000'] * 312
    for k, (t, v) in zip([*range(125, 134), *range(281, 290)], nz):
        exp_O[k] = v.strip('b').rjust(8, '0')
    succ_ch = raw_changes(text_vcd, ids['success'][0])
    check('success changes exactly twice: x at 0 and 0 at the first rise',
          [(t, v.strip('b')) for t, v in succ_ch], [(0, 'x'), (CYC0, '0')])
    exp_succ = [value_at(succ_ch, CYC0 + CYCLE * k, 1) for k in range(312)]
    check('so success is low on every cycle', set(exp_succ), {'0'})

    # ---- 6. the artifact, against this file's own derivation ---------------------
    check('the report quotes the VCD it read and its hash',
          report['reference']['sha256'],
          hashlib.sha256(VCD.read_bytes()).hexdigest())
    check('the report agrees the waveform is 312 cycles', report['reference']['cycles'], 312)
    check('the report agrees on the clock geometry',
          (report['reference']['clock']['edges'], report['reference']['clock']['half_period_ps'],
           report['reference']['clock']['first_rise_ps'], report['reference']['clock']['uniform']),
          (625, HALF, CYC0, True))
    check('the report reference table for O equals this gate s derivation',
          report['reference_values']['O'], exp_O)
    check('the report reference table for success equals this gate s derivation',
          report['reference_values']['success'], exp_succ)
    check('the report reference stimulus equals this gate s derivation',
          [report['reference_values'][n] for n in ('rst_n', 'enable', 'I')],
          [stim_cycles[n] for n in ('rst_n', 'enable', 'I')])
    check('the report carries the same two feed vectors',
          sorted(report['stimulus']['feed_vectors'].values()), sorted([v1, v2]))
    check('the report decodes the same two streams',
          [(s['cycles'], s['text']) for s in report['decode']['streams']],
          [([125, 133], 'TRY AGAIN'), ([281, 289], 'TRY AGAIN')])
    check('the report claims no mismatches',
          (report['comparison']['output_bits_mismatched'],
           report['comparison']['stimulus_bits_mismatched']), (0, 0))
    check('the report does not claim more comparisons than exist',
          (report['comparison']['output_bits_compared'],
           report['comparison']['stimulus_bits_compared']), (312 * 9, 312 * 3))
    check('the report has no mismatch entries', report['comparison']['mismatches'], [])
    check('every report check passed',
          sorted(c['check'] for c in report['checks'] if not c['passed']), [])
    check('the report records no x or z in our outputs',
          report['comparison']['our_outputs_with_x_or_z'], 0)

    # ---- 7. the harness, re-run here, against this gate s derivation --------------
    vvp = ROOT / 'recon' / 'scratch' / 'gate_c1' / 'replay.vvp'
    rows, comp_rc, sim_rc = S.run_simulation(S.OUT_TB, vvp)
    check('the harness compiles and runs', (comp_rc, sim_rc), (0, 0))
    check('the harness printed one line per cycle', len(rows), 312)
    got_o = [r['O'] for r in rows]
    got_s = [r['success'] for r in rows]
    o_mis = [k for k in range(312) if got_o[k] != exp_O[k]]
    s_mis = [k for k in range(312) if got_s[k] != exp_succ[k]]
    check('our O matches the reference at every cycle, re-run here', o_mis, [])
    check('our success matches the reference at every cycle, re-run here', s_mis, [])
    check('so every one of the 2808 output bit positions agreed',
          sum(8 for _ in o_mis) + len(s_mis), 0)
    stim_bad = [k for k in range(312) for n in ('rst_n', 'enable', 'I')
                if rows[k][n] != stim_cycles[n][k]]
    check('and our harness drove the reference stimulus, bit for bit', stim_bad, [])
    check('our outputs contain no x or z',
          sum(1 for r in rows for n in ('O', 'success') if 'x' in r[n] or 'z' in r[n]), 0)

    # the undriven net
    probes = S.probe_wires()
    check('B5 still enumerates exactly one undriven net', len(probes), 1)
    check('and it is cluster 806, an input pin with no driver',
          (probes[0]['cluster'], probes[0]['pin_names']),
          (806, ['a311o_2.A1', 'a31oi_2.A1']))
    wire_text = S.EMITTED.read_text(encoding='utf-8')
    check('the emitted netlist declares it as a wire',
          bool(re.search(rf'^\s*wire\s+{probes[0]["wire"]}\s*;', wire_text, re.M)), True)
    check('it is read by exactly the two consumers B5 names',
          len(re.findall(rf'\(\.\w+\({probes[0]["wire"]}\)', wire_text)), 2)
    probe_vals = sorted({r['probes'][0] for r in rows})
    check('the undriven net reads an undefined value (z) on every cycle', probe_vals, ['z'])
    forced_rows = {}
    for fv in (0, 1):
        frows, _, _ = S.run_simulation(S.OUT_TB, vvp, force=fv)
        forced_rows[fv] = frows
        check(f'forcing the net to {fv} changes no output',
              [k for k in range(min(len(frows), 312))
               if frows[k]['O'] != exp_O[k] or frows[k]['success'] != exp_succ[k]], [])
        check(f'and forcing it to {fv} leaves our own unforced run identical',
              [k for k in range(min(len(frows), 312))
               if (frows[k]['O'], frows[k]['success']) != (got_o[k], got_s[k])], [])
    check('the report records the same forced-run result',
          {k: (v['matches_reference'], v['moved_count']) for k, v in report['forced_runs'].items()},
          {'0': (True, 0), '1': (True, 0)})
    check('the report records the z reading',
          report['undriven_probe']['n806']['values_seen'], ['z'])

    # ---- 8. the oracle's power, and this gate's own re-measurement of a sample ----
    # C1 matches 2808 bits, which sounds like strength and is not: it is a count of comparisons.
    # The power stage measures what the replay *misses* by negating each model in turn. Its
    # numbers are only worth quoting if they reproduce, so four models are re-measured here --
    # two the report says are caught and two it says are silent.
    pw_before = P.OUT_REPORT.read_bytes()
    rc, produced, _ = regenerate_many('tools.puzzle.power', ('OUT_REPORT',), 'model-power',
                                      names={'OUT_REPORT': 'c1_power.json'})
    check('the power stage runs clean', rc, 0)
    check('c1_power.json regenerates byte for byte', produced['OUT_REPORT'], pw_before)
    pw = json.loads(pw_before.decode('utf-8'))
    check('the power report classifies every module exactly once',
          (pw['totals']['modules'],
           pw['totals']['caught'] + pw['totals']['silent'] + pw['totals']['not_mutable']),
          (69, 69))
    check('it negates every model with logic and leaves the physical-only masters alone',
          (pw['totals']['mutated'], pw['totals']['not_mutable']), (66, 3))
    check('it asserts the baseline replay was clean before mutating',
          (pw['baseline']['output_bits_mismatched'], pw['baseline']['cycles']), (0, 312))
    check('every power check passed',
          sorted(c['check'] for c in pw['checks'] if not c['passed']), [])
    by_model = {r['model']: r for r in pw['per_model']}
    check('it records F1 s break (negated or2_2) as caught, early',
          (by_model[P.F1_BREAK]['caught'], by_model[P.F1_BREAK]['first_wrong_cycle'] < 10),
          (True, True))
    check('it records both consumers of the undriven net as silent',
          {m: by_model[m]['caught'] for m in P.UNDRIVEN_CONSUMERS},
          {m: False for m in P.UNDRIVEN_CONSUMERS})
    check('it does not claim the oracle is complete',
          pw['totals']['silent'] > 0 and pw['totals']['caught'] < pw['totals']['mutated'], True)

    head_text, modules = P.split_modules(S.CELLS.read_text(encoding='utf-8'))
    sample = [P.F1_BREAK, P.UNDRIVEN_CONSUMERS[0],
              'sky130_fd_sc_hd__inv_2', 'sky130_fd_sc_hd__or4_2']
    check('the sample spans both outcomes',
          (by_model[sample[0]]['caught'], by_model[sample[1]]['caught'],
           by_model[sample[3]]['caught']), (True, False, False))
    sample_dir = ROOT / 'recon' / 'scratch' / 'gate_c1'
    sample_dir.mkdir(parents=True, exist_ok=True)
    for name in sample:
        i = next(j for j, m in enumerate(modules) if P.module_name(m) == name)
        mutated, _ = P.negate(modules[i])
        variant = head_text + ''.join(mutated if j == i else m for j, m in enumerate(modules))
        path = sample_dir / f'cells_{name.split("__")[-1]}.v'
        path.write_text(variant, encoding='utf-8', newline='\n')
        saved, S.CELLS = S.CELLS, path
        try:
            srows, src, _ = S.run_simulation(S.OUT_TB, sample_dir / 'sample.vvp')
        finally:
            S.CELLS = saved
        got_wrong = len([k for k in range(min(312, len(srows)))
                         if srows[k]['O'] != exp_O[k] or srows[k]['success'] != exp_succ[k]])
        check(f'{name.split("__")[-1]}: re-measured here, {got_wrong} wrong cycles',
              (src, got_wrong), (0, by_model[name]['cycles_wrong']))
    check('the power measurement left build/cells.v alone',
          S.CELLS.read_bytes(), cells_before)

    # ---- 9. the negative control: this comparison must be able to fail ------------
    tb_text = tb_before.decode('utf-8')
    lines, shifted = [], False
    for ln in tb_text.splitlines():
        m = re.match(r"^(\s*)#(\d+)( .*enable = 1'b0;.*)$", ln)
        if m and not shifted:
            lines.append(f'{m.group(1)}#{int(m.group(2)) + CYCLE}{m.group(3)}')
            shifted = True
        else:
            lines.append(ln)
    check('the negative control found the line it means to move', shifted, True)
    variant = '\n'.join(lines) + '\n'
    check('and it really moved the stimulus', variant != tb_text, True)
    with tempfile.TemporaryDirectory() as td:
        vtb = Path(td) / 'replay_tb_shifted.v'
        vtb.write_text(variant, encoding='utf-8', newline='\n')
        vrows, vrc, _ = S.run_simulation(vtb, Path(td) / 'shifted.vvp')
        check('the shifted run still simulates', (vrc, len(vrows)), (0, 312))
        v_bad = [k for k in range(312) if vrows[k]['O'] != exp_O[k]]
        check('moving the stimulus one cycle makes this comparison FAIL', len(v_bad) > 0, True)
        check('and the shifted run really drove different stimulus',
              len([k for k in range(312) if vrows[k]['enable'] != stim_cycles['enable'][k]]) > 0,
              True)
        check('the unforced run and the shifted run are not the same data',
              [vrows[k]['O'] for k in range(312)] != got_o, True)

    # ---- 10. anti-vacuity, and the honest shape of the evidence ------------------
    check('anti-vacuity: the reference is one constant message, twice',
          (s1 == s2, len(nz), Counter(exp_O)['00000000']), (True, 18, 294))
    check('anti-vacuity: the comparison is over 2808 bits, not a handful',
          len(exp_O) * 9 >= 2808, True)
    check('anti-vacuity: our outputs are not all idle (the message really is ours)',
          sum(1 for b in got_o if b != '00000000'), 18)
    check('anti-vacuity: the two feed vectors are not degenerate',
          (set(v1), sorted(set(v2))), ({'0', '1'}, ['0', '1']))
    check('anti-vacuity: nothing in the tree was rewritten by this gate',
          (S.OUT_TB.read_bytes() == tb_before, S.OUT_REPORT.read_bytes() == report_before,
           S.CELLS.read_bytes() == cells_before, S.EMITTED.read_bytes() == emitted_before),
          (True, True, True, True))
    n_checks = len(results) + 1
    check('anti-vacuity: at least 75 checks ran', n_checks >= 75, True)

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
        print(f'STEP C1 GATE: FAIL ({n_fail} failing)')
        return 1
    print('STEP C1 GATE: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
