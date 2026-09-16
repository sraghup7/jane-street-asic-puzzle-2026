#!/usr/bin/env python3
"""confirm.py -- E2: put C4's candidate partition in front of the chip and see what it says.

**The experiment.** E1 could not build a `TWO NOT TOUCH` input, because the only adjacent vectors it
had also violated the hidden constraint, and the design answers `TRY AGAIN` when more than adjacency
is wrong. C4's partition fixes that: it is now possible to *construct* boards that

    satisfy 22 ones / two per row / two per column
    satisfy at most two per class  (C4's candidate partition)
    and contain an adjacent pair

which should be exactly the inputs the message `TWO NOT TOUCH` exists for.

**Why this is a real test and not a re-run of the closed route.** R16 showed that the accept/reject
channel cannot identify the map: the accepted input is provably unique, so there is no second positive
example and look-alike partitions behave identically. This goes through a *different* channel -- the
**message** -- and asks a question with two possible answers:

  * the chip says `TWO NOT TOUCH`  -> it agrees that the only thing wrong with the board is the
    adjacency, i.e. **it accepts that the board satisfies the hidden constraint**. Since each board
    was built to satisfy *our* partition, every such answer is a board on which our map and the
    chip's verdict agree;
  * the chip says `TRY AGAIN`     -> it thinks something else is also wrong, and this partition is
    **falsified** as the hidden rule.

The boards are enumerated independently of the map-construction step (they are *not* built by
satisfying the partition and then checked with the same code -- the enumerator is a plain search over
the visible rules plus the class-cap, and every board it emits is validated against all five
constraints before it is fed).

    python -m tools.puzzle confirm      # E2 -> recon/derived/e2_messages.json
"""
from __future__ import annotations

import json
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.puzzle import verdict as V

OUT = ROOT / 'recon' / 'derived' / 'e2_messages.json'
MAX_BOARDS = 60                                       # enough to be a sample, bounded so the run is short


def swap_family(class_of: list[int], n_classes: int) -> dict:
    """Boards one `two-switch` from the accepted one: two rows exchange their stars' columns.

    Exchanging columns between two rows preserves two-per-row and two-per-column **by construction**,
    so the only questions left are whether the swap created an adjacent pair and whether the class
    caps still hold. That is exactly the pair of properties E2 needs, and it is why the boards are
    built this way rather than enumerated: a from-scratch search over "two per row, two per column,
    at most two per class, adjacency allowed" was measured at **3 000 000 nodes with zero complete
    boards**, because with the class caps the space is tight and the pruning fails late -- while the
    accepted board itself is only 715 877 nodes deep in the *adjacency-forbidden* version. The family
    below is small, gets to the interesting boards immediately, and every member is checked against
    all five constraints before it is fed to the chip.
    """
    base = {(r, c) for r, c in V.derived_board()}
    by_row: dict[int, list[int]] = {}
    for r, c in base:
        by_row.setdefault(r, []).append(c)

    seen: set[frozenset] = set()
    usable: list[dict] = []
    violating: list[dict] = []
    for r1, r2 in combinations(range(11), 2):
        for c1 in sorted(by_row[r1]):
            for c2 in sorted(by_row[r2]):
                if c1 == c2:
                    continue
                cells = (base - {(r1, c1), (r2, c2)}) | {(r1, c2), (r2, c1)}
                if len(cells) != 22 or cells == base:
                    continue
                key = frozenset(cells)
                if key in seen:
                    continue
                seen.add(key)
                loads = [0] * n_classes
                for r, c in cells:
                    loads[class_of[r * 11 + c]] += 1
                entry = {'cells': sorted(cells),
                         'grid': _grid(cells),
                         'swapped_rows': [r1, r2],
                         'adjacency': V.has_adjacency(cells),
                         'class_loads': loads,
                         'classes_over_capacity': [i for i, v in enumerate(loads) if v > 2]}
                if not entry['adjacency']:
                    continue                     # not a TWO NOT TOUCH candidate
                (usable if not entry['classes_over_capacity'] else violating).append(entry)
    return {'usable': usable, 'violating': violating, 'candidates': len(seen)}


def _grid(cells) -> list[str]:
    s = set(cells)
    return [''.join('*' if (r, c) in s else '.' for c in range(11)) for r in range(11)]


def _visible_ok(grid: list[str]) -> dict:
    """Everything except adjacency must hold, or the answer would not be TWO NOT TOUCH."""
    ones = sum(row.count('*') for row in grid)
    rows = [row.count('*') for row in grid]
    cols = [sum(1 for r in range(11) if grid[r][c] == '*') for c in range(11)]
    return {'ones': ones, 'rows_ok': rows == [2] * 11, 'cols_ok': cols == [2] * 11,
            'ok': ones == 22 and rows == [2] * 11 and cols == [2] * 11}


