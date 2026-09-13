#!/usr/bin/env python3
"""solve.py -- Phase D: solve the puzzle from the constraints we recovered, with our own search.

The published solution handed its constraint problem to a formal solver over an opaque netlist. This
module solves the problem *we* recovered, with two independently written enumerations, and requires
them to agree on both the solution and the total count. The point is not speed; it is that the answer
be **derived** rather than *reproduced from the known target* -- which is what the traceability table
in `docs/03_our_plan.md` sec.10 asks of AC1, and the last gap in it.

**The five constraints.** Four are the puzzle's mechanical rules; the fifth is the hidden one, and it
comes from C4's artifact, not from this file:

    C1  22 stars on the 121 cells                      (implied by C2 + 11 rows)
    C2  exactly two stars per row
    C3  exactly two stars per column
    C4  no two stars adjacent, diagonals included
    C5  at most two stars per class of the region partition   <- recon/derived/c4_partition.json

**What C5 is, and is not.** C4 recovered a *candidate* partition: it satisfies every test we can put
to it, with the strength of each test measured (plan C4 outcome, R20). So uniqueness below is
uniqueness **given that partition** -- and that is itself informative, because the published design
accepts exactly one board: a partition that admitted more than one solution could not be the rule the
chip enforces. The pair of counts this module reports is therefore a *discriminator* between the two
capacity-2 covers C4 found:

  * the ragged cover  -> exactly 1 solution, the accepted board          (consistent with being the rule)
  * the column cover  -> the visible two-per-column rule, so >1 solution  (cannot be the rule)

**D1-D4 are one computation.** The four plan steps share one search and one artifact
(`recon/derived/solutions.json`): the solver (D1), the two enumerations run to completion (D2), the
bounded count without the region constraint (D3) and both bit orders (D4). They are reported as
separate sections of one run rather than four passes over the same state.

    python -m tools.puzzle solve      # D1-D4 -> recon/derived/solutions.json
"""
from __future__ import annotations

import json
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import target as T
from tools.puzzle import verdict as V

ART_C4 = ROOT / 'recon' / 'derived' / 'c4_partition.json'
OUT = ROOT / 'recon' / 'derived' / 'solutions.json'

# Every row holds exactly two stars at least two columns apart: 45 of the 55 pairs.
ROW_PAIRS = [(a, b) for a, b in combinations(range(11), 2) if abs(a - b) >= 2]
# The same set as bitmasks, for the second enumerator: two bits set, never adjacent.
ROW_MASKS = [m for m in range(1 << 11) if bin(m).count('1') == 2 and not (m & (m << 1))]
# D3's bound: the region-free space is large, and this step is analysis, not part of the answer. The
# count is reported as "at least this many", with the bound that produced it.
MECH_CAP = 100_000
MECH_NODE_BUDGET = 30_000_000


# ---------------------------------------------------------------------------------------
# the constraint set
# ---------------------------------------------------------------------------------------
def region_partition() -> tuple[list[int], dict]:
    """The candidate partition from C4: a 121-entry class map, plus the record of where it came from.

    Refuses to guess. If C4's artifact is missing, or holds anything other than exactly one
    non-column capacity-2 cover, this raises rather than picking one -- an arbitrary choice here would
    silently change the constraint system the solve is against.
    """
    if not ART_C4.exists():
        raise SystemExit(f'missing {ART_C4.relative_to(ROOT).as_posix()} -- run: '
                         f'python -m tools.puzzle region-map')
    art = json.loads(ART_C4.read_text(encoding='utf-8'))
    ragged = [c for c in art['candidates'] if not c['is_the_visible_column_rule']]
    if len(ragged) != 1:
        raise SystemExit(f'expected exactly one non-column capacity-2 cover, found {len(ragged)}')
    cand = ragged[0]
    class_of = [-1] * 121
    for i, flop in enumerate(cand['flops']):
        for p in cand['classes'][flop]:
            if class_of[p] != -1:
                raise SystemExit(f'cell {p} appears in two classes')
            class_of[p] = i
    if -1 in class_of:
        raise SystemExit('the classes do not cover all 121 cells')
    stars = {r * 11 + c for r, c in V.answer_cells()}
    loads = [sum(1 for p, k in enumerate(class_of) if k == i and p in stars)
             for i in range(len(cand['flops']))]
    if loads != [2] * len(cand['flops']):
        raise SystemExit(f'the accepted board does not sit two-per-class: {loads}')
    return class_of, {'source': ART_C4.relative_to(ROOT).as_posix(),
                      'flops': cand['flops'], 'class_sizes': cand['class_sizes'],
                      'is_the_visible_column_rule': False}


