#!/usr/bin/env python3
"""check_stepE3.py -- the E3 gate: the acceptance matrix, and its refusal to overclaim.

E3 is the step where the project states, in one table, whether it matched the known answer on every
agreed criterion. A matrix like that is exactly the kind of artifact that can quietly become a
victory lap, so this gate does two jobs:

* **re-derives every criterion's comparison** from the committed artifacts and the contract in
  `tools/target.py` -- the matrix is not allowed to be an opinion about the artifacts;
* **polices the honesty rules**, with checks that fail if the matrix is worded rather than computed:
  AC6's letters and status must come from `accept.letter_classes()`/`accept.ac6_ok()` re-applied to the
  same artifacts, its notes must carry the method disclosure (single-star probing, prohibited method
  5), AC4 must carry the note explaining the one contract row that E1 alone does not satisfy, the
  report must list what is not claimed, and a criterion marked `PASS` must carry evidence with no
  unmet items.

It follows that the state this gate guards is "6 PASS, 0 PARTIAL, 0 FAIL" -- if AC6's status stops
matching what `ac6_ok()` computes from its own evidence, this gate fails rather than trusting the row.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import target as T
from tools.puzzle import accept as A

ART = ROOT / 'recon' / 'derived' / 'acceptance.json'
D = ROOT / 'recon' / 'derived'
MIN_COMPARED_BITS = 2000          # anti-vacuity floor for the replay comparison
MIN_E2_BOARDS = 10

checks: list[dict] = []


def check(name: str, passed: bool, detail: str = '') -> None:
    checks.append({'check': name, 'passed': bool(passed), 'detail': detail})


def load(name: str) -> dict:
    return json.loads((D / name).read_text(encoding='utf-8'))


def main() -> int:
    if not ART.exists():
        print(f'missing {ART.relative_to(ROOT).as_posix()}')
        print('run: python -m tools.puzzle acceptance')
        return 1
    art = json.loads(ART.read_text(encoding='utf-8'))
    rows = {r['id']: r for r in art['criteria']}

    # ---- the yardstick, re-verified here too -----------------------------------------
    c = T.check_constraints(T.GRID)
    check('the yardstick is internally consistent',
          T.grid_from_bits(T.FEED_ORDER) == T.GRID
          and T.grid_from_bits(T.WITNESS_AS_PRINTED) == T.rot180(T.GRID)
          and T.rot180(T.GRID) != T.GRID
          and c['total_ok'] and c['per_row_ok'] and c['per_col_ok'] and c['no_adjacency'],
          'grid <-> bit vector in both orders, and the order distinction is non-vacuous')

    # ---- every criterion's comparison, re-derived ------------------------------------
    sol = load('solutions.json')
    e1 = load('e1_messages.json')
    e2 = load('e2_messages.json')
    rep = load('vcd_replay.json')
    c4 = load('c4_partition.json')

    s = sol['solution']
    check('AC1: the vector in the artifact is ours and equals the contract',
          s['feed_order'] == T.FEED_ORDER and s['as_printed'] == T.WITNESS_AS_PRINTED
          and s['grid'] == T.GRID and sol['solutions_found'] == 1 and sol['unique']
          and sol['enumerators']['agree_on_solution'] and sol['enumerators']['agree_on_count'],
          f"{sol['solutions_found']} solution, both enumerators agree, both bit orders equal")
    check('AC1: the matrix recorded that comparison, not a friendlier one',
          rows['AC1']['status'] == 'PASS'
          and rows['AC1']['evidence']['feed_order_equals_target'] is True
          and rows['AC1']['evidence']['as_printed_equals_target'] is True
          and rows['AC1']['evidence']['board_equals_target_grid'] is True,
          f"{rows['AC1']['status']}")
    check('AC1: the search actually searched',
          sol['enumerators']['rows_dfs']['nodes'] > 1000
          and sol['enumerators']['bitmask_stack']['nodes'] > 1000,
          f"{sol['enumerators']['rows_dfs']['nodes']} + "
          f"{sol['enumerators']['bitmask_stack']['nodes']} nodes")

    rising = [o for o in e1['offsets'] if o['success_cycle_1based'] is not None]
    check('AC2: success is asserted at the contract cycle and at exactly one offset',
          len(rising) == 1 and rising[0]['success_cycle_1based'] == T.SUCCESS_CYCLE
          and rows['AC2']['status'] == 'PASS',
          f"offsets asserting success: {[o['offset'] for o in rising]}, "
          f"cycle {[o['success_cycle_1based'] for o in rising]}")

    correct = next(m for m in e1['messages'] if m['case'] == 'correct')
    check('AC3: the printed message is the contract string',
          correct['got'] == T.MESSAGE_TABLE['correct'] and correct['unknown_bits'] == 0
          and rows['AC3']['status'] == 'PASS',
          f"{correct['got']!r}")

    rowmap = {m['case']: m for m in e1['messages']}
    matched = [k for k, m in rowmap.items() if m['got'] == T.MESSAGE_TABLE[k]]
    check('AC4: all four wrong-input classes are present in the record',
          len(matched) >= 4 and e2['message_classes']['all_zeros']['text'] == 'EMPTY SKY'
          and e2['message_classes']['all_ones']['text'] == 'BIG BANG'
          and e2['message_classes']['other_wrong']['text'] == 'TRY AGAIN',
          f"{len(matched)} of {len(rowmap)} contract rows matched directly, "
          f"TWO NOT TOUCH via E2")
    check('AC4: TWO NOT TOUCH is reproduced by the E2 boards, and not by the controls',
          e2['boards_spelling_the_message'] == e2['boards_tested'] >= MIN_E2_BOARDS
          and e2['control_group']['boards_not_spelling_it'] == e2['control_group']['boards'] > 0,
          f"{e2['boards_spelling_the_message']}/{e2['boards_tested']} boards, "
          f"{e2['control_group']['boards_not_spelling_it']}/{e2['control_group']['boards']} controls")
    check('AC4: the matrix carries the note about the one row E1 alone cannot satisfy',
          any('predates the map' in n for n in rows['AC4']['notes']),
          'the e1 `two_per_row_col_but_adjacent` mismatch is explained, not hidden')

    comp = rep['comparison']
    check('AC5: the replay is exact and non-vacuous',
          comp['mismatches'] == [] and comp['exact'] and comp['output_bits_mismatched'] == 0
          and comp['output_bits_compared'] >= MIN_COMPARED_BITS
          and comp['stimulus_bits_mismatched'] == 0 and comp['our_outputs_with_x_or_z'] == 0
          and rep['cross_check']['mismatches'] == 0
          and all(v['matches_reference'] for v in rep['forced_runs'].values())
          and rows['AC5']['status'] == 'PASS',
          f"{comp['output_bits_compared']} output bits compared, {comp['cycles']} cycles, "
          f"cross-check {rep['cross_check']['compared']}")

    ragged = [x for x in c4['candidates'] if not x['is_the_visible_column_rule']]
    check('AC6: the partition is real, capacity-2, and pins the answer',
          len(ragged) == 1 and len(ragged[0]['flops']) == 11
          and sum(len(ragged[0]['classes'][f]) for f in ragged[0]['flops']) == 121
          and rows['AC6']['evidence']['solutions_under_this_partition'] == 1,
          f"{ragged[0]['class_sizes']}, one solution under it")

    sel = next(x for x in c4['candidates'] if x['name'] == c4['selected'])
    classes = {f: sel['classes'][f] for f in sel['flops']}
    letters = A.letter_classes(classes)
    check('AC6: the letters are computed from the partition, not asserted',
          rows['AC6']['evidence']['letters_found'] == sorted(letters)
          and set(letters) == {'J', 'S'}, f'{letters}')
    check('AC6 status follows its evidence',
          rows['AC6']['status'] == ('PASS' if A.ac6_ok(rows['AC6']['evidence']) else 'PARTIAL'),
          rows['AC6']['status'])
    check('AC6 carries the method disclosure (single-star probing, prohibited method 5)',
          any('single-star' in n and 'prohibited' in n for n in rows['AC6']['notes']), 'disclosed')

    # ---- every number the matrix displays must be the artifact's number ---------------
    # Found by fault injection: zeroing AC5's quoted bit count escaped, because the check above
    # re-derives from vcd_replay.json and never compared the matrix's own copy. A matrix that can
    # quote different numbers from its sources is worse than no matrix.
    check('the matrix quotes the replay artifact faithfully',
          rows['AC5']['evidence']['cycles'] == comp['cycles']
          and rows['AC5']['evidence']['output_bits_compared'] == comp['output_bits_compared']
          and rows['AC5']['evidence']['output_bits_mismatched'] == comp['output_bits_mismatched']
          and rows['AC5']['evidence']['exact'] == comp['exact']
          and rows['AC5']['evidence']['cross_check_mismatches'] == rep['cross_check']['mismatches'],
          f"cycles {rows['AC5']['evidence']['cycles']}, bits "
          f"{rows['AC5']['evidence']['output_bits_compared']}")
    check('the matrix quotes the solution artifact faithfully',
          rows['AC1']['evidence']['solutions_found'] == sol['solutions_found']
          and rows['AC1']['evidence']['unique'] == sol['unique']
          and rows['AC1']['evidence']['enumerators_agree_on_count']
          == sol['enumerators']['agree_on_count']
          and rows['AC1']['evidence']['ones'] == sol['solution']['ones']
          and rows['AC1']['evidence']['region_constraint_from'] == sol['region_partition']['source'],
          f"{rows['AC1']['evidence']['solutions_found']} solution, "
          f"{rows['AC1']['evidence']['ones']} stars")
    check('the matrix quotes the message artifacts faithfully',
          rows['AC2']['evidence']['success_cycle_1based']
          == [o['success_cycle_1based'] for o in rising]
          and rows['AC3']['evidence']['message_on_the_winning_offset'] == correct['got']
          and rows['AC4']['evidence']['two_not_touch_boards_spelling_it']
          == e2['boards_spelling_the_message']
          and rows['AC4']['evidence']['control_boards_not_spelling_it']
          == e2['control_group']['boards_not_spelling_it'],
          "the 126, the message, and the E2 board counts are the artifacts' own")
    check('the matrix quotes the partition artifact faithfully',
          rows['AC6']['evidence']['solutions_under_this_partition']
          == sel['unique_solution']['solutions']
          and rows['AC6']['evidence']['letters_found'] == sorted(letters)
          and rows['AC6']['evidence']['chip_matches_prediction']
          == e2['agreement']['chip_matches_prediction']
          and rows['AC6']['evidence']['swap_boards'] == e2['agreement']['boards']
          and rows['AC6']['evidence']['lookalikes_reproducing_the_chip']
          == e2['lookalike_power']['reproduce_all_boards'],
          f"{sel['name']}, letters {sorted(letters)}, "
          f"{rows['AC6']['evidence']['chip_matches_prediction']}/{rows['AC6']['evidence']['swap_boards']} "
          f"boards agree")

    # ---- the honesty rules -----------------------------------------------------------
    check('the report lists what is not claimed, and it is not empty',
          len(art['claims_not_made']) >= 3 and art['partial'] == [],
          f"partial={art['partial']}, {len(art['claims_not_made'])} unclaimed items")
    check('a criterion marked PASS carries evidence and no unmet items',
          all(r['evidence'] and not r['unmet'] for r in art['criteria'] if r['status'] == 'PASS'),
          'PASS means "asserted, with numbers"')
    check('the matrix re-verifies the yardstick it used',
          art['yardstick'].startswith('tools/target.py'),
          art['yardstick'])
    check('the counts match the statuses, and nothing failed',
          art['counts']['FAIL'] == 0
          and art['counts']['PASS'] == sum(1 for r in art['criteria'] if r['status'] == 'PASS')
          and art['counts']['PARTIAL'] == sum(1 for r in art['criteria'] if r['status'] == 'PARTIAL')
          and art['overall'] == 'PASS',
          f"{art['counts']}, overall {art['overall']}")

    failed = [c for c in checks if not c['passed']]
    width = max(len(c['check']) for c in checks)
    for c in checks:
        print(f'  {"PASS" if c["passed"] else "FAIL"}  {c["check"]:<{width}}  {c["detail"]}')
    print()
    print(f'{len(checks) - len(failed)}/{len(checks)} checks passed')
    if failed:
        print('STEP E3 GATE: FAIL')
        return 1
    print('STEP E3 GATE: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
