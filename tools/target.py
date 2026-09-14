#!/usr/bin/env python3
"""target.py -- the acceptance contract for this project, as machine-checkable constants.

The target answer was published after submissions closed. Before we treat it as the
yardstick (Step 5), we independently re-verify it here: derive the 11x11 grid from the
stated bit vector, check the four mechanical constraints, and compare against the
stated ASCII grid. If the published target were internally inconsistent, we need to
know now rather than after building an extractor.

    python tools/target.py          # -> prints the verification report
"""
from __future__ import annotations

import sys

# --- stated target, transcribed from the published writeup --------------------
ANSWER_STRING = 'TWO STARS'
SUCCESS_CYCLE = 126
GRID_SIZE = 11
FEED_BITS = GRID_SIZE * GRID_SIZE          # 121

# The witness string exactly as the published writeup prints it, which is the SMT
# solver's dump of `input_data[120:0]` -- i.e. MSB (bit 120) FIRST. The first
# character of this string is therefore the LAST bit clocked into `I`, not the first.
#
# CAUTION, and this bit us once already: read row-major top-left, THIS string gives
# the published grid rotated 180 degrees. It must be reversed to get feed order.
# The writeup's prose ("first bit fed is top-left") describes the GRID correctly but
# does not flag that the printed witness is in the opposite order.
WITNESS_AS_PRINTED = ('0000000101000100100000100000010000000100001010000010000001000001'
                      '000000101000000000000101010100000000000010000101010000000')

# Feed order: bit for grid cell (0,0) first -- this is what must be clocked into `I`.
FEED_ORDER = WITNESS_AS_PRINTED[::-1]

# Backwards-compatible alias used by the helper below.
BITS = FEED_ORDER

GRID = [
    '.......*.*.',
    '*....*.....',
    '.......*.*.',
    '*.*........',
    '....*.*....',
    '..*.....*..',
    '....*.....*',
    '.*....*....',
    '...*......*',
    '.....*..*..',
    '.*.*.......',
]

MESSAGE_TABLE = {
    'all_zeros': 'EMPTY SKY',
    'all_ones': 'BIG BANG',
    'two_per_row_col_but_adjacent': 'TWO NOT TOUCH',
    'other_wrong': 'TRY AGAIN',
    'correct': '(* TWO STARS *)',
}

EXPECTED = {
    'ones_total': 22,
    'per_row': 2,
    'per_col': 2,
    'no_adjacency': True,
}


def grid_from_bits(bits: str) -> list[str]:
    """Row-major: first bit fed is the top-left cell, last is the bottom-right."""
    assert len(bits) == FEED_BITS, f'expected {FEED_BITS} bits, got {len(bits)}'
    return [''.join('*' if bits[r * GRID_SIZE + c] == '1' else '.'
                    for c in range(GRID_SIZE))
            for r in range(GRID_SIZE)]


def rot180(grid: list[str]) -> list[str]:
    return [''.join(reversed(row)) for row in reversed(grid)]


def check_constraints(grid: list[str]) -> dict:
    """The four mechanical rules, measured on a grid.

    Returns the star total, the per-row and per-column counts, every adjacent pair found (diagonals
    included), and a boolean per rule -- so a caller can assert whichever subset it is about rather
    than re-implementing any of them.
    """
    ones = sum(row.count('*') for row in grid)
    rows = [row.count('*') for row in grid]
    cols = [sum(1 for r in range(GRID_SIZE) if grid[r][c] == '*')
            for c in range(GRID_SIZE)]
    adj = []
    for r in range(GRID_SIZE):
        for c in range(GRID_SIZE):
            if grid[r][c] != '*':
                continue
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < GRID_SIZE and 0 <= cc < GRID_SIZE \
                            and grid[rr][cc] == '*':
                        adj.append((r, c, rr, cc))
    return {
        'ones_total': ones,
        'row_counts': rows,
        'col_counts': cols,
        'per_row_ok': all(n == 2 for n in rows),
        'per_col_ok': all(n == 2 for n in cols),
        'adjacent_pairs': adj,
        'no_adjacency': not adj,
        'total_ok': ones == 22,
    }


def main() -> int:
    problems = []

    print(f'witness length           : {len(WITNESS_AS_PRINTED)} (expect {FEED_BITS})')
    if len(WITNESS_AS_PRINTED) != FEED_BITS:
        problems.append(f'witness length {len(WITNESS_AS_PRINTED)} != {FEED_BITS}')

    feed_grid = grid_from_bits(FEED_ORDER)
    printed_grid = grid_from_bits(WITNESS_AS_PRINTED)
    ok_feed = feed_grid == GRID
    ok_printed = printed_grid == rot180(GRID)
    non_vacuous = rot180(GRID) != GRID

    print(f'feed order  -> grid == grid  : {ok_feed}')
    print(f'as-printed  -> grid == rot180: {ok_printed}'
          f'   (distinction meaningful: {non_vacuous})')
    if not ok_feed:
        problems.append('feed order does not reproduce the published grid')
    if not ok_printed:
        problems.append('as-printed witness is not the 180-degree rotation of the grid')
    if not non_vacuous:
        problems.append('the grid is 180-degree symmetric, so this check is vacuous')

    c = check_constraints(GRID)
    print()
    print(f'ones total               : {c["ones_total"]} (expect 22)   '
          f'{"OK" if c["total_ok"] else "FAIL"}')
    print(f'row counts               : {c["row_counts"]}   '
          f'{"OK" if c["per_row_ok"] else "FAIL"}')
    print(f'col counts               : {c["col_counts"]}   '
          f'{"OK" if c["per_col_ok"] else "FAIL"}')
    print(f'adjacent star pairs      : {len(c["adjacent_pairs"])}   '
          f'{"OK" if c["no_adjacency"] else "FAIL"}')

    if not c['total_ok']:
        problems.append(f'ones total is {c["ones_total"]}, expected 22')
    if not c['per_row_ok']:
        problems.append(f'row counts {c["row_counts"]}')
    if not c['per_col_ok']:
        problems.append(f'col counts {c["col_counts"]}')
    if not c['no_adjacency']:
        problems.append(f'{len(c["adjacent_pairs"])} adjacent star pairs')

    print()
    print(f'answer string            : {ANSWER_STRING!r}')
    print(f'success cycle            : {SUCCESS_CYCLE}')
    print()
    for k, v in MESSAGE_TABLE.items():
        print(f'  {k:32} -> {v!r}')

    print()
    if problems:
        print('TARGET VERIFICATION: FAIL')
        for p in problems:
            print(f'  - {p}')
        return 1
    print('TARGET VERIFICATION: PASS '
          '(grid consistent with the bit vector; all four mechanical constraints hold)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