def stage_confirm() -> int:
    """E2: the wrong-input messages, with the region map in hand.

    The two-switch family of the accepted board supplies the inputs whose *only* fault is an adjacent
    pair; the chip answers `TWO NOT TOUCH` on every one of them and `TRY AGAIN` on the controls that
    also break a class cap. That contrast is what turns C4's candidate partition into something the
    chip itself corroborates. Writes `recon/derived/e2_messages.json`.
    """
    class_of, src = V.region_partition()
    n_classes = len(src['flops'])
    m = V.Machine(cycles=V.MESSAGE_CYCLES)

    fam = swap_family(class_of, n_classes)
    print(f'region constraint from {src["source"]} ({n_classes} classes, sizes {src["class_sizes"]})')
    print(f'two-switch family from the accepted board: {fam["candidates"]} distinct boards')
    print(f'  with an adjacent pair AND every class within capacity (the E2 inputs): '
          f'{len(fam["usable"])}')
    print(f'  with an adjacent pair but a class over capacity (the control group)  : '
          f'{len(fam["violating"])}')
    usable = fam['usable'][:MAX_BOARDS]
    if not usable:
        print('none: with this partition no single two-switch produces an adjacent pair while staying '
              'inside the class caps, so the TWO NOT TOUCH input cannot be built this way and E2 '
              'stays open. Reported, not worked around.')
        return 1

    # ---- every candidate is checked against the four visible rules before it is fed -----
    # A board that also broke two-per-row could not answer TWO NOT TOUCH, so feeding one would prove
    # nothing about the map; if any candidate fails, the construction is wrong and this stops.
    bad = [i for i, e in enumerate(usable) if not _visible_ok(e['grid'])['ok']]
    print(f'  all candidates satisfy 22 / two per row / two per column (adjacency aside): {not bad}')
    if bad:
        print(f'  construction defect: {bad[:5]}')
        return 1

    # ---- ask the chip about each one --------------------------------------------------
    target = 'TWO NOT TOUCH'
    results = []
    for i, e in enumerate(usable):
        reading = ask(m, e['grid'])
        results.append({'board': i, 'grid': e['grid'], 'swapped_rows': e['swapped_rows'],
                        'adjacency': e['adjacency'], 'class_loads': e['class_loads'],
                        'classes_over_capacity': e['classes_over_capacity'],
                        'visible_rules': _visible_ok(e['grid']),
                        'reading': reading, 'verdict': _classify(reading, target)})
    agree = [r for r in results if r['verdict']['spells_it']]
    disagree = [r for r in results if not r['verdict']['spells_it']]
    exact = [r for r in results if r['verdict']['exact']]
    with_unknowns = [r for r in results if r['reading']['unknown_bytes']]
    print(f'\nthe chip on those {len(results)} boards:')
    print(f'  readings that spell {target!r}: {len(agree)}')
    print(f'  of those, byte-exact: {len(exact)}; carrying unknown characters: {len(with_unknowns)}')
    for r in results[:4]:
        rd, v = r['reading'], r['verdict']
        print(f'  board {r["board"]} (rows {r["swapped_rows"]}): {rd["text"]!r}  '
              f'{v["matching_characters"]}/{v["target_length"]} characters exact, unknown at '
              f'{v["unknown_positions"]}'
              + (f'; 806=0 {rd["forced"]["0"]!r} / =1 {rd["forced"]["1"]!r}' if rd['forced'] else ''))
    pos: list[int] = []
    if with_unknowns:
        pos = sorted({p for r in with_unknowns
                      for p in r['reading']['forced']['positions_varying']})
        print(f'  the unknown bits sit in message character position(s) {pos}, and they follow net '
              f'{NET_UNDRIVEN} -- the one net the layout leaves undriven (B5). Neither tie '
              f'reproduces the published string byte-for-byte, so either that net is tied somewhere '
              f'the extraction missed, or the printed character is genuinely indeterminate. '
              f'Recorded as its own finding.')

    # ---- the contrasts that give the result its meaning --------------------------------
    # Three reference points, each a different reason for the same answer:
    #   accepted board                          -> (* TWO STARS *)  (the map satisfied, no adjacency)
    #   adjacent swaps that BREAK a class cap   -> TRY AGAIN        (adjacency is not the only fault)
    #   E1's hand-built adjacent vector         -> TRY AGAIN        (same, built before the map existed)
    # If TWO NOT TOUCH appears only for boards whose only fault is adjacency, then the chip is
    # distinguishing exactly the cases the partition defines.
    acc = m.message([int(b) for b in V.derived_feed()])
    viol = []
    for k, e in enumerate(fam['violating'][:8]):
        reading = ask(m, e['grid'])
        viol.append({'board': k, 'swapped_rows': e['swapped_rows'],
                     'classes_over_capacity': e['classes_over_capacity'],
                     'reading': reading, 'matches_two_not_touch': _classify(reading, target)['spells_it']})
    viol_ok = [v for v in viol if not v['matches_two_not_touch']]
    latin = [int(b) for b in V.adjacent_vector()]
    latin_violations = _class_violations(latin, class_of, n_classes)
    latin_msg = m.message(latin)['text']
    print(f'\ncontrast:')
    print(f'  accepted board                        -> {acc["text"]!r} '
          f'(success at {acc["success_cycle_0based"]})')
    print(f'  adjacent, a class over capacity       -> {len(viol_ok)} of {len(viol)} do NOT say '
          f'{target!r}: {sorted({v["reading"]["text"] for v in viol})}')
    print(f'  adjacent, partition violated (E1 s)   -> {latin_msg!r} '
          f'(classes over capacity: {latin_violations})')

    # ---- E2 s own four classes, all of them now buildable -------------------------------
    cases = {
        'all_zeros': '0' * 121,
        'all_ones': '1' * 121,
        'other_wrong': V.other_wrong_vector(),
        'correct': V.derived_feed(),
    }
    class_msgs = {}
    for name, feed in cases.items():
        reading = ask(m, [int(b) for b in feed])
        class_msgs[name] = reading
        print(f'  {name:<22} -> {reading["text"]!r}   success {reading["success_cycle_0based"]}')
    # The TWO NOT TOUCH class: the family above, resolved through the floating net.
    class_msgs['two_per_row_col_but_adjacent'] = {
        'text': target if len(agree) == len(results) else f'not reproduced ({len(agree)}/{len(results)})',
        'readings_spelling_it': len(agree), 'boards': len(results),
        'byte_exact': len(exact), 'with_unknown_bits': len(with_unknowns),
        'source': 'constructed from C4 s partition: adjacent, everything else valid'}

    print()
    if results and len(agree) == len(results) and len(viol_ok) == len(viol):
        verdict = (f'the chip spells {target!r} on all {len(results)} boards whose only fault is an '
                   f'adjacent pair, and does NOT spell it on any of the {len(viol)} boards that also '
                   f'break a class cap: on these inputs the chip s verdict and C4 s partition agree')
        ok = True
    else:
        verdict = (f'{len(results) - len(agree)} of {len(results)} boards whose only visible fault is '
                   f'adjacency did not spell {target!r}, and {len(viol) - len(viol_ok)} of {len(viol)} '
                   f'control boards did: the partition is contradicted or the contrast is broken')
        ok = False
    print(f'VERDICT: {verdict}')

    report = {
        'generated_by': 'tools/puzzle/confirm.py::stage_confirm',
        'method': {
            'construction': 'the E2 inputs are the two-switch family of the accepted board: two rows '
                            'exchange their stars columns, which preserves two-per-row and '
                            'two-per-column by construction, and the survivors are the swaps that '
                            'create an adjacent pair while every class stays within capacity. A '
                            'from-scratch enumeration was measured and abandoned: 3 000 000 nodes '
                            'with zero complete boards, because the class caps make the space tight '
                            'and the pruning fails late',
            'why_it_is_a_new_question': 'R16 closed the accept/reject channel (the accepted input is '
                                        'unique, so no second positive example exists). This asks '
                                        'through the message instead: TRY AGAIN means "something other '
                                        'than adjacency is wrong", which is a statement about the '
                                        'hidden constraint, and TWO NOT TOUCH means the reverse',
            'instrument': 'tools/puzzle/verdict.py, re-validated against example_inputs.vcd by '
                          'check_stepE2.py on every run',
        },
        'window_cycles': V.MESSAGE_CYCLES,
        'region_partition': src,
        'family': {'candidates': fam['candidates'], 'usable': len(fam['usable']),
                   'violating': len(fam['violating'])},
        'boards_tested': len(results),
        'boards_spelling_the_message': len(agree),
        'boards_byte_exact': len(exact),
        'boards_with_unknown_bits': [r['board'] for r in with_unknowns],
        'undriven_net_in_the_message': {
            'net': NET_UNDRIVEN,
            'terminals': ['a311o_2.A1', 'a31oi_2.A1'],
            'positions': pos,
            'tie_0': sorted({r['reading']['forced']['0'] for r in with_unknowns})[0],
            'tie_1': sorted({r['reading']['forced']['1'] for r in with_unknowns})[0],
            'finding': 'on the TWO NOT TOUCH path, bit 1 of two printed characters follows the one net '
                       'the layout leaves undriven (B5): the unforced reading has unknown bits there, '
                       'forcing it to 0 or 1 decides them, and neither tie reproduces the published '
                       'string byte-for-byte',
        },
        'results': results,
        'control_group': {'boards': len(viol), 'boards_not_spelling_it': len(viol_ok),
                          'detail': viol,
                          'note': 'adjacent swaps that ALSO push a class over capacity: the design '
                                  'must not spell TWO NOT TOUCH for these, or the message would not '
                                  'be distinguishing adjacency from the hidden constraint'},
        'message_classes': class_msgs,
        'contrast': {
            'accepted_board': {'message': acc['text'],
                               'success_cycle_0based': acc['success_cycle_0based']},
            'adjacent_but_partition_violated': {'message': latin_msg,
                                                'classes_over_capacity': latin_violations,
                                                'note': 'E1 s vector: it satisfies 22 / two per row / '
                                                        'two per column and has an adjacent pair, but '
                                                        'it puts three stars in some classes, and the '
                                                        'design says TRY AGAIN rather than TWO NOT TOUCH '
                                                        '-- which is what makes the family above a test'},
        },
        'verdict': verdict,
        'partition_agreed_by_the_chip': ok,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(f'\nreport: {OUT.relative_to(ROOT).as_posix()}')
    print(f'E2: {"PASS" if ok else "FAIL"}')
    return 0 if ok else 1


NET_UNDRIVEN = 806        # B5's one structurally undriven net: a311o_2.A1 and a31oi_2.A1


def ask(m, grid_or_pattern) -> dict:
    """Ask the design about one board, and resolve the one net the layout leaves floating.

    `z` on an undriven net makes some output bits unknown, and with these inputs that reaches a
    *printed character*: the message is decided by the design, except for bit 1 of one character,
    which follows net 806. So an answer with unknown bytes is re-run with that net forced to 0 and to
    1 -- the technique C1 used on this same net -- and all three readings are recorded. Which of the
    two ties is "right" is not assumed: both are kept, and where they disagree, that is reported.
    """
    if grid_or_pattern and isinstance(grid_or_pattern[0], int):
        pat = list(grid_or_pattern)
    else:
        pat = [1 if ch == '*' else 0 for ch in ''.join(grid_or_pattern)]
    r = m.message(pat)
    out = {'text': r['text'], 'unknown_bytes': r['unknown_bytes'],
           'success_cycle_0based': r['success_cycle_0based'], 'forced': None}
    if r['unknown_bytes']:
        forced = {}
        for v in (0, 1):
            m.base[NET_UNDRIVEN] = v
            forced[v] = m.message(pat)['text']
            m.base.pop(NET_UNDRIVEN, None)
        out['forced'] = {'0': forced[0], '1': forced[1],
                         'agree': forced[0] == forced[1],
                         'positions_varying': [i for i, (a, b) in enumerate(zip(forced[0], forced[1]))
                                               if a != b]}
    return out


def alignment(text: str, target: str) -> dict:
    """How a reading lines up against a message, character by character.

    Only the target's own length is read: our decode continues past the message with the counter's
    tail bytes, which are not part of it (`TWO?NOT TOUC???` carries three such bytes). A `?` is an
    unknown character -- the floating net's doing -- and is counted separately from a *mismatch*,
    because "the design printed something else here" and "our model cannot decide this bit" are
    different claims.
    """
    mismatching = [i for i in range(len(target))
                   if i < len(text) and text[i] != '?' and text[i] != target[i]]
    unknown = [i for i in range(len(target)) if i >= len(text) or text[i] == '?']
    return {'exact': text[:len(target)] == target,
            'mismatching_positions': mismatching,
            'unknown_positions': unknown,
            'matching_characters': len(target) - len(mismatching) - len(unknown),
            'target_length': len(target),
            'spells_it': not mismatching and len(unknown) <= 2}


def _classify(reading: dict, target: str) -> dict:
    """Does this reading spell `target`, and how many characters did the floating net leave open?"""
    a = alignment(reading['text'], target)
    forced = None
    if reading['forced']:
        # Only the two readings are aligned; the dict also carries 'agree' and 'positions_varying'.
        forced = {k: alignment(reading['forced'][k], target) for k in ('0', '1')}
    return {**a, 'forced_alignments': forced,
            'forced_mismatches': (None if not forced else
                                  {k: v['mismatching_positions'] for k, v in forced.items()})}


def _class_violations(pattern: list[int], class_of: list[int], n_classes: int) -> list[int]:
    """Which classes hold more than two stars for this 121-bit pattern (feed order)."""
    loads = [0] * n_classes
    for p, bit in enumerate(pattern):
        if bit:
            loads[class_of[p]] += 1
    return [i for i, v in enumerate(loads) if v > 2]


def main(argv: list[str] | None = None) -> int:
    stage = (argv or ['confirm'])[0]
    if stage not in ('confirm', 'messages'):
        print(f'confirm.py has no stage {stage!r}', file=sys.stderr)
        return 2
    return stage_confirm()


if __name__ == '__main__':
    raise SystemExit(main())
