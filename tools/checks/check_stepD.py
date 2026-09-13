#!/usr/bin/env python3
"""check_stepD.py -- the D gate (D1-D4).

D is the step that turns "we reproduced the published answer" into "we derived it". The vector in
`solutions.json` is produced by our own search over the five constraints we recovered, and this gate
re-derives it from scratch rather than reading it:

* **D1/D2 — both enumerators, run again, live.** `solve_rows` (rows / column pairs / recursion) and
  `enumerate_bitmask` (11-bit masks / explicit stack) are re-run on the partition taken from C4's
  artifact, and both must still find exactly one solution -- the same one the artifact records.
  Agreement between two separately written programs is the claim; a count read out of a JSON file
  would not support it.
* **D3 — the bound is part of the result.** The region-free count is bounded on purpose, and the gate
  requires the artifact to say so (`bounded`, with the cap it hit) and to show the count is large.
  It also checks the discriminator: the *other* capacity-2 cover C4 found is the visible column rule,
  so it must admit more than one solution -- the published uniqueness result rules it out as the
  hidden rule, and that is the strongest statement available about which cover is the real one.
* **D4 — both bit orders, against the contract**, including the reverse (`WITNESS_AS_PRINTED`), which
  is the Step 2 ordering trap made explicit.
* **Validation from the grid.** All five constraints are recomputed by `tools/target.py`'s own checker
  (written in Step 1, before any solver existed) plus the class loads, never by the search that
  produced the board.

The honest limit, asserted rather than glossed: uniqueness holds **given C4's candidate partition**.
A different capacity-2 partition can admit more than one solution, and the artifact records which
partition was used and where it came from.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import target as T
from tools.puzzle import solve as S
from tools.puzzle import verdict as V

ART = ROOT / 'recon' / 'derived' / 'solutions.json'
MIN_REGION_FREE = 100_000
MIN_NODES = 1000

checks: list[dict] = []


def check(name: str, passed: bool, detail: str = '') -> None:
    checks.append({'check': name, 'passed': bool(passed), 'detail': detail})


def main() -> int:
    if not ART.exists():
        print(f'missing {ART.relative_to(ROOT).as_posix()}')
        print('run: python -m tools.puzzle solve')
        return 1
    art = json.loads(ART.read_text(encoding='utf-8'))

    # ---- the region constraint, re-derived from C4's artifact -------------------------
    class_of, src = S.region_partition()
    n_classes = len(src['flops'])
    stars = {r * 11 + c for r, c in V.answer_cells()}
    loads_answer = [0] * n_classes
    for p in stars:
        loads_answer[class_of[p]] += 1
    check('the region constraint is C4 s non-column cover, re-loaded and re-checked',
          loads_answer == [2] * n_classes
          and sorted(src['class_sizes']) == sorted(
              sum(1 for k in class_of if k == i) for i in range(n_classes)),
          f'{n_classes} classes, the accepted board sits {sorted(set(loads_answer))} per class')
    counts = Counter(class_of)
    check('the partition covers all 121 cells exactly once, with the recorded class sizes',
          set(counts) == set(range(n_classes)) and sum(counts.values()) == 121
          and sorted(counts.values()) == sorted(src['class_sizes']),
          f'{len(counts)} classes, sizes {sorted(counts.values())}')

    # ---- D1/D2: both enumerators, live -----------------------------------------------
    a = S.solve_rows(class_of)
    b = S.enumerate_bitmask(class_of)
    live_grids = [tuple(''.join(row) for row in g) for g in a['solutions']]
    check('D1 solver: exactly one solution, and the search finished',
          a['count'] == 1 and a['complete'], f'{a["count"]} solution(s), {a["nodes"]} nodes')
    check('D2 independent enumerator: exactly one solution, same board',
          b['count'] == 1 and [tuple(''.join(r) for r in g) for g in b['solutions']] == live_grids,
          f'{b["count"]} solution(s) over {b["nodes"]} nodes')
    check('the two enumerators agree on the count and on the board',
          a['count'] == b['count'] and len(live_grids) == 1,
          f'{a["count"]} vs {b["count"]}')
    check('the searches actually explored something',
          a['nodes'] >= MIN_NODES and b['nodes'] >= MIN_NODES,
          f'{a["nodes"]} and {b["nodes"]} nodes')

    # ---- the artifact must be that same board ----------------------------------------
    grid = [''.join(row) for row in a['solutions'][0]]
    check('the artifact s board is the board the gate just derived',
          art['solution']['grid'] == grid, 'identical' if art['solution']['grid'] == grid
          else f'artifact differs at row '
               f'{next((i for i, (x, y) in enumerate(zip(art["solution"]["grid"], grid)) if x != y), -1)}')
    check('the artifact agrees that it is unique',
          art['unique'] is True and art['solutions_found'] == 1,
          f"unique={art['unique']}, found={art['solutions_found']}")
    check('the artifact s enumerator records match the re-derivation',
          art['enumerators']['rows_dfs']['count'] == a['count']
          and art['enumerators']['bitmask_stack']['count'] == b['count']
          and art['enumerators']['agree_on_solution'] is True
          and art['enumerators']['agree_on_count'] is True,
          f"{art['enumerators']['rows_dfs']['count']}/"
          f"{art['enumerators']['bitmask_stack']['count']} recorded")

    # ---- validation from the grid, by the target's own checker ------------------------
    val = S.validate(grid, class_of, n_classes)
    check('all five constraints recomputed from the grid alone',
          val['all_five_ok'], f'stars {val["stars"]}, rows ok {val["mechanical"]["per_row_ok"]}, '
                             f'cols ok {val["mechanical"]["per_col_ok"]}, '
                             f'adjacency {val["mechanical"]["no_adjacency"]}, '
                             f'class loads {val["region_loads"]}')
    check('the validation uses the Step-1 checker, not the solver',
          art['validation']['mechanical']['row_counts'] == T.check_constraints(grid)['row_counts'],
          'target.py::check_constraints agrees')
    check('the artifact s recorded class loads match the re-derivation',
          art['validation']['region_loads'] == val['region_loads'],
          f"{art['validation']['region_loads']} vs {val['region_loads']}")

    # ---- D4: both bit orders ---------------------------------------------------------
    feed = S.bits_of(grid)
    check('the derived vector equals target.FEED_ORDER (AC1)',
          feed == T.FEED_ORDER and art['solution']['feed_order'] == T.FEED_ORDER,
          f'{feed.count("1")} ones over {len(feed)} bits')
    check('its reverse equals target.WITNESS_AS_PRINTED (the Step-2 ordering trap)',
          feed[::-1] == T.WITNESS_AS_PRINTED
          and art['solution']['as_printed'] == T.WITNESS_AS_PRINTED,
          'both orders asserted, so the trap cannot be a false negative')
    check('the derived board is the published grid',
          grid == T.GRID, 'identical')
    check('the artifact records the target comparisons as true',
          all(art['against_target'].values()), str(art['against_target']))

    # ---- D3: the bound, and the discriminator between the covers ---------------------
    lb = art['load_bearing']
    check('D3: the region-free count is large and reported with its bound',
          lb['region_free']['bounded'] is True
          and lb['region_free']['solutions_seen'] >= MIN_REGION_FREE
          and lb['region_free']['cap'] == S.MECH_CAP,
          f'{lb["region_free"]["solutions_seen"]} solutions seen, bound {lb["region_free"]["cap"]}')
    check('the unique solution is a member of the region-free set (it satisfies the four rules)',
          val['mechanical']['total_ok'] and val['mechanical']['per_row_ok']
          and val['mechanical']['per_col_ok'] and val['mechanical']['no_adjacency'],
          'the same board passes with no region constraint at all')
    col = S.solve_rows(S._columns_class_of(), cap=2)
    check('the column cover admits more than one solution, so it cannot be the hidden rule',
          col['count'] >= 2 and lb['column_cover']['solutions_seen'] >= 2,
          f'{col["count"]} solutions under the column cover (cap 2)')
    check('the artifact says why that cover is not the rule',
          'column' in lb['column_cover']['note'] and 'uniqueness' in lb['column_cover']['note'],
          lb['column_cover']['note'][:70] + '...')

    # ---- the honest limit ------------------------------------------------------------
    check('the artifact records which partition uniqueness is relative to',
          art['region_partition']['source'] == 'recon/derived/c4_partition.json'
          and art['method']['region_constraint'].startswith('the candidate partition from C4'),
          art['region_partition']['source'])

    # ---- anti-vacuity floors ---------------------------------------------------------
    check('floors: 121 bits, 22 ones, five constraints checked, two enumerators',
          len(feed) == 121 and feed.count('1') == 22 and val['all_five_ok']
          and a['nodes'] >= MIN_NODES and b['nodes'] >= MIN_NODES,
          f'{len(feed)} bits, {feed.count("1")} ones, {a["nodes"]:,}+{b["nodes"]:,} nodes')

    failed = [c for c in checks if not c['passed']]
    width = max(len(c['check']) for c in checks)
    for c in checks:
        print(f'  {"PASS" if c["passed"] else "FAIL"}  {c["check"]:<{width}}  {c["detail"]}')
    print()
    print(f'{len(checks) - len(failed)}/{len(checks)} checks passed')
    if failed:
        print('STEP D GATE: FAIL')
        return 1
    print('STEP D GATE: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
