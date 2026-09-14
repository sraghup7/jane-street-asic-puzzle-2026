#!/usr/bin/env python3
"""accept.py -- E3: the acceptance matrix. One table, AC1..AC6, from our own artifacts.

This is the step that answers the protocol's Step 5 question -- *does the recovered truth match the
known answer on every acceptance criterion agreed in the plan* -- in a form that can be read at a
glance and, if anything is wrong, argues with you.

Three design rules:

1. **The yardstick is re-verified first.** Every criterion is compared against `tools/target.py`, and
   that file is itself checked here (grid consistent with the bit vector, four mechanical constraints
   holding, the feed-order/as-printed distinction non-vacuous) so a broken yardstick cannot flatter us.
2. **Everything is read from a committed artifact**, never re-computed from the netlist: this is the
   matrix over the project's results, and the per-step gates are what guard those results. Where a
   criterion is not met, it says so rather than being re-worded until it passes.
3. **Three states, not two.** `PASS`, `FAIL`, and `PARTIAL` -- because AC6 is neither: the partition is
   recovered and the chip corroborates it, and "the map, proven" is still not claimable, nor is the
   printed map's "JS" reading reproduced. A two-state matrix would have to lie about one of those.

    python -m tools.puzzle acceptance      # E3 -> recon/derived/acceptance.json
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import target as T

D = ROOT / 'recon' / 'derived'
OUT = D / 'acceptance.json'

PASS, PARTIAL, FAIL = 'PASS', 'PARTIAL', 'FAIL'


def load(name: str) -> dict:
    return json.loads((D / name).read_text(encoding='utf-8'))


# ---------------------------------------------------------------------------------------
# AC1 - the exact 121-bit vector, derived by us
# ---------------------------------------------------------------------------------------
def ac1() -> dict:
    sol = load('solutions.json')
    s = sol['solution']
    ev = {
        'derived_by': sol['generated_by'],
        'solutions_found': sol['solutions_found'],
        'unique': sol['unique'],
        'enumerators_agree_on_solution': sol['enumerators']['agree_on_solution'],
        'enumerators_agree_on_count': sol['enumerators']['agree_on_count'],
        'feed_order_equals_target': s['feed_order'] == T.FEED_ORDER,
        'as_printed_equals_target': s['as_printed'] == T.WITNESS_AS_PRINTED,
        'board_equals_target_grid': s['grid'] == T.GRID,
        'ones': s['ones'],
        'five_constraints_checked': sol['validation']['all_five_ok'],
        'region_constraint_from': sol['region_partition']['source'],
    }
    ok = (ev['solutions_found'] == 1 and ev['unique'] and ev['feed_order_equals_target']
          and ev['as_printed_equals_target'] and ev['board_equals_target_grid']
          and ev['enumerators_agree_on_solution'] and ev['enumerators_agree_on_count']
          and ev['five_constraints_checked'] and ev['ones'] == T.EXPECTED['ones_total'])
    notes = [f"derived by our own search over five constraints, not transcribed from the answer: "
             f"{ev['derived_by']}",
             "both bit orders asserted, so the Step-2 feed-order trap cannot produce a false negative"]
    return {'id': 'AC1', 'criterion': 'the exact 121-bit input vector asserting success',
            'status': PASS if ok else FAIL, 'evidence': ev, 'notes': notes, 'unmet': []}


# ---------------------------------------------------------------------------------------
# AC2 / AC3 - success at cycle 126, and the message
# ---------------------------------------------------------------------------------------
def ac2_ac3() -> list[dict]:
    e1 = load('e1_messages.json')
    offs = {o['offset']: o for o in e1['offsets']}
    rising = [o for o in e1['offsets'] if o['success_cycle_1based'] is not None]
    correct = next(m for m in e1['messages'] if m['case'] == 'correct')
    ev = {
        'expected_cycle': T.SUCCESS_CYCLE,
        'offsets_tested': sorted(offs),
        'offsets_that_assert_success': [o['offset'] for o in rising],
        'success_cycle_1based': [o['success_cycle_1based'] for o in rising],
        'message_on_the_winning_offset': correct['got'],
        'expected_message': T.MESSAGE_TABLE['correct'],
        'answer_string': T.ANSWER_STRING,
        'unknown_bits': correct['unknown_bits'],
    }
    ac2_ok = (len(rising) == 1 and rising[0]['offset'] == 4
              and rising[0]['success_cycle_1based'] == T.SUCCESS_CYCLE)
    ac3_ok = correct['got'] == T.MESSAGE_TABLE['correct'] and correct['unknown_bits'] == 0
    return [
        {'id': 'AC2', 'criterion': f'success asserts at cycle {T.SUCCESS_CYCLE}, and only there',
         'status': PASS if ac2_ok else FAIL, 'evidence': ev,
         'notes': ['every other feed offset shifts the grid and correctly answers TRY AGAIN'],
         'unmet': []},
        {'id': 'AC3', 'criterion': f'output string {T.MESSAGE_TABLE["correct"]!r}',
         'status': PASS if ac3_ok else FAIL, 'evidence': ev, 'notes': [], 'unmet': []},
    ]


# ---------------------------------------------------------------------------------------
# AC4 - the four wrong-input messages
# ---------------------------------------------------------------------------------------
def ac4() -> dict:
    """AC4: the four wrong-input messages.

    PASS requires the contract's rows to be matched directly, with one stated exception: the
    `two_per_row_col_but_adjacent` row is a *class* of input, and the single vector E1 built for it
    also violated the hidden constraint, so it answers `TRY AGAIN`. The class is reproduced by E2's
    constructed boards -- and refused by E2's controls -- which is what this row checks.
    """
    e1 = load('e1_messages.json')
    e2 = load('e2_messages.json')
    rows = {m['case']: m for m in e1['messages']}
    expected = T.MESSAGE_TABLE
    matched = [k for k, m in rows.items() if m['got'] == expected[k]]
    mismatch = {k: {'expected': expected[k], 'got': m['got']} for k, m in rows.items()
                if m['got'] != expected[k]}
    two_not_touch = (e2['message_classes']['two_per_row_col_but_adjacent']['readings_spelling_it']
                     == e2['boards_tested'])
    ev = {
        'classes_matching_the_contract': matched,
        'classes_mismatching': mismatch,
        'two_not_touch_boards_spelling_it': e2['boards_spelling_the_message'],
        'two_not_touch_boards_tested': e2['boards_tested'],
        'control_boards_not_spelling_it': e2['control_group']['boards_not_spelling_it'],
        'control_boards': e2['control_group']['boards'],
        'characters_exact_in_that_message': e2['message_classes']
                                              ['two_per_row_col_but_adjacent']['byte_exact'],
    }
    # The contract's `two_per_row_col_but_adjacent` row is a *class* of input: adjacent with
    # everything else valid. E1's single hand-built vector for it also broke the hidden constraint
    # (built before the map existed), so it answers TRY AGAIN; E2 reproduces the class properly.
    ok = two_not_touch and not [k for k in mismatch if k != 'two_per_row_col_but_adjacent'] \
        and len(matched) >= 4
    notes = [
        f"`TWO NOT TOUCH` is reproduced on {e2['boards_spelling_the_message']} constructed boards "
        f"(E2) and NOT on the {e2['control_group']['boards_not_spelling_it']} controls that also "
        f"break a class cap",
        "E1's single vector for that class predates the map and also violates the hidden constraint, "
        "so it answers TRY AGAIN; that mismatch is expected and is why the class needed E2",
        "the printed message carries one indeterminate character, following the design's undriven net "
        "(806) -- recorded, not resolved",
    ]
    return {'id': 'AC4', 'criterion': 'the four wrong-input messages',
            'status': PASS if ok else FAIL, 'evidence': ev, 'notes': notes, 'unmet': []}


# ---------------------------------------------------------------------------------------
# AC5 - byte-exact replay of the reference waveform
# ---------------------------------------------------------------------------------------
def ac5() -> dict:
    rep = load('vcd_replay.json')
    c, f, x = rep['comparison'], rep['forced_runs'], rep['cross_check']
    ev = {
        'cycles': c['cycles'],
        'output_bits_compared': c['output_bits_compared'],
        'output_bits_mismatched': c['output_bits_mismatched'],
        'stimulus_bits_mismatched': c['stimulus_bits_mismatched'],
        'our_outputs_with_x_or_z': c['our_outputs_with_x_or_z'],
        'exact': c['exact'],
        'cross_check_compared': x['compared'],
        'cross_check_mismatches': x['mismatches'],
        'forced_806_matches_reference': {k: v['matches_reference'] for k, v in f.items()},
    }
    ok = (c['output_bits_mismatched'] == 0 and c['stimulus_bits_mismatched'] == 0
          and c['our_outputs_with_x_or_z'] == 0 and c['exact']
          and x['mismatches'] == 0
          and all(v['matches_reference'] for v in f.values()))
    notes = ['semantic equality at every sampled instant, `x` included',
             'the one undriven net (806) was forced both ways; the reference is reproduced either way']
    return {'id': 'AC5', 'criterion': 'byte-exact replay of example_inputs.vcd',
            'status': PASS if ok else FAIL, 'evidence': ev, 'notes': notes, 'unmet': []}


# ---------------------------------------------------------------------------------------
# AC6 - the region partition: recovered and corroborated, not confirmed
# ---------------------------------------------------------------------------------------
def ac6() -> dict:
    """AC6: the region partition -- recovered and corroborated, which is not the same as confirmed.

    Returns `PARTIAL` by design, with the unmet reasons attached: the accepted input is unique so the
    chip's verdicts cannot distinguish our partition from a look-alike, the printed map does not read
    "JS" in our recovery, and one character of the corroborating message follows an undriven net.
    The gate fails if this row is upgraded or if those reasons are deleted.
    """
    c4 = load('c4_partition.json')
    e2 = load('e2_messages.json')
    ragged = [c for c in c4['candidates'] if not c['is_the_visible_column_rule']]
    stars_per_class = []
    if ragged:
        classes = ragged[0]['classes']
        # The artifact stores cells as linear indices (cell index = r*11 + c), not (r, c) pairs.
        stars_per_class = [sum(1 for i in classes[f] if T.GRID[i // 11][i % 11] == '*')
                           for f in ragged[0]['flops']]
    partition_ok = (len(ragged) == 1 and len(ragged[0]['flops']) == 11
                    and len(stars_per_class) == 11 and set(stars_per_class) == {2}
                    and sum(len(ragged[0]['classes'][f]) for f in ragged[0]['flops']) == 121)
    sol_count = ragged[0]['unique_solution']['solutions'] if ragged else None
    ev = {
        'capacity2_covers_found': c4['capacity2_cover_count'],
        'non_column_candidate': ragged[0]['name'] if ragged else None,
        'class_sizes': ragged[0]['class_sizes'] if ragged else None,
        'stars_per_class': stars_per_class,
        'solutions_under_this_partition': sol_count,
        'chip_corroborated_it': e2['partition_agreed_by_the_chip'],
        'boards_supporting_it': e2['boards_spelling_the_message'],
        'control_boards_against_it': e2['control_group']['boards_not_spelling_it'],
        'js_spelling_reproduced': False,
        'verdict_channel_can_confirm': False,
    }
    recovered = partition_ok and sol_count == 1 and ev['chip_corroborated_it']
    unmet = [
        'the printed map does not read "JS" in our recovery, so the spelling claim is not reproduced',
        'the chip cannot confirm it rather than a look-alike: the accepted input is unique (R16 §3), '
        'and look-alike partitions behave identically under rejection (R20 control 1: 188/200)',
        'one character of the corroborating message is indeterminate, following net 806',
    ]
    return {'id': 'AC6',
            'criterion': 'region partition recovered from the design\'s own latches, spelling "JS"',
            'status': PARTIAL if recovered else FAIL, 'evidence': ev,
            'notes': ['AC6 was re-scoped on 2026-09-13 (C4 R20): the partition is recovered and the '
                      'chip corroborates it through the message channel (E2), while confirmation and '
                      'the "JS" reading are not claimed'],
            'unmet': unmet}


def yardstick() -> dict:
    """The target itself, re-verified -- a broken yardstick would flatter everything below."""
    c = T.check_constraints(T.GRID)
    feed_grid = T.grid_from_bits(T.FEED_ORDER)
    printed_grid = T.grid_from_bits(T.WITNESS_AS_PRINTED)
    ev = {
        'witness_length': len(T.WITNESS_AS_PRINTED),
        'feed_order_reproduces_grid': feed_grid == T.GRID,
        'as_printed_is_rot180': printed_grid == T.rot180(T.GRID),
        'order_distinction_non_vacuous': T.rot180(T.GRID) != T.GRID,
        'ones_total': c['ones_total'],
        'per_row_ok': c['per_row_ok'],
        'per_col_ok': c['per_col_ok'],
        'no_adjacency': c['no_adjacency'],
        'success_cycle': T.SUCCESS_CYCLE,
        'message_table': T.MESSAGE_TABLE,
    }
    ok = (ev['feed_order_reproduces_grid'] and ev['as_printed_is_rot180']
          and ev['order_distinction_non_vacuous'] and c['total_ok'] and c['per_row_ok']
          and c['per_col_ok'] and c['no_adjacency'])
    return {'id': 'TARGET', 'criterion': 'the yardstick is internally consistent',
            'status': PASS if ok else FAIL, 'evidence': ev,
            'notes': ['tools/target.py verified against itself before being used as the yardstick'],
            'unmet': []}


def main(argv: list[str] | None = None) -> int:
    rows = [yardstick()]
    rows.append(ac1())
    rows.extend(ac2_ac3())
    rows.append(ac4())
    rows.append(ac5())
    rows.append(ac6())

    criteria = [r for r in rows if r['id'] != 'TARGET']
    counts = {s: sum(1 for r in criteria if r['status'] == s) for s in (PASS, PARTIAL, FAIL)}
    failing = [r['id'] for r in rows if r['status'] == FAIL]

    width = max(len(r['criterion']) for r in rows)
    print(f'{"":<7} {"criterion":<{width}}  status   evidence')
    for r in rows:
        head = (f'{r["id"]:<7} {r["criterion"]:<{width}}  {r["status"]:<8} ')
        print(head)
        for k, v in r['evidence'].items():
            if isinstance(v, (str, int, bool)) or v is None:
                print(f'{"":<{7 + width + 11}}  {k}: {v}')
    print()
    for r in rows:
        for n in r['notes']:
            print(f'  [{r["id"]}] {n}')
    print()
    print(f'criteria: {counts[PASS]} PASS, {counts[PARTIAL]} PARTIAL, {counts[FAIL]} FAIL '
          f'(of {len(criteria)}; plus the yardstick)')
    for r in rows:
        for u in r['unmet']:
            print(f'  UNMET [{r["id"]}]: {u}')

    report = {
        'generated_by': 'tools/puzzle/accept.py',
        'yardstick': 'tools/target.py, re-verified in this report',
        # F5 (2026-09-13): the matrix says which committed artifacts it read, so its own inputs are
        # named in the artifact. Recorded as repo-relative path -> sha256; check_stepF2 verifies it.
        'source': {rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() for rel in (
            'recon/derived/solutions.json', 'recon/derived/e1_messages.json',
            'recon/derived/e2_messages.json', 'recon/derived/c4_partition.json',
            'recon/derived/vcd_replay.json', 'tools/target.py')},
        'criteria': criteria,
        'counts': counts,
        'partial': [r['id'] for r in rows if r['status'] == PARTIAL],
        'claims_not_made': [u for r in rows for u in r['unmet']],
        'overall': PASS if not failing and counts[FAIL] == 0 else FAIL,
    }
    OUT.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(f'\nreport: {OUT.relative_to(ROOT).as_posix()}')
    print(f'ACCEPTANCE: {"PASS" if report["overall"] == PASS else "FAIL"}'
          + (f' ({counts[PARTIAL]} criterion PARTIAL, declared)' if counts[PARTIAL] else ''))
    return 0 if report['overall'] == PASS else 1


if __name__ == '__main__':
    raise SystemExit(main())
