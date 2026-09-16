#!/usr/bin/env python3
"""check_stepC4.py -- the C4 gate.

C4's subject is the hidden constraint, and most of its record is negative: no per-cell region decode
(R9, R10), no per-region counter (R11-R15), no window rule (R16), and no way to read the map back
out of the chip's own verdicts, because the accepted input is provably unique (R16). Three things
therefore have to be held in place rather than trusted:

* **The instrument.** Every negative above rests on "the evaluator's silence about a signal is
  evidence", so it is re-validated here against the chip's own recorded behaviour: replay
  `example_inputs.vcd`, 312 cycles, every sampled instant of `O` and `success`, zero mismatches and
  zero unknown bits. A gate that skipped this would be testing arithmetic, not the chip.
* **The structure (R18), re-derived.** `success` is the state of one flop; `i1594.D`'s cone is 104
  nets / 57 flops with a measured census; and between the accepted board and 20 boards that satisfy
  every visible rule, **exactly two** flops in that cone differ at the decision cycle. That is the
  hidden condition isolated to two latches, and the counts are re-derived rather than quoted.
* **The candidate partition (R19/R20) and its controls.** The design's own latches produce an exact
  cover of the 121 cells, and the cover that is not the visible column rule does pin the accepted
  board as the unique solution -- while the controls measure how weak that is: the design's
  rejections cannot separate it from a look-alike partition, and look-alike partitions pin the answer
  often enough that uniqueness is evidence, not proof. The gate asserts the measurements, never the
  interpretation. Nothing here claims the map is recovered.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.puzzle import verdict as V

ART_C4 = ROOT / 'recon' / 'derived' / 'c4_partition.json'
BLOCKS = ROOT / 'recon' / 'derived' / 'blocks.json'

# Recorded by C4 R18 and re-derived below.
CONE_NETS = 104
CONE_FLOPS = 57
CENSUS = {'column_counter_bit': 27, 'winning_only_bit': 18, 'ones_counter': 6,
          'unclassified': 5, 'message_counter': 1}
HIDDEN_FLOPS = {'i0455': 1, 'i0798': 0}
CONE_DEPTH = 12
N_BOARDS = 20
RAGGED_SIZES = [4, 5, 6, 7, 8, 8, 9, 11, 14, 21, 28]

checks: list[dict] = []


def check(name: str, passed: bool, detail: str = '') -> None:
    checks.append({'check': name, 'passed': bool(passed), 'detail': detail})


def main() -> int:
    for path in (ART_C4, BLOCKS):
        if not path.exists():
            print(f'missing {path.relative_to(ROOT).as_posix()}')
            print('run: python -m tools.puzzle region-map')
            return 1
    art = json.loads(ART_C4.read_text(encoding='utf-8'))
    role = json.loads(BLOCKS.read_text(encoding='utf-8'))['assignment']

    m = V.Machine(cycles=126)

    # ---- 1. the instrument, against the chip's own recorded behaviour -----------------
    m312 = V.Machine(cycles=312)
    rep = m312.reference_replay()
    check('the evaluator reproduces example_inputs.vcd over 312 cycles',
          rep['mismatches'] == 0 and rep['first_mismatch'] is None,
          f"{rep['mismatches']} mismatches, first {rep['first_mismatch']}")
    check('the comparison is not vacuous: 312 x 9 output bits compared',
          rep['output_comparisons'] == 9 * rep['cycles'] == 2808,
          f"{rep['output_comparisons']} comparisons over {rep['cycles']} cycles")
    check('no unknown bits in our outputs on the reference waveform',
          rep['unknown_bits'] == 0, f"{rep['unknown_bits']} unknown")

    # ---- 2. the structure: one verdict flop, and its cone ----------------------------
    succ = m.ports['success']
    direct = m.flop_of_net.get(succ)
    drv = m.driver.get(succ)
    via_gate = drv is not None and drv[0] == 'i1594' and drv[1] == 'Q'
    check('the `success` net is flop i1594 s Q output (directly, or through a gate it drives)',
          direct == 'i1594' or via_gate,
          f'direct Q net of {direct}' if direct else
          (f'through {drv[0]}.{drv[1]}' if via_gate else 'no driver found'))
    check('the success port belongs to i1594 and to no other flop',
          m.flop_of_net.get(succ) in (None, 'i1594'),
          f'{m.flop_of_net.get(succ)}')

    root = m.flop['i1594']['d']
    cone, gates = m.cone(root, depth=CONE_DEPTH)
    cone_flops = sorted(m.flop_of_net[n] for n in cone if n in m.flop_of_net)
    check(f'i1594.D cone is {CONE_NETS} nets / {CONE_FLOPS} flops (depth {CONE_DEPTH})',
          len(cone) == CONE_NETS and len(cone_flops) == CONE_FLOPS,
          f'{len(cone)} nets, {len(cone_flops)} flops')

    census: dict[str, int] = {}
    for f in cone_flops:
        census[role.get(f, 'unknown')] = census.get(role.get(f, 'unknown'), 0) + 1
    check('the cone s flops fall in the recorded blocks',
          census == CENSUS, f'{census} vs recorded {CENSUS}')

    # ---- 3. the hidden condition: exactly two latches differ -------------------------
    board = V.derived_board()
    acc = m.run(V.pattern_from_cells(board), watch_flops=cone_flops)
    boards = V.valid_boards(600, N_BOARDS)
    check(f'{N_BOARDS} rejected-but-visible-valid boards were generated',
          len(boards) == N_BOARDS, f'{len(boards)} boards')
    check('every generated board satisfies the four visible rules',
          all(V.visible_rules(b['cells'])['all_ok'] for b in boards),
          'rows/cols/adjacency all hold')
    rejs = [m.run(V.pattern_from_cells(b['cells']), watch_flops=cone_flops) for b in boards]
    k = V.DECISION_CYCLE
    diff = [f for f in cone_flops
            if all(r['flops'][f][k] != acc['flops'][f][k] for r in rejs)]
    check('exactly two flops in the cone differ from every rejected board at the decision cycle',
          sorted(diff) == sorted(HIDDEN_FLOPS), f'{sorted(diff)}')
    signs_ok = all(f in diff
                   and acc['flops'][f][k] == HIDDEN_FLOPS[f]
                   and all(r['flops'][f][k] == 1 - HIDDEN_FLOPS[f] for r in rejs)
                   for f in HIDDEN_FLOPS)
    check('their values are the recorded ones (i0455 1/0, i0798 0/1)',
          signs_ok,
          '; '.join(f'{f}: accepted {acc["flops"][f][k]}, rejected '
                    f'{sorted({r["flops"][f][k] for r in rejs})}' for f in sorted(HIDDEN_FLOPS)))

    # ---- 4. the candidate partition: structure, from the artifact and from the design --
    cands = art['candidates']
    check('the artifact records at least one eleven-class candidate', len(cands) >= 1, f'{len(cands)}')
    ragged = [c for c in cands if not c['is_the_visible_column_rule']]
    check('the rows identify a cover that is not the visible column rule',
          len(ragged) == 1, f'{len(ragged)} such cover(s)')
    if not ragged:
        return report()
    rag = ragged[0]
    check('the artifact selected the ragged cover (the one with exactly one solution)',
          art.get('selected') == rag['name'],
          f"selected {art.get('selected')!r}, the ragged cover is named {rag['name']!r}")
    classes = [set(rag['classes'][f]) for f in rag['flops']]
    covered = set().union(*classes)
    overlaps = sum(len(a & b) for i, a in enumerate(classes) for b in classes[i + 1:])
    stars_per = [len(c & {r * 11 + c2 for r, c2 in board}) for c in classes]
    check('the classes are eleven, disjoint, and cover all 121 cells',
          len(classes) == 11 and overlaps == 0 and covered == set(range(121)),
          f'{len(classes)} classes, {overlaps} overlapping cells, {len(covered)} covered')
    check('every class holds exactly two of the derived board s stars',
          stars_per == [2] * 11, f'{stars_per}')
    check('the class sizes are the recorded ones',
          sorted(len(c) for c in classes) == RAGGED_SIZES,
          f'{sorted(len(c) for c in classes)}')

    # Freshness: the artifact is not stale. Re-run single-star inputs at a deterministic sample of
    # cells and demand that the class flop which fires is the one whose class holds that cell.
    owner = {}
    for f, c in zip(rag['flops'], classes):
        for p in c:
            owner[p] = f
    sample = list(range(0, 121, 10))
    live_ok, live_bad = 0, []
    for p in sample:
        final = m.run([1 if i == p else 0 for i in range(121)])['final']
        fired = [f for f in rag['flops'] if final.get(f) == 1]
        if fired == [owner[p]]:
            live_ok += 1
        else:
            live_bad.append((p, fired, owner[p]))
    check(f'the artifact s trigger sets are re-derived live at {len(sample)} sampled cells',
          not live_bad, f'{live_ok}/{len(sample)} cells' + (f' bad {live_bad[:3]}' if live_bad else ''))

    # ---- 5. the test the partition passes, and how strong it is ----------------------
    co = V.class_of_grid(classes)
    got, nodes = V.count_solutions(co)
    check('the candidate + the visible rules has exactly one solution, the accepted board',
          got == 1, f'{got} solution(s) over {nodes} search nodes')
    tiny = V.uniqueness(co, node_budget=10)
    check('a search stopped by its node budget is never reported as unique',
          tiny['complete'] is False and tiny['unique'] is False,
          f"{tiny['solutions']} found in {tiny['nodes']} nodes, complete={tiny['complete']}")
    mech, mech_nodes = V.count_solutions(None, cap=1000, node_budget=2_000_000)
    check('the visible rules alone do not pin it (the region constraint does work)',
          mech >= 1000, f'{mech} solutions seen, cap reached ({mech_nodes} nodes)')

    # ---- 6. the artifact must not outrun the re-derivation ---------------------------
    check('the artifact s recorded solution count matches the re-derivation',
          (rag.get('unique_solution') or {}).get('solutions') == got,
          f"artifact {(rag.get('unique_solution') or {}).get('solutions')} vs live {got}")
    check('the artifact records one candidate per eleven-class cover',
          art.get('eleven_class_cover_count') == len(cands), f"{art.get('eleven_class_cover_count')}")
    check('the artifact s trigger-set total agrees with the sets it stores',
          art.get('flops_with_a_trigger_set') == len(art.get('trigger_sets') or {}),
          f"{art.get('flops_with_a_trigger_set')} vs {len(art.get('trigger_sets') or {})}")
    check('the artifact s class sizes match the classes it stores',
          sorted(rag['class_sizes']) == sorted(len(c) for c in classes),
          f"{rag['class_sizes']}")
    col_cands = [c for c in cands if c['is_the_visible_column_rule']]
    check('the cover flagged as the visible column rule really is the eleven columns',
          len(col_cands) == 1
          and V.is_columns_cover([set(col_cands[0]['classes'][f]) for f in col_cands[0]['flops']])
          and not V.is_columns_cover(classes),
          f"{len(col_cands)} flagged as the column rule; the ragged cover is not it, checked "
          f"through the same canonical comparison")

    # Control B: uniqueness is not generic. The stage now tests 200 look-alikes, which is too slow to
    # redo in full on every gate run; the gate instead re-derives the first 20 of that same seeded
    # sequence live -- same seed, same generator, so these are exactly the stage's own first 20 draws,
    # not a fresh sample -- and checks the artifact recorded the full 200.
    rng = random.Random(20260913)
    sample = [V.same_shape_partitions(rng, rag['class_sizes'], board) for _ in range(20)]
    live_unique = sum(1 for look in sample if V.uniqueness(look)['unique'])
    lu = rag.get('lookalike_uniqueness') or {}
    check('control B: the first 20 of the recorded 200 look-alikes are re-derived live, and rare',
          lu.get('tested') == 200 and lu.get('seed') == 20260913
          and live_unique <= len(sample) // 2,
          f'{live_unique} of {len(sample)} re-derived unique (of {lu.get("tested")} recorded, '
          f'{lu.get("also_unique")} also unique)')

    # ---- 7. what C4 did *not* find is still not claimed ------------------------------
    check('no part of this gate claims the map is recovered: the artifact says what it is not',
          'not' in art['method']['what_it_is_not'],
          art['method']['what_it_is_not'][:80] + '...')

    # ---- anti-vacuity floors ---------------------------------------------------------
    check('floors: cone, boards, classes and cells all non-trivial',
          len(cone_flops) >= 50 and len(boards) == N_BOARDS and len(classes) == 11
          and len(covered) == 121,
          f'{len(cone_flops)} flops, {len(boards)} boards, {len(classes)} classes, '
          f'{len(covered)} cells')
    check('the derived board itself has 22 stars, so "two per class" is a real constraint',
          len(board) == 22, f'{len(board)}')

    return report()


def report() -> int:
    failed = [c for c in checks if not c['passed']]
    width = max(len(c['check']) for c in checks)
    for c in checks:
        print(f'  {"PASS" if c["passed"] else "FAIL"}  {c["check"]:<{width}}  {c["detail"]}')
    print()
    print(f'{len(checks) - len(failed)}/{len(checks)} checks passed')
    if failed:
        print('STEP C4 GATE: FAIL')
        return 1
    print('STEP C4 GATE: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
