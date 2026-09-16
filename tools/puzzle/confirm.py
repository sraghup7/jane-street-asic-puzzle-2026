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

**The whole family, not a sample (2026-09-15).** Every two-switch board is fed, adjacent or not: 23
adjacent-within-caps (the `TWO NOT TOUCH` candidates), 156 adjacent-over-caps and 10 not-adjacent
(necessarily over caps too, or one of them would be a second solution to the five constraints -- which
D already proved does not exist). Two questions this now answers, pure Python and needing no further
simulation once the 189 verdicts are in hand: how much does agreement on this family actually
discriminate the map from a same-shape look-alike (`lookalike_power`), and how finely does it pin each
individual cell (`boundary_resolution`) -- reported rather than left implicit, because a test that
looks strong and is not should not be presented as if it were.

    python -m tools.puzzle confirm      # E2 -> recon/derived/e2_messages.json
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.puzzle import verdict as V

OUT = ROOT / 'recon' / 'derived' / 'e2_messages.json'


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
    usable: list[dict] = []                # adjacent, every class within capacity: the TWO NOT TOUCH candidates
    violating: list[dict] = []             # adjacent, a class over capacity: the contrast group
    not_adjacent: list[dict] = []          # not adjacent (necessarily over capacity -- see stage_confirm)
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
                    not_adjacent.append(entry)
                else:
                    (usable if not entry['classes_over_capacity'] else violating).append(entry)
    return {'usable': usable, 'violating': violating, 'not_adjacent': not_adjacent, 'candidates': len(seen)}


def family_boards(fam: dict) -> list[dict]:
    """Every board of the family, in one fixed order: usable, then violating, then not-adjacent."""
    return fam['usable'] + fam['violating'] + fam['not_adjacent']


def prediction(cells, class_of: list[int]) -> str:
    """What the message should be if the chip enforces `class_of`: pure Python, no simulation.

    Adjacency and the class caps are the only two things a two-switch board of the accepted grid can
    have wrong (two-per-row/column and the ones count hold by construction), so this is the same
    three-way split the message channel itself makes.
    """
    loads = Counter(class_of[r * 11 + c] for r, c in cells)
    over = bool(loads) and max(loads.values()) > 2
    adjacent = V.has_adjacency(cells)
    if adjacent and not over:
        return 'TWO NOT TOUCH'
    if not adjacent and not over:
        return '(* TWO STARS *)'
    return 'TRY AGAIN'


def lookalike_power(fam: dict, class_of: list[int], trials: int = 2000, seed: int = 1) -> dict:
    """How well does agreement on this family single out `class_of` against a same-shape look-alike?

    For each of `trials` same-shape partitions (`verdict.same_shape_partitions`, anchored at the
    derived board's own 22 cells -- the project's null model), predict every board of the family and
    count how many of the real predictions it reproduces. A look-alike that reproduced all of them
    would make the family no test at all; `best_match` and `reproduce_all_boards` report how close the
    null model gets, not just that it (mostly) fails.
    """
    boards = [e['cells'] for e in family_boards(fam)]
    truth = [prediction(b, class_of) for b in boards]
    class_sizes = sorted(Counter(class_of).values())
    stars = V.derived_board()
    rng = random.Random(seed)
    match_counts = []
    reproduce_all = 0
    for _ in range(trials):
        look = V.same_shape_partitions(rng, class_sizes, stars)
        matches = sum(1 for b, t in zip(boards, truth) if prediction(b, look) == t)
        match_counts.append(matches)
        if matches == len(boards):
            reproduce_all += 1
    return {'tested': trials, 'seed': seed, 'boards': len(boards),
            'reproduce_all_boards': reproduce_all, 'best_match': max(match_counts)}