# ---------------------------------------------------------------------------------------
# D1: our solver. Rows, pairs, cell sets. Recursive.
# ---------------------------------------------------------------------------------------
def solve_rows(class_of: list[int], cap: int = 2) -> dict:
    """Every board with two per row, two per column, no adjacency and at most two per class.

    Row by row: a row picks one of the 45 legal column pairs, the column counts and class counts are
    kept as small lists, and a row is only allowed if it does not touch the row above (which is the
    eight-neighbour rule between adjacent rows). The search runs to completion unless `cap` solutions
    have been found, so "exactly one" is a statement about a finished search.
    """
    colcnt, clscnt = [0] * 11, [0] * 11
    board: list[tuple[int, int] | None] = [None] * 11
    found: list[list[list[int]]] = []
    nodes = 0

    def dfs(r: int) -> bool:
        nonlocal nodes
        nodes += 1
        if r == 11:
            if all(v == 2 for v in colcnt):
                found.append([['*' if c in pair else '.' for c in range(11)]
                              for pair in board])
                return len(found) >= cap
            return False
        prev = board[r - 1]
        for a, b in ROW_PAIRS:
            if colcnt[a] >= 2 or colcnt[b] >= 2:
                continue
            ca, cb = class_of[r * 11 + a], class_of[r * 11 + b]
            if ca == cb:
                if clscnt[ca] + 2 > 2:
                    continue
            elif clscnt[ca] + 1 > 2 or clscnt[cb] + 1 > 2:
                continue
            if prev is not None and any(abs(x - y) <= 1 for x in (a, b) for y in prev):
                continue
            colcnt[a] += 1
            colcnt[b] += 1
            clscnt[ca] += 1
            clscnt[cb] += 1
            board[r] = (a, b)
            stop = dfs(r + 1)
            board[r] = None
            clscnt[ca] -= 1
            clscnt[cb] -= 1
            colcnt[a] -= 1
            colcnt[b] -= 1
            if stop:
                return True
        return False

    dfs(0)
    return {'solutions': found, 'count': len(found), 'nodes': nodes, 'complete': len(found) < cap}


# ---------------------------------------------------------------------------------------
# D2: the second enumerator. Bitmasks, integer sets, iterative. Written independently.
# ---------------------------------------------------------------------------------------
def enumerate_bitmask(class_of: list[int], cap: int = 2) -> dict:
    """The same problem, formulated over 11-bit row masks and enumerated with an explicit stack.

    Deliberately unlike the first: a board is a sequence of masks, column counts are kept as a tuple,
    and two consecutive rows are tested for touching with one bitwise expression --
    `next_mask & (mask | mask << 1 | mask >> 1)` is non-zero exactly when a star in the next row is
    adjacent (including diagonally) to one in this row. No `itertools`, no recursion, and the masks
    are visited in numeric order rather than generated per row. Agreement with `solve_rows` is
    therefore agreement between two different programs, not a re-run of one.
    """
    found: list[list[list[int]]] = []
    nodes = 0
    # stack entries: (row, col_load tuple, class_load tuple, prev_mask, masks so far)
    stack = [(0, (0,) * 11, (0,) * 11, 0, [])]
    while stack:
        row, cols, clss, prev, masks = stack.pop()
        if row == 11:
            if all(c == 2 for c in cols):
                found.append([['*' if m >> c & 1 else '.' for c in range(11)] for m in masks])
                if len(found) >= cap:
                    break
            continue
        forbid = (prev | prev << 1 | prev >> 1) & 0x7FF
        for m in ROW_MASKS:
            nodes += 1
            if m & forbid:
                continue
            bits = [c for c in range(11) if m >> c & 1]
            if any(cols[c] >= 2 for c in bits):
                continue
            ids = [class_of[row * 11 + c] for c in bits]
            if ids[0] == ids[1]:
                if clss[ids[0]] + 2 > 2:
                    continue
            elif clss[ids[0]] + 1 > 2 or clss[ids[1]] + 1 > 2:
                continue
            nc, ns = list(cols), list(clss)
            for c in bits:
                nc[c] += 1
            for i in ids:
                ns[i] += 1
            stack.append((row + 1, tuple(nc), tuple(ns), m, masks + [m]))
    return {'solutions': found, 'count': len(found), 'nodes': nodes, 'complete': len(found) < cap}


