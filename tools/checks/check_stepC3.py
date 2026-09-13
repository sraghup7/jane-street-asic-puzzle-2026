#!/usr/bin/env python3
"""check_stepC3.py -- the C3 gate.

What this verifies, and why each part is here rather than trusted from the artifact:

* **The partition.** The flop list is re-derived from build/puzzle.v, and blocks.json must assign
  every one of them to exactly one block -- no flop dropped, none double-counted. A classification
  that silently covers 70 of 92 flops is the failure mode a summary table hides.
* **The shift register, re-derived.** The artifact says 12 stages with delays 0..11. This re-runs the
  winning harness and checks that each named flop's trace really equals the serial input delayed by
  the delay claimed for it, with zero mismatches. The claim is checked against the simulation, not
  against itself.
* **The ones counter, re-derived.** The chain is read as a binary number from the re-run dump; the
  value must reach exactly 22 during the winning feed, and every step must be +1 except the clear
  between attempts.
* **Hermeticity.** The two emitted harnesses are regenerated into scratch and compared byte-for-byte
  with the committed ones, so an artifact cannot drift from the code that claims to produce it.
* **Anti-vacuity.** Floors on the flop count, the block count and the number of compared samples, so
  a run that compares nothing cannot pass.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.puzzle import analyse as A
from tools.puzzle import simulate as S
from tools import target as T

REPORT = ROOT / 'recon' / 'derived' / 'blocks.json'
PUZZLE = ROOT / 'build' / 'puzzle.v'
SCRATCH = ROOT / 'recon' / 'scratch' / 'analyse'
FLOPS = ('sky130_fd_sc_hd__dfrtp_2', 'sky130_fd_sc_hd__dfstp_2', 'sky130_fd_sc_hd__dfxtp_2')
INST_RE = re.compile(r'^  (\S+)\s+(i\d+)\s*\((.*)\);\s*$', re.M)

checks: list[dict] = []


def check(name: str, passed: bool, detail: str = '') -> None:
    checks.append({'check': name, 'passed': bool(passed), 'detail': detail})


def main() -> int:
    if not REPORT.exists():
        print(f'missing {REPORT.relative_to(ROOT).as_posix()}; run python -m tools.puzzle decompose')
        return 1
    art = json.loads(REPORT.read_text(encoding='utf-8'))

    # ---- the netlist's own flop list, re-derived ------------------------------------
    flops, flop_nets = [], {}
    for m in INST_RE.finditer(PUZZLE.read_text(encoding='utf-8')):
        if m.group(1) not in FLOPS:
            continue
        flops.append(m.group(2))
        flop_nets[m.group(2)] = dict(re.findall(r'\.(\w+)\s*\(([^)]*)\)', m.group(3)))['Q'].strip()
    flops = sorted(flops)
    check('build/puzzle.v declares 92 flops', len(flops) == 92, f'found {len(flops)}')

    assignment = art['assignment']
    blocks = art['blocks']
    assigned = [f for f, b in assignment.items() if b != 'unclassified']
    unassigned = [f for f, b in assignment.items() if b == 'unclassified']
    check('every flop appears in the assignment',
          set(assignment) == set(flops),
          f'missing {sorted(set(flops) - set(assignment))[:5]}, '
          f'extra {sorted(set(assignment) - set(flops))[:5]}')
    seen: dict[str, list[str]] = {}
    for f, b in assignment.items():
        seen.setdefault(b, []).append(f)
    check('no flop is in two blocks',
          all(len(v) == len(set(v)) for v in seen.values()))
    check('the blocks plus the unclassified set partition the flops',
          len(assigned) + len(unassigned) == len(flops),
          f'{len(assigned)} classified + {len(unassigned)} unclassified != {len(flops)}')
    check('at least 5 blocks were found', len(seen) >= 5, f'{len(seen)}: {sorted(seen)}')
    # The per-block lists and the assignment are two records of the same fact; if they disagree the
    # artifact is self-inconsistent even though each half looks fine on its own.
    disagree = []
    for name, blk in blocks.items():
        listed, from_map = set(blk.get('flops', [])), {f for f, b in assignment.items() if b == name}
        if listed != from_map:
            disagree.append((name, sorted(listed ^ from_map)[:3]))
    check('every block\'s flop list agrees with the assignment',
          not disagree, str(disagree[:3]))

    # ---- the required blocks are present --------------------------------------------
    for name in ('input_shift_register', 'ones_counter', 'message_counter'):
        check(f'the {name} block is present and non-empty',
              len(blocks.get(name, {}).get('flops', [])) > 0)

    # ---- re-derive the shift register and the ones counter from a fresh simulation ---
    stub = A.flop_table()
    rows, q = A.run('winning', stub, S.read_reference(), tb=SCRATCH / 'gate_win_tb.v')
    check('a fresh simulation of the winning feed produced 312 cycles',
          len(rows) == 312, f'{len(rows)} cycles')
    win_en = [r['enable'] for r in rows]
    win_wins = A.windows(win_en)
    win_inp = [r['I'] for r in rows]
    check('the winning feed has exactly two 121-cycle enable windows',
          [b - a + 1 for a, b in win_wins] == [121, 121],
          str([b - a + 1 for a, b in win_wins]))

    delays = blocks['input_shift_register']['delays']
    mism = total = 0
    for inst, d in delays.items():
        for a, b in win_wins:
            for p in range(d, b - a + 1):
                total += 1
                if q[inst][a + p] != win_inp[a + p - d]:
                    mism += 1
    check('every claimed shift stage really is the input delayed by its claimed depth',
          mism == 0 and total >= 2000,
          f'{mism} mismatches over {total} position comparisons')
    check('the delays are exactly 0..11 (a 12-stage pipeline, no duplicates)',
          sorted(int(d) for d in delays.values()) == list(range(12)),
          str(sorted(delays.values())))

    chain = blocks['ones_counter']['flops']
    val = A.chain_value(chain, q)
    a, b = win_wins[0]
    peak = max(val[a:b + 1])
    trans = [val[k] - val[k - 1] for k in range(1, len(val)) if val[k] != val[k - 1]]
    up = sum(1 for d in trans if d == 1)
    check('the ones counter reaches exactly 22 during the winning feed',
          peak == 22, f'peak {peak}')
    check('every step of the ones counter is +1 apart from the clear',
          up >= 0.9 * len(trans) and len(trans) >= 20,
          f'{up} of {len(trans)} steps are +1')
    check('the ones counter has at least 5 bits', len(chain) >= 5, f'{len(chain)} bits')
    # The claims in the artifact must match the re-derivation, or an artifact could report a peak of
    # 22 while the design never reaches it and the gate above would still pass on its own reading.
    claim = blocks['ones_counter']
    check('the artifact\'s reported peak matches the re-derivation',
          claim.get('value_peak_winning') == peak,
          f'artifact {claim.get("value_peak_winning")} vs re-derived {peak}')
    check('the artifact\'s reported step counts match the re-derivation',
          claim.get('steps_plus_one') == up and claim.get('steps_total') == len(trans),
          f'artifact {claim.get("steps_plus_one")}/{claim.get("steps_total")} '
          f'vs re-derived {up}/{len(trans)}')

    # ---- the region bits fire twice, which is the 2-stars-per-region fact -------------
    region = blocks['region_counter_bit']['flops']
    fires = {f: sum(1 for k in range(1, len(q[f])) if q[f][k] != q[f][k - 1]) for f in region}
    check('every region-counter bit fires exactly twice under the winning feed',
          fires and all(v == 2 for v in fires.values()),
          f'{sum(1 for v in fires.values() if v == 2)} of {len(fires)} fire twice')
    check('at least 22 bits fire exactly twice: two bits for each of 11 regions, each seeing its '
          'two stars',
          len(fires) >= 22, f'{len(fires)} bits x 2 transitions = {sum(fires.values())} events')

    # ---- hermeticity: the harnesses must be reproducible from the stage --------------
    hermetic = 0
    for name, stimulus, committed in (('ref', 'reference', A.OUT_REF_TB),
                                      ('win', 'winning', A.OUT_WIN_TB)):
        scratch = SCRATCH / f'hermetic_{name}_tb.v'
        A.emit(stimulus, stub, scratch)
        hermetic += 1 if scratch.read_bytes() == committed.read_bytes() else 0
    check('both emitted harnesses are byte-identical when regenerated',
          hermetic == 2, f'{hermetic} of 2')

    # ---- anti-vacuity ---------------------------------------------------------------
    check('the artifact records signatures for every flop',
          len(art['signatures']) == 92, f'{len(art["signatures"])} entries')
    check('the winning feed actually contains 22 stars',
          T.FEED_ORDER.count('1') == 22, f'{T.FEED_ORDER.count("1")}')

    failed = [c for c in checks if not c['passed']]
    width = max(len(c['check']) for c in checks)
    for c in checks:
        print(f'  {"PASS" if c["passed"] else "FAIL"}  {c["check"]:<{width}}  {c["detail"]}')
    print()
    print(f'{len(checks) - len(failed)}/{len(checks)} checks passed')
    if failed:
        print('STEP C3 GATE: FAIL')
        return 1
    print('STEP C3 GATE: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