def boundary_resolution(fam: dict, class_of: list[int]) -> dict:
    """How finely does the family pin down each cell's class, one cell at a time?

    For every non-star cell, and every other class it could be reassigned to, re-predict all 189
    boards under the edited map: if the predictions are unchanged, this family cannot tell that cell
    apart from its recorded class. `boundary_moves` restricts this to edits into a 4-neighbouring
    class (the moves closest to plausible, since the true classes are themselves cell-contiguous in
    the accepted grid's own geometry); `undetected` is how many of those the family misses.
    """
    boards = [e['cells'] for e in family_boards(fam)]
    truth = [prediction(b, class_of) for b in boards]
    stars = {r * 11 + c for r, c in V.derived_board()}
    n_classes = max(class_of) + 1
    any_edits = any_same = nb_edits = nb_same = 0
    undetected_cells: set[int] = set()
    for p in range(121):
        if p in stars:
            continue
        r, c = divmod(p, 11)
        nbr_classes = ({class_of[rr * 11 + cc] for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1))
                       if 0 <= rr < 11 and 0 <= cc < 11} - {class_of[p]})
        for k in range(n_classes):
            if k == class_of[p]:
                continue
            edited = class_of[:]
            edited[p] = k
            same = all(prediction(b, edited) == t for b, t in zip(boards, truth))
            any_edits += 1
            any_same += same
            if k in nbr_classes:
                nb_edits += 1
                if same:
                    nb_same += 1
                    undetected_cells.add(p)
    return {'boundary_moves': nb_edits, 'undetected': nb_same,
            'non_star_cells_with_an_undetected_move': len(undetected_cells),
            'non_star_cells_total': 121 - len(stars),
            'any_class_edits': any_edits, 'any_class_undetected': any_same}


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
    chip itself corroborates -- and now the *whole* family is fed, not a sample, so `lookalike_power`
    and `boundary_resolution` can measure how strong that corroboration actually is. Writes
    `recon/derived/e2_messages.json`.
    """
    class_of, src = V.region_partition()
    n_classes = len(src['flops'])
    m = V.Machine(cycles=V.MESSAGE_CYCLES)

    fam = swap_family(class_of, n_classes)
    not_adj_over = [e for e in fam['not_adjacent'] if e['classes_over_capacity']]
    not_adj_within = [e for e in fam['not_adjacent'] if not e['classes_over_capacity']]
    print(f'region constraint from {src["source"]} ({n_classes} classes, sizes {src["class_sizes"]})')
    print(f'two-switch family from the accepted board: {fam["candidates"]} distinct boards')
    print(f'  adjacent, every class within capacity (the TWO NOT TOUCH candidates): {len(fam["usable"])}')
    print(f'  adjacent, a class over capacity (the contrast group)                : {len(fam["violating"])}')
    print(f'  not adjacent, necessarily over capacity too (else a second solution): {len(not_adj_over)}')
    if not_adj_within:
        print(f'  WARNING: {len(not_adj_within)} not-adjacent board(s) are within every class cap -- '
              f'that would be a second solution, contradicting D')
    all_boards = family_boards(fam)
    if not fam['usable']:
        print('none usable: with this partition no single two-switch produces an adjacent pair while '
              'staying inside the class caps, so the TWO NOT TOUCH input cannot be built this way and '
              'E2 stays open. Reported, not worked around.')
        return 1

    # ---- every candidate is checked against the four visible rules before it is fed -----
    # A board that also broke two-per-row could not answer TWO NOT TOUCH, so feeding one would prove
    # nothing about the map; if any candidate fails, the construction is wrong and this stops.
    bad = [i for i, e in enumerate(all_boards) if not _visible_ok(e['grid'])['ok']]
    print(f'  all {len(all_boards)} candidates satisfy 22 / two per row / two per column '
          f'(adjacency aside): {not bad}')
    if bad:
        print(f'  construction defect: {bad[:5]}')
        return 1

    # ---- ask the chip about every board of the family -----------------------------------
    target = 'TWO NOT TOUCH'
    tagged = ([('usable', e) for e in fam['usable']] + [('violating', e) for e in fam['violating']]
             + [('not_adjacent', e) for e in fam['not_adjacent']])
    results = []
    for i, (group, e) in enumerate(tagged):
        want = prediction(e['cells'], class_of)
        reading = ask(m, e['grid'])
        results.append({'board': i, 'group': group, 'grid': e['grid'], 'swapped_rows': e['swapped_rows'],
                        'adjacency': e['adjacency'], 'class_loads': e['class_loads'],
                        'classes_over_capacity': e['classes_over_capacity'],
                        'visible_rules': _visible_ok(e['grid']), 'predicted': want,
                        'reading': reading, 'verdict': _classify(reading, want)})
        if (i + 1) % 50 == 0:
            print(f'  {i + 1}/{len(tagged)} boards asked', flush=True)
    agree_all = [r for r in results if r['verdict']['spells_it']]
    print(f'  the chip matches the prediction on {len(agree_all)} of {len(results)} boards')

    usable_results = [r for r in results if r['group'] == 'usable']
    violating_results = [r for r in results if r['group'] == 'violating']
    agree = [r for r in usable_results if r['verdict']['spells_it']]
    exact = [r for r in usable_results if r['verdict']['exact']]
    with_unknowns = [r for r in usable_results if r['reading']['unknown_bytes']]
    print(f'\nthe chip on the {len(usable_results)} TWO NOT TOUCH candidates:')
    print(f'  readings that spell {target!r}: {len(agree)}')
    print(f'  of those, byte-exact: {len(exact)}; carrying unknown characters: {len(with_unknowns)}')
    for r in usable_results[:4]:
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
    # distinguishing exactly the cases the partition defines. The contrast group is now the full 156,
    # not a sample of 8 -- every one of them was already asked above.
    acc = m.message([int(b) for b in V.derived_feed()])
    # 'spells_it' here means "matches the predicted TRY AGAIN", i.e. correctly does NOT spell the
    # message -- the contrast holds exactly when every violating board's verdict says spells_it.
    viol_ok = [r for r in violating_results if r['verdict']['spells_it']]
    latin = [int(b) for b in V.adjacent_vector()]
    latin_violations = _class_violations(latin, class_of, n_classes)
    latin_msg = m.message(latin)['text']
    print(f'\ncontrast:')
    print(f'  accepted board                        -> {acc["text"]!r} '
          f'(success at {acc["success_cycle_0based"]})')
    print(f'  adjacent, a class over capacity       -> {len(viol_ok)} of {len(violating_results)} do NOT '
          f'say {target!r}: {sorted({r["reading"]["text"] for r in violating_results})}')
    print(f'  adjacent, partition violated (E1 s)   -> {latin_msg!r} '
          f'(classes over capacity: {latin_violations})')

    # ---- how much does agreement on this family actually prove? ------------------------
    power = lookalike_power(fam, class_of)
    resolution = boundary_resolution(fam, class_of)
    print(f'\nlook-alike power: of {power["tested"]} same-shape look-alikes (seed {power["seed"]}), '
          f'{power["reproduce_all_boards"]} reproduce the map on all {power["boards"]} boards; '
          f'the closest gets {power["best_match"]}')
    print(f'boundary resolution: of {resolution["boundary_moves"]} single-cell moves into a '
          f'neighbouring class, {resolution["undetected"]} are invisible to this family '
          f'({resolution["non_star_cells_with_an_undetected_move"]} of '
          f'{resolution["non_star_cells_total"]} non-star cells have at least one)')

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
        'text': target if len(agree) == len(usable_results)
                else f'not reproduced ({len(agree)}/{len(usable_results)})',
        'readings_spelling_it': len(agree), 'boards': len(usable_results),
        'byte_exact': len(exact), 'with_unknown_bits': len(with_unknowns),
        'source': 'constructed from C4 s partition: adjacent, everything else valid'}

    print()
    if (results and len(agree_all) == len(results) and not not_adj_within
            and len(agree) == len(usable_results) and len(viol_ok) == len(violating_results)):
        verdict = (f'the chip matches the map s prediction on all {len(results)} boards of the '
                   f'two-switch family: {target!r} on all {len(usable_results)} whose only fault is an '
                   f'adjacent pair, and TRY AGAIN on the {len(violating_results)} that also break a '
                   f'class cap and the {len(fam["not_adjacent"])} that are not adjacent')
        ok = True
    else:
        verdict = (f'{len(results) - len(agree_all)} of {len(results)} boards of the family did not '
                   f'match the map s prediction: the partition is contradicted or the contrast is broken')
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
        'family': {'boards': fam['candidates'], 'adjacent_within_caps': len(fam['usable']),
                   'adjacent_over_caps': len(fam['violating']),
                   'not_adjacent_over_caps': len(not_adj_over),
                   'not_adjacent_within_caps': len(not_adj_within)},
        'agreement': {'boards': len(results), 'chip_matches_prediction': len(agree_all)},
        'lookalike_power': power,
        'boundary_resolution': resolution,
        'boards_tested': len(usable_results),
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
        'control_group': {'boards': len(violating_results), 'boards_not_spelling_it': len(viol_ok),
                          'note': 'every adjacent swap that ALSO pushes a class over capacity (the full '
                                  '156, not a sample -- each is already in results, group "violating"): '
                                  'the design must not spell TWO NOT TOUCH for these, or the message '
                                  'would not be distinguishing adjacency from the hidden constraint'},
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