# ---------------------------------------------------------------------------------------
# validation: recomputed from the grid, not from the search
# ---------------------------------------------------------------------------------------
def validate(grid: list[str], class_of: list[int], n_classes: int) -> dict:
    """Recompute all five constraints from the grid alone.

    The four mechanical ones use `tools/target.py`'s own checker -- a different implementation from
    anything in this module, written for Step 1 before any solver existed -- and the fifth is counted
    here from the partition. A solver checking its own work with its own code would prove little.
    """
    mech = T.check_constraints(grid)
    loads = [0] * n_classes
    for r in range(11):
        for c in range(11):
            if grid[r][c] == '*':
                loads[class_of[r * 11 + c]] += 1
    return {'mechanical': {k: mech[k] for k in ('ones_total', 'row_counts', 'col_counts',
                                                'per_row_ok', 'per_col_ok', 'adjacent_pairs',
                                                'no_adjacency', 'total_ok')},
            'region_loads': loads, 'region_capacity_ok': max(loads) <= 2,
            'stars': sum(row.count('*') for row in grid),
            'all_five_ok': mech['total_ok'] and mech['per_row_ok'] and mech['per_col_ok']
                           and mech['no_adjacency'] and max(loads) <= 2}


def bits_of(grid: list[str]) -> str:
    """Feed order: bit for cell (0,0) first, row-major."""
    return ''.join('1' if grid[r][c] == '*' else '0' for r in range(11) for c in range(11))


