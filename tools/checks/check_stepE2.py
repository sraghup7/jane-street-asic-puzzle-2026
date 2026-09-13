#!/usr/bin/env python3
"""check_stepE2.py -- the E2 gate: does the chip agree with C4's partition?

E2 was blocked for as long as there was no map: `TWO NOT TOUCH` cannot be constructed without one,
because the design prints it only when adjacency is the *only* thing wrong. With C4's candidate
partition that input exists, and this gate holds the result in place:

* the boards are **re-derived** (the two-switch family of the accepted board, filtered to those with
  an adjacent pair and every class within capacity), not read from the artifact;
* every board is checked against the four visible rules *before* it is fed, because a board that also
  broke two-per-row could not answer `TWO NOT TOUCH` and would prove nothing;
* the chip is asked live about a sample, and the artifact is compared against the re-derivation;
* the **contrast** is checked too: boards that are adjacent *and* break a class cap must not spell the
  message, or the message is not distinguishing adjacency from the hidden constraint;
* the one character that the design's undriven net leaves open is asserted as such, including the
  awkward part -- that neither tie reproduces the published string byte-for-byte.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import target as T
from tools.puzzle import confirm as C
from tools.puzzle import verdict as V

ART = ROOT / 'recon' / 'derived' / 'e2_messages.json'
TARGET = 'TWO NOT TOUCH'
LIVE_SAMPLE = 8          # boards asked live (the artifact holds all 23)
MIN_USABLE = 5
MIN_FAMILY = 50

checks: list[dict] = []


def check(name: str, passed: bool, detail: str = '') -> None:
    checks.append({'check': name, 'passed': bool(passed), 'detail': detail})


def main() -> int:
    if not ART.exists():
        print(f'missing {ART.relative_to(ROOT).as_posix()}')
        print('run: python -m tools.puzzle confirm')
        return 1
    art = json.loads(ART.read_text(encoding='utf-8'))

    m = V.Machine(cycles=V.MESSAGE_CYCLES)

    # ---- the instrument --------------------------------------------------------------
    rep = m.reference_replay()
    check('the evaluator reproduces example_inputs.vcd',
          rep['mismatches'] == 0 and rep['unknown_bits'] == 0,
          f"{rep['mismatches']} mismatches, {rep['unknown_bits']} unknown bits")

    # ---- the boards, re-derived ------------------------------------------------------
    class_of, src = V.region_partition()
    n_classes = len(src['flops'])
    fam = C.swap_family(class_of, n_classes)
    check(f'the two-switch family is re-derived and is not tiny',
          fam['candidates'] >= MIN_FAMILY, f'{fam["candidates"]} distinct boards')
    check(f'at least {MIN_USABLE} boards are adjacent with every class within capacity',
          len(fam['usable']) >= MIN_USABLE,
          f"{len(fam['usable'])} usable, {len(fam['violating'])} over capacity")
    check('the artifact records the same family',
          art['family']['candidates'] == fam['candidates']
          and art['family']['usable'] == len(fam['usable'])
          and art['family']['violating'] == len(fam['violating']),
          f"{art['family']} vs re-derived {fam['candidates']}/"
          f"{len(fam['usable'])}/{len(fam['violating'])}")
    check('every usable board satisfies 22 / two per row / two per column, adjacency aside',
          all(C._visible_ok(e['grid'])['ok'] for e in fam['usable']),
          'so only the hidden constraint and adjacency can be at issue')
    check('every usable board really does contain an adjacent pair',
          all(e['adjacency'] for e in fam['usable']), f"{len(fam['usable'])} boards")
    check('every usable board is inside the class caps',
          all(not e['classes_over_capacity'] for e in fam['usable']),
          'the partition is satisfied by construction, then checked')
    # Without this, a board mutated inside the artifact simply falls out of the intersection below
    # and the gate would pass on the rest -- the artifact must record boards the family actually has.
    fam_grids = {tuple(e['grid']) for e in fam['usable']}
    check('every board the artifact records is a member of the re-derived family',
          all(tuple(r['grid']) in fam_grids for r in art['results'])
          and len({tuple(r['grid']) for r in art['results']}) == len(art['results']),
          f"{sum(1 for r in art['results'] if tuple(r['grid']) in fam_grids)} of "
          f"{len(art['results'])} recognised")

    # ---- the chip, asked live about a sample -----------------------------------------
    tested = [e for e in fam['usable'] if e['grid'] in [r['grid'] for r in art['results']]]
    sample = tested[:LIVE_SAMPLE] if tested else fam['usable'][:LIVE_SAMPLE]
    live = []
    for e in sample:
        reading = C.ask(m, e['grid'])
        live.append((reading, C._classify(reading, TARGET)))
    check(f'the chip spells {TARGET!r} on every sampled board, live',
          all(v['spells_it'] for _r, v in live),
          f"{sum(1 for _r, v in live if v['spells_it'])} of {len(live)}"
          + (f"; readings {[r['text'] for r, _v in live if not _v['spells_it']]}"
             if any(not v['spells_it'] for _r, v in live) else ''))
    check('the artifact records the same readings for those boards',
          all(any(r['grid'] == e['grid'] and r['reading']['text'] == rd['text']
                  for r in art['results'])
              for e, (rd, _v) in zip(sample, live)),
          'artifact and re-derivation agree')
    check('the artifact says every tested board spells the message',
          art['boards_spelling_the_message'] == len(art['results'])
          and art['boards_tested'] == len(art['results']),
          f"{art['boards_spelling_the_message']} of {art['boards_tested']}")

    # ---- the contrast: adjacency is not enough ---------------------------------------
    viol_sample = fam['violating'][:4]
    viol_live = [(C.ask(m, e['grid']), e) for e in viol_sample]
    check('boards that are adjacent AND break a class cap do not spell the message',
          all(not C._classify(r, TARGET)['spells_it'] for r, _e in viol_live),
          f"readings {sorted({r['text'] for r, _e in viol_live})}")
    check('the artifact records that contrast',
          art['control_group']['boards'] > 0
          and art['control_group']['boards_not_spelling_it'] == art['control_group']['boards'],
          f"{art['control_group']['boards_not_spelling_it']} of "
          f"{art['control_group']['boards']}")
    check('E1 s partition-violating adjacent vector is the third reference point',
          'TRY AGAIN' in art['contrast']['adjacent_but_partition_violated']['message']
          and art['contrast']['adjacent_but_partition_violated']['classes_over_capacity'],
          f"{art['contrast']['adjacent_but_partition_violated']['message']!r} with classes over "
          f"capacity {art['contrast']['adjacent_but_partition_violated']['classes_over_capacity']}")

    # ---- the four message classes ----------------------------------------------------
    classes = art['message_classes']
    check('the other message classes still reproduce',
          classes['all_zeros']['text'] == 'EMPTY SKY' and classes['all_ones']['text'] == 'BIG BANG'
          and classes['other_wrong']['text'] == 'TRY AGAIN'
          and classes['correct']['text'] == '(* TWO STARS *)',
          f"{ {k: v['text'] for k, v in classes.items()} }")
    check('success stays low on every wrong input, including the TWO NOT TOUCH boards',
          all(classes[k]['success_cycle_0based'] is None
              for k in ('all_zeros', 'all_ones', 'other_wrong'))
          and classes['correct']['success_cycle_0based'] is not None
          and all(r['reading']['success_cycle_0based'] is None for r in art['results']),
          'no board in this step asserts success')
    check('the TWO NOT TOUCH class is recorded with how many characters were exact',
          classes['two_per_row_col_but_adjacent']['readings_spelling_it'] == art['boards_tested']
          and classes['two_per_row_col_but_adjacent']['byte_exact'] == 0
          and classes['two_per_row_col_but_adjacent']['with_unknown_bits'] > 0,
          f"{classes['two_per_row_col_but_adjacent']}")

    # ---- the undriven net, asserted including the awkward part ------------------------
    finding = art['undriven_net_in_the_message']
    live_reading = C.ask(m, sample[0]['grid']) if sample else None
    check('the unknown characters come from B5 s one undriven net',
          finding['net'] == 806 and finding['terminals'] == ['a311o_2.A1', 'a31oi_2.A1']
          and live_reading is not None and live_reading['forced'] is not None
          and sorted(live_reading['forced']['positions_varying'])
          == sorted(finding.get('positions', [3, 12])),
          f"net {finding['net']} varies positions "
          f"{sorted(live_reading['forced']['positions_varying']) if live_reading and live_reading['forced'] else '?'}")
    tie0, tie1 = (live_reading['forced']['0'], live_reading['forced']['1']) \
        if live_reading and live_reading['forced'] else ('', '')
    check('neither tie of that net reproduces the published string byte-for-byte',
          tie0 != TARGET and tie1 != TARGET and tie0 != tie1,
          f'806=0 gives {tie0!r}, 806=1 gives {tie1!r} -- recorded as a finding, not smoothed over')

    # ---- anti-vacuity floors ---------------------------------------------------------
    check('floors: family, sample, control group and target length',
          fam['candidates'] >= MIN_FAMILY and len(live) >= 1 and len(viol_live) >= 1
          and len(TARGET) == 13 and T.FEED_ORDER.count('1') == 22,
          f"{len(live)} sampled, {len(viol_live)} control, target {len(TARGET)} characters")

    failed = [c for c in checks if not c['passed']]
    width = max(len(c['check']) for c in checks)
    for c in checks:
        print(f'  {"PASS" if c["passed"] else "FAIL"}  {c["check"]:<{width}}  {c["detail"]}')
    print()
    print(f'{len(checks) - len(failed)}/{len(checks)} checks passed')
    if failed:
        print('STEP E2 GATE: FAIL')
        return 1
    print('STEP E2 GATE: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
