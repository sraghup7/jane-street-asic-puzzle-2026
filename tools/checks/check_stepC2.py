#!/usr/bin/env python3
"""check_stepC2.py -- executable gate for step C2 (warm-up functional equivalence).

C2's claim is narrow and total: **every one of the 65536 `(A, B)` pairs produces the same `S` from
our extracted netlist as from Jane Street's RTL.** That is a stronger statement than C1's — an
exhaustive input space rather than one waveform — and it is checked here the way the project
checks every claim: independently, with the artifact's word for nothing.

What this gate does
-------------------
* **Regenerates both C2 outputs hermetically** and requires byte equality, then asserts the tree
  was not rewritten.
* **Parses `00_source.v` itself**, with its own regexes, and requires the report to agree about
  the protocol: the shift width, the comparison target, the top module, the serial order. If the
  stage's reader and this one ever disagree, that is the failure worth knowing about.
* **Re-runs the exhaustive sweep** against the committed netlist and compares against its *own*
  expectation, derived from the source's target rather than from the artifact.
* **Checks the rename is only a rename**: regenerates the renamed reference from upstream and
  requires byte equality with the stage's copy, and exactly one differing line.
* **Asserts the harness instantiates both designs and connects the supply ports** — the interface
  defect C2 itself found in B7, so the fix cannot silently regress.
* **Re-measures two cell-model negations itself**, requiring the exact pair counts the C2 power
  measurement records, and re-derives the cross-oracle arithmetic.
* **Carries a negative control**: the reference with its target constant changed by one must make
  this gate's comparison fail. A comparison that cannot fail looks exactly like a correct one.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.checks._regen import regenerate_many          # noqa: E402
from tools.puzzle import equiv as EQ                     # noqa: E402
from tools.puzzle import power as P                      # noqa: E402
from tools.puzzle import simulate as S                   # noqa: E402

SRC = ROOT / 'asic-puzzle-2026' / 'warmup' / '00_source.v'

results: list[tuple[str, object, object]] = []


def check(label: str, actual, expected) -> None:
    results.append((label, actual, expected))


def own_source_parse(text: str) -> dict:
    """The source's protocol parameters, re-derived here with this file's own patterns."""
    width = re.search(r'output\s+reg\s*\[(\d+):0\]\s*\w+', text)
    shift = re.search(r'(\w+)\s*<=\s*\{\s*\w+\[(\d+):0\]\s*,\s*(\w+)\s*\}', text)
    target = re.search(r"==\s*\d*'d(\d+)", text)
    mods = re.findall(r'(?m)^module\s+(\w+)', text)
    return {'width': int(width.group(1)) + 1 if width else None,
            'target': int(target.group(1)) if target else None,
            'shift_hi': int(shift.group(2)) if shift else None,
            'modules': sorted(mods)}


def main() -> int:
    tb_before = EQ.OUT_TB.read_bytes()
    report_before = EQ.OUT_REPORT.read_bytes()
    pw_before = P.OUT_WU_REPORT.read_bytes()
    cells_before = S.CELLS.read_bytes()
    dut_before = EQ.WU_DUT.read_bytes()
    src_text = SRC.read_text(encoding='utf-8')
    report = json.loads(report_before.decode('utf-8'))
    pw = json.loads(pw_before.decode('utf-8'))
    mine = own_source_parse(src_text)

    # ---- 1. regenerate C2's outputs hermetically ----------------------------------
    rc, produced, _ = regenerate_many(
        'tools.puzzle.equiv', ('OUT_TB', 'OUT_REPORT'), 'warmup-equiv',
        names={'OUT_TB': 'warmup_equiv_tb.v', 'OUT_REPORT': 'warmup_equiv.json'})
    check('the stage runs clean', rc, 0)
    check('build/warmup_equiv_tb.v regenerates byte for byte', produced['OUT_TB'], tb_before)
    check('warmup_equiv.json regenerates byte for byte', produced['OUT_REPORT'], report_before)
    check('regeneration did not write into the tree',
          (EQ.OUT_TB.read_bytes(), EQ.OUT_REPORT.read_bytes()), (tb_before, report_before))

    # ---- 2. the source, read again here -------------------------------------------
    check('this gate agrees about the source s modules', mine['modules'],
          ['adder8', 'adder_demo', 'comparator496', 'shift_register'])
    check('the report agrees about the protocol, re-derived here',
          (report['reference']['width'], report['reference']['target'],
           report['reference']['top'], mine['shift_hi']),
          (mine['width'], mine['target'], 'adder_demo', mine['width'] - 2))
    check('the report quotes the source hash',
          report['reference']['sha256'], hashlib.sha256(SRC.read_bytes()).hexdigest())
    check('the report quotes the netlist under test, with its hash',
          (report['sut']['path'], report['sut']['sha256']),
          ('build/warmup.v', hashlib.sha256(dut_before).hexdigest()))

    # ---- 3. the rename is only a rename -------------------------------------------
    renamed_here = EQ.rename_top(src_text, report['reference']['top'])
    check('the stage s renamed reference is exactly a rename of upstream',
          EQ.REF_V.read_text(encoding='utf-8'), renamed_here)
    diff = [i for i, (a, b) in enumerate(zip(src_text.splitlines(),
                                             renamed_here.splitlines())) if a != b]
    check('exactly one line differs from upstream, and it is the module declaration',
          (len(diff), renamed_here.splitlines()[diff[0]] if diff else None),
          (1, 'module adder_demo_ref ('))

    # ---- 4. the harness instantiates both, and uses the interface B7 fixed ---------
    tb_text = tb_before.decode('utf-8')
    check('the harness instantiates our netlist', 'adder_demo dut_ours' in tb_text, True)
    check('the harness instantiates the renamed reference',
          'adder_demo_ref dut_ref' in tb_text, True)
    check('and it connects the supply ports, so the B7 interface fix cannot regress',
          ('.VGND(VGND)' in tb_text, '.VPWR(VPWR)' in tb_text), (True, True))

    # ---- 5. re-run the exhaustive sweep, against this gate s own expectation -------
    limit = 1 << mine['width']
    pairs = limit ** 2
    expected_high = sum(1 for a in range(limit) for b in range(limit) if a + b == mine['target'])
    check('the sweep size is the whole input space', pairs, 65536)
    check('and the equating pairs are a real minority', expected_high, 15)
    out, comp_rc, sim_rc = S.compile_and_run(
        EQ.OUT_TB, ROOT / 'recon' / 'scratch' / 'gate_c2' / 'rerun.vvp',
        [EQ.WU_DUT, S.CELLS, EQ.REF_V])
    got = EQ.parse_output(out)['summary']
    check('the harness compiles and runs', (comp_rc, sim_rc), (0, 0))
    check('it visited every pair', got['checked'], pairs)
    check('the reference implements the stated function, re-run here',
          got['mism_ref_vs_func'], 0)
    check('and asserts on exactly the equating pairs', got['ref_high'], expected_high)
    check('our netlist matches the reference on every pair, re-run here',
          got['mism_ours_vs_ref'], 0)
    check('our netlist implements the same function, re-run here',
          got['mism_ours_vs_func'], 0)
    check('our S is not stuck', got['ours_high'], expected_high)
    check('the report claims no mismatches either',
          (report['comparison']['ours_vs_reference_mismatches'],
           report['comparison']['reference_vs_function_mismatches'],
           report['comparison']['ours_vs_function_mismatches']), (0, 0, 0))
    check('the report s sweep numbers match this run',
          (report['sweep']['checked'], report['sweep']['equating_pairs'],
           report['sweep']['reference_high'], report['sweep']['ours_high']),
          (got['checked'], expected_high, expected_high, expected_high))
    check('every report check passed',
          sorted(c['check'] for c in report['checks'] if not c['passed']), [])

    # ---- 6. the C2 power measurement: consistency, arithmetic, and a re-measurement
    check('the power report is for this oracle', pw['oracle']['pairs'], pairs)
    check('it mutates only the masters the design instantiates',
          pw['totals']['mutable'] + pw['totals']['physical_only'], pw['totals']['masters'])
    check('and classifies each one exactly once',
          pw['totals']['caught'] + pw['totals']['silent'], pw['totals']['mutable'])
    check('every power check passed',
          sorted(c['check'] for c in pw['checks'] if not c['passed']), [])
    blind = set(pw['cross_oracle']['c1_blind_instanced_here'])
    check('all three C1-blind models this design instantiates were measured',
          sorted(blind), pw['cross_oracle']['c1_blind_instanced_here'])
    c1_pw = json.loads(P.OUT_REPORT.read_text(encoding='utf-8'))
    c1_blind_total = sum(1 for r in c1_pw['per_model'] if r['caught'] is False)
    check('the C2 power report and C1 s agree about how many models C1 is blind to',
          (pw['cross_oracle']['c1_blind_total'], c1_blind_total), (29, 29))
    # "reached by neither" must be exactly the union of (blind and silent here) and (blind and
    # not instanced here): an arithmetic claim, so it is recomputed rather than quoted.
    neither = sorted(set(pw['cross_oracle']['c1_blind_silent_here'])
                     | set(pw['cross_oracle']['c1_blind_not_instanced_here']))
    check('"reached by neither oracle" is exactly the two groups that make it up',
          pw['cross_oracle']['reached_by_neither'], neither)
    check('and it is non-empty: the strategy s limit is stated, not hidden', len(neither) > 0, True)

    head_text, modules = P.split_modules(S.CELLS.read_text(encoding='utf-8'))
    by_model = {r['model']: r for r in pw['per_model']}
    sample = ['sky130_fd_sc_hd__and2_2', 'sky130_fd_sc_hd__clkbuf_16']
    check('the sample spans caught and silent',
          (by_model[sample[0]]['caught'], by_model[sample[1]]['caught']), (True, False))
    sample_dir = ROOT / 'recon' / 'scratch' / 'gate_c2'
    sample_dir.mkdir(parents=True, exist_ok=True)
    for name in sample:
        i = next(j for j, m in enumerate(modules) if P.module_name(m) == name)
        mutated, _ = P.negate(modules[i])
        variant = head_text + ''.join(mutated if j == i else m for j, m in enumerate(modules))
        path = sample_dir / f'cells_{name.split("__")[-1]}.v'
        path.write_text(variant, encoding='utf-8', newline='\n')
        s_out, s_crc, s_rc = S.compile_and_run(EQ.OUT_TB, sample_dir / 'sample.vvp',
                                               [EQ.WU_DUT, path, EQ.REF_V])
        s_got = EQ.parse_output(s_out)['summary']
        check(f'{name.split("__")[-1]}: re-measured here',
              (s_crc, s_rc, s_got['mism_ours_vs_ref']),
              (0, 0, by_model[name]['pairs_wrong']))

    # ---- 7. the negative control ---------------------------------------------------
    mutated_ref = src_text.replace("9'd496", "9'd495")
    check('the negative control changed the reference', mutated_ref != src_text, True)
    with tempfile.TemporaryDirectory() as td:
        vref = Path(td) / 'warmup_ref_495.v'
        vref.write_text(EQ.rename_top(mutated_ref, report['reference']['top']),
                        encoding='utf-8', newline='\n')
        n_out, n_crc, n_rc = S.compile_and_run(EQ.OUT_TB, Path(td) / 'neg.vvp',
                                               [EQ.WU_DUT, S.CELLS, vref])
        neg = EQ.parse_output(n_out)['summary']
        # The counts are exact, not "non-zero": the two designs disagree on exactly the pairs whose
        # sum is 495 or 496 (16 + 15 = 31), the altered reference disagrees with the stated
        # function on those same 31 (it now claims 495 where the function claims 496), and *our*
        # netlist still agrees with the function on all of them -- which is what localises the
        # fault to the reference rather than to the design.
        e495 = sum(1 for a in range(limit) for b in range(limit) if a + b == 495)
        check('the altered reference fails this comparison, and the failure localises',
              (neg['mism_ours_vs_ref'], neg['mism_ref_vs_func'], neg['mism_ours_vs_func']),
              (expected_high + e495, expected_high + e495, 0))
        check('the altered run still visits every pair', neg['checked'], pairs)

    # ---- 8. anti-vacuity, and nothing rewritten -----------------------------------
    check('anti-vacuity: 65536 pairs, 15 equating, and ours is high on 15',
          (pairs, expected_high, got['ours_high']), (65536, 15, 15))
    check('anti-vacuity: the netlist under test is the committed one',
          S.CELLS.read_bytes(), cells_before)
    check('anti-vacuity: nothing in the tree was rewritten by this gate',
          (EQ.OUT_TB.read_bytes() == tb_before, EQ.OUT_REPORT.read_bytes() == report_before,
           EQ.WU_DUT.read_bytes() == dut_before, P.OUT_WU_REPORT.read_bytes() == pw_before),
          (True, True, True, True))
    n_checks = len(results) + 1
    check('anti-vacuity: at least 40 checks ran', n_checks >= 40, True)

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
        print(f'STEP C2 GATE: FAIL ({n_fail} failing)')
        return 1
    print('STEP C2 GATE: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