def stage_solve() -> int:
    class_of, src = region_partition()
    n_classes = len(src['flops'])
    print(f'region constraint from: {src["source"]} ({n_classes} classes, sizes '
          f'{src["class_sizes"]})')

    # ---- D1/D2: our solver, then the independent enumerator ---------------------------
    a = solve_rows(class_of)
    b = enumerate_bitmask(class_of)
    print()
    print(f'D1  solver (rows / pairs / recursion)   : {a["count"]} solution(s), {a["nodes"]} nodes, '
          f'{"complete" if a["complete"] else "capped"}')
    print(f'D2  enumerator (bitmasks / stack)      : {b["count"]} solution(s), {b["nodes"]} nodes, '
          f'{"complete" if b["complete"] else "capped"}')
    grids_a = [tuple(''.join(row) for row in g) for g in a['solutions']]
    grids_b = [tuple(''.join(row) for row in g) for g in b['solutions']]
    agree_solution = grids_a == grids_b
    agree_count = a['count'] == b['count']
    print(f'    the two agree on the solution(s): {agree_solution}; on the count: {agree_count}')
    if not agree_solution or not agree_count:
        print('    DISAGREEMENT -- the two enumerations do not describe the same problem')
        return 1

    unique = a['count'] == 1
    print(f'    unique: {unique}')

    # ---- the answer itself ------------------------------------------------------------
    if not unique:
        print(f'    {a["count"]} solutions, so there is no single answer to emit')
        return 1
    grid = [''.join(row) for row in a['solutions'][0]]
    print()
    print('the board our solver derived:')
    for row in grid:
        print(f'    {row}')

    # ---- validation, from the grid, with the target's own checker ---------------------
    val = validate(grid, class_of, n_classes)
    print()
    print(f'validation (tools/target.py checker + the region counts): '
          f'stars {val["stars"]}, rows {val["mechanical"]["row_counts"]}, '
          f'cols {val["mechanical"]["col_counts"]}, '
          f'adjacent pairs {len(val["mechanical"]["adjacent_pairs"])}, '
          f'class loads {val["region_loads"]}')
    print(f'    all five constraints recomputed from the grid: {val["all_five_ok"]}')

    # ---- D4: both bit orders ----------------------------------------------------------
    feed = bits_of(grid)
    printed = feed[::-1]
    same_grid = grid == T.GRID
    ok_feed = feed == T.FEED_ORDER
    ok_printed = printed == T.WITNESS_AS_PRINTED
    print()
    print(f'D4  derived vector == target.FEED_ORDER        : {ok_feed}')
    print(f'    its reverse == WITNESS_AS_PRINTED          : {ok_printed}')
    print(f'    the board == the published grid            : {same_grid}')

    # ---- D3: how much work the region constraint does, and the cover discriminator ----
    mech_run = _mech_count()
    print()
    print(f'D3  without the region constraint        : {mech_run["solutions_seen"]} solutions seen, '
          f'{mech_run["nodes"]} nodes, '
          f'{"bound hit -- a lower bound" if mech_run["bounded"] else "complete"}')
    # The other capacity-2 cover C4 found is the visible column rule, so it adds no constraint. Its
    # count is therefore the region-free count, which the published uniqueness result rules out.
    col_class_of = _columns_class_of()
    col_run = solve_rows(col_class_of, cap=2)
    print(f'    with the COLUMN cover instead         : {col_run["count"]}+ solutions '
          f'(the visible two-per-column rule adds nothing), so it cannot be the hidden rule')

    report = {
        'generated_by': 'tools/puzzle/solve.py::stage_solve',
        'method': {
            'why': 'AC1 asks for the 121-bit vector. Reproducing it from the published target proves '
                   'nothing about our recovery, so it is derived here from the constraint system we '
                   'recovered: four mechanical rules plus the hidden constraint as C4 recovered it',
            'two_enumerators': 'solve_rows (rows / column pairs / recursion / cell-set pruning) and '
                               'enumerate_bitmask (11-bit row masks / explicit stack / one bitwise '
                               'adjacency test) are written separately and must agree on both the '
                               'solution and the count',
            'validator': 'tools/target.py::check_constraints recomputes the four mechanical rules from '
                         'the grid -- an implementation from Step 1, independent of both enumerators -- '
                         'and the fifth is counted from the partition',
            'region_constraint': 'the candidate partition from C4, not an assumption: it is what makes '
                                 'the count one, and it is a candidate (see C4 R20 for what that is '
                                 'worth)',
        },
        'constraints': {'ones': 22, 'per_row': 2, 'per_col': 2, 'no_adjacency': True,
                        'per_region_max': 2},
        'region_partition': src,
        'solutions_found': a['count'],
        'unique': unique,
        'solution': {
            'grid': grid,
            'cells': [[r, c] for r in range(11) for c in range(11) if grid[r][c] == '*'],
            'feed_order': feed,
            'as_printed': printed,
            'feed_order_bits': 121,
            'ones': feed.count('1'),
        },
        'enumerators': {
            'rows_dfs': {'count': a['count'], 'nodes': a['nodes'], 'complete': a['complete']},
            'bitmask_stack': {'count': b['count'], 'nodes': b['nodes'], 'complete': b['complete']},
            'agree_on_solution': agree_solution, 'agree_on_count': agree_count,
        },
        'validation': val,
        'against_target': {'feed_order_equals': ok_feed, 'as_printed_equals': ok_printed,
                           'board_equals': same_grid},
        'load_bearing': {
            'region_free': mech_run,
            'column_cover': {'solutions_seen': col_run['count'], 'cap': 2,
                             'note': 'the other capacity-2 cover C4 found is the visible column rule, '
                                     'so it adds no constraint; the published uniqueness result '
                                     '(exactly one accepted input) therefore rules it out as the '
                                     'hidden rule'},
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8', newline='\n')
    print()
    print(f'report: {OUT.relative_to(ROOT).as_posix()}')
    ok = unique and agree_solution and agree_count and val['all_five_ok'] and ok_feed \
        and ok_printed and same_grid
    print(f'PHASE D: {"PASS" if ok else "FAIL"}')
    return 0 if ok else 1


def _mech_count() -> dict:
    """The bounded region-free count (D3). Bounded on purpose: this is analysis, not the answer."""
    colcnt = [0] * 11
    board: list[tuple[int, int] | None] = [None] * 11
    total = nodes = 0

    def dfs(r: int) -> bool:
        nonlocal total, nodes
        nodes += 1
        if nodes > MECH_NODE_BUDGET or total >= MECH_CAP:
            return True
        if r == 11:
            if all(v == 2 for v in colcnt):
                total += 1
            return total >= MECH_CAP
        prev = board[r - 1]
        for a, b in ROW_PAIRS:
            if colcnt[a] >= 2 or colcnt[b] >= 2:
                continue
            if prev is not None and any(abs(x - y) <= 1 for x in (a, b) for y in prev):
                continue
            colcnt[a] += 1
            colcnt[b] += 1
            board[r] = (a, b)
            stop = dfs(r + 1)
            board[r] = None
            colcnt[a] -= 1
            colcnt[b] -= 1
            if stop:
                return True
        return False

    dfs(0)
    return {'solutions_seen': total, 'nodes': nodes,
            'bounded': total >= MECH_CAP or nodes > MECH_NODE_BUDGET,
            'cap': MECH_CAP, 'node_budget': MECH_NODE_BUDGET}


def _columns_class_of() -> list[int]:
    """A class map whose classes are the eleven columns (the visible rule, as a 'region' set)."""
    return [c for _r in range(11) for c in range(11)]


def main(argv: list[str] | None = None) -> int:
    stage = (argv or ['solve'])[0]
    # The four D stages are one computation; the names exist so the stage table and the plan line up.
    if stage not in ('solve', 'uniqueness', 'load-bearing', 'answer'):
        print(f'solve.py has no stage {stage!r}', file=sys.stderr)
        return 2
    return stage_solve()


if __name__ == '__main__':
    raise SystemExit(main())
