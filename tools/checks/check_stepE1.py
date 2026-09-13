#!/usr/bin/env python3
"""check_stepE1.py -- the E1 gate.

E1 is the step that says the recovery is complete enough to answer the puzzle: the netlist we
extracted from the layout, driven by the published winning vector on the reference's own control
timeline, asserts `success` at cycle 126 and prints `(* TWO STARS *)`.

The gate re-derives all of it from the netlist rather than from the step's prose:

* **the offset is measured, not assumed** -- drove the vector at offsets 4..8 and exactly one of them
  reproduces the acceptance cycle, which is what makes "cycle 126" a test instead of a convention;
* **the message is read off `O`** the same way the `iverilog` harnesses read it;
* **four of the five message classes are reproduced**, and the fifth is asserted to be the *measured*
  `TRY AGAIN` rather than allowed to pass as a match: `TWO NOT TOUCH` cannot be constructed without
  knowing the region map, so the design's answer to the vector that is adjacent-but-not-region-valid
  is `TRY AGAIN`. That is an open acceptance sub-item, and this gate fails if any *other* class stops
  matching -- the point is to keep the gap visible and specific.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import target as T
from tools.puzzle import verdict as V

ART_E1 = ROOT / 'recon' / 'derived' / 'e1_messages.json'
WINNING_OFFSET = 4
SUCCESS_MESSAGE = '(* TWO STARS *)'
# The four classes that must match exactly, and the one that is known to be blocked on the map.
REQUIRED = {'all_zeros': 'EMPTY SKY', 'all_ones': 'BIG BANG', 'other_wrong': 'TRY AGAIN',
            'correct': SUCCESS_MESSAGE}
BLOCKED = {'two_per_row_col_but_adjacent': 'TWO NOT TOUCH'}

checks: list[dict] = []


def check(name: str, passed: bool, detail: str = '') -> None:
    checks.append({'check': name, 'passed': bool(passed), 'detail': detail})


def main() -> int:
    if not ART_E1.exists():
        print(f'missing {ART_E1.relative_to(ROOT).as_posix()}')
        print('run: python -m tools.puzzle winning')
        return 1
    art = json.loads(ART_E1.read_text(encoding='utf-8'))
    m = V.Machine(cycles=V.MESSAGE_CYCLES)

    # ---- the instrument --------------------------------------------------------------
    rep = m.reference_replay()
    check('the evaluator reproduces example_inputs.vcd',
          rep['mismatches'] == 0 and rep['unknown_bits'] == 0,
          f"{rep['mismatches']} mismatches, {rep['unknown_bits']} unknown bits over "
          f"{rep['cycles']} cycles")

    # ---- AC2: success at cycle 126, at one measured offset only ----------------------
    winning = [int(b) for b in T.FEED_ORDER]
    check('the target vector has 121 bits and 22 ones',
          len(winning) == 121 and sum(winning) == 22,
          f'{len(winning)} bits, {sum(winning)} ones')
    rises, by_offset = [], {}
    for offset in (4, 5, 6, 7, 8):
        m.offset = offset
        r = m.message(winning)
        by_offset[offset] = r
        if r['success_cycle_0based'] is not None:
            rises.append((offset, r['success_cycle_0based'] + 1))
    check('exactly one feed offset reproduces the acceptance cycle',
          [o for o, _c in rises] == [WINNING_OFFSET], f'{rises}')
    check(f'success rises at cycle {T.SUCCESS_CYCLE} under that offset',
          rises and rises[0][1] == T.SUCCESS_CYCLE, f'{rises}')
    check('the message printed on the winning feed is the published one',
          by_offset[WINNING_OFFSET]['text'] == SUCCESS_MESSAGE,
          repr(by_offset[WINNING_OFFSET]['text']))
    check('the other offsets fail rather than succeed silently',
          all(by_offset[o]['text'] == 'TRY AGAIN' for o in (5, 6, 7, 8)),
          f"{sorted({by_offset[o]['text'] for o in (5, 6, 7, 8)})}")
    check('no unknown bits anywhere in the winning message window',
          by_offset[WINNING_OFFSET]['unknown_bits'] == 0,
          f"{by_offset[WINNING_OFFSET]['unknown_bits']} unknown")
    m.offset = WINNING_OFFSET

    # ---- the message classes ---------------------------------------------------------
    feeds = {'all_zeros': '0' * 121, 'all_ones': '1' * 121,
             'two_per_row_col_but_adjacent': V.adjacent_vector(),
             'other_wrong': V.other_wrong_vector(), 'correct': T.FEED_ORDER}
    got = {tag: m.message([int(b) for b in feed]) for tag, feed in feeds.items()}
    for tag, want in sorted(REQUIRED.items()):
        check(f'{tag} -> {want}', got[tag]['text'] == want, repr(got[tag]['text']))
    for tag, blocked in sorted(BLOCKED.items()):
        check(f'{tag} is the blocked class: the design says TRY AGAIN, not {blocked}',
              got[tag]['text'] == 'TRY AGAIN',
              f"{got[tag]['text']!r} -- constructing {blocked} needs the region map")
    check('no other class drifted', sum(1 for t, w in REQUIRED.items()
                                        if got[t]['text'] == w) == len(REQUIRED),
          f'{len(REQUIRED)} of {len(REQUIRED) + len(BLOCKED)} classes reproduce their message')
    check('success is low on every wrong input',
          all(got[t]['success_cycle_0based'] is None for t in REQUIRED if t != 'correct')
          and got['correct']['success_cycle_0based'] is not None,
          'only the winning vector asserts success')

    # ---- the artifact must agree with the gate ---------------------------------------
    art_offsets = {o['offset']: o for o in art['offsets']}
    check('the artifact s expected cycle is the contract s',
          art['success_cycle_expected'] == T.SUCCESS_CYCLE,
          f"{art['success_cycle_expected']} vs {T.SUCCESS_CYCLE}")
    check('the artifact records one entry per offset, with the ones fed re-measured',
          [o['offset'] for o in art['offsets']] == [4, 5, 6, 7, 8]
          and all(o['ones_in_window'] == 22 for o in art['offsets']),
          f"{[(o['offset'], o['ones_in_window']) for o in art['offsets']]}")
    check('the committed artifact records the same offsets and rising cycle',
          art_offsets.get(WINNING_OFFSET, {}).get('success_cycle_1based') == T.SUCCESS_CYCLE
          and [o['offset'] for o in art['offsets'] if o['success_cycle_0based'] is not None]
          == [WINNING_OFFSET],
          f"{[o['offset'] for o in art['offsets'] if o['success_cycle_0based'] is not None]}")
    art_msgs = {r['case']: r['got'] for r in art['messages']}
    check('the committed message table matches the re-derived one',
          art_msgs == {t: r['text'] for t, r in got.items()},
          'identical' if art_msgs == {t: r['text'] for t, r in got.items()} else 'drifted')
    check('the artifact states the open item rather than hiding it',
          'TWO NOT TOUCH' in art.get('open', '') and 'region map' in art.get('open', ''),
          art.get('open', '')[:70] + '...')

    # ---- anti-vacuity floors ---------------------------------------------------------
    check('floors: five offsets, five classes, real comparisons',
          len(by_offset) == 5 and len(got) == 5 and rep['output_comparisons'] > 1000,
          f'{len(by_offset)} offsets, {len(got)} classes, {rep["output_comparisons"]} comparisons')

    failed = [c for c in checks if not c['passed']]
    width = max(len(c['check']) for c in checks)
    for c in checks:
        print(f'  {"PASS" if c["passed"] else "FAIL"}  {c["check"]:<{width}}  {c["detail"]}')
    print()
    print(f'{len(checks) - len(failed)}/{len(checks)} checks passed')
    if failed:
        print('STEP E1 GATE: FAIL')
        return 1
    print('STEP E1 GATE: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
