#!/usr/bin/env python3
"""check_step2.py -- executable acceptance gate for Step 2 (the known solution).

Three jobs:

  1. QUOTES -- every verbatim quote this project relies on from the published writeup
     must actually be present in the retrieved source snapshot. Comparison is done on
     whitespace-normalised text so the HTML->text transform cannot create false passes
     or false failures. If the snapshot is absent the quote checks report SKIP (the
     snapshot is deliberately untracked), and that is called out, not silently passed.

  2. TARGET -- the published target answer must be internally consistent: the witness
     string, the ASCII grid, the four mechanical constraints, and the non-vacuous
     feed-order/as-printed relationship.

  3. DISCREPANCIES -- the two rows where we contradict the published record must be
     backed by our own Step 1 measurements, not by opinion.

    .venv/Scripts/python tools/checks/check_step2.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))          # importable as `tools.*` from any cwd

from tools import target                        # noqa: E402

SNAPSHOT_TXT = ROOT / 'recon' / 'sources' / 'known_solution.txt'
SNAPSHOT_HTML = ROOT / 'recon' / 'sources' / 'known_solution.html'
INVENTORY = ROOT / 'recon' / 'inventory.json'

EXPECTED_SHA256 = {
    'known_solution.html': '0e73f73c567a04f87f6f7cafc683d9745526c4390d19ae9661c7e846e24714fb',
    'known_solution.txt': '10ae574bf1e885d4b4d6d4aa46be3300d559267baa26d3914c4bbd18612e11dd',
}

# Verbatim quotes this project depends on. Each is a claim of theirs that we either
# build on, or explicitly contradict (marked).
QUOTES = {
    'grid_is_11x11':
        'The chip is a two-star Star Battle verifier on an 11x11 grid.',
    'answer_string':
        'The answer string is TWO STARS',
    'success_cycle':
        'asserts on cycle 126',
    'regions_spell_js':
        'across the grid, which is the Jane Street wordmark',
    'count_738_other_writeups':
        'brought the count up to the 738 that matches other public writeups',
    'conb_is_functional':
        'One category that does not belong on the physical-only list is',
    'clkbuf_passthrough':
        'Treating clkbuf as a pass-through',
    'largest_nets_are_power':
        'compute the total polygon area of each resulting net, and drop the two largest nets',
    'via3_wrong_datatype':
        'the first mapping I used came from an old project fork and had',
    'tech_lef_has_no_pins':
        'the technology LEF that ships with the PDK',
    'reset_four_cycles_bug':
        'the first harness I wrote held reset for four cycles instead of three',
    'lut_never_decoded':
        'Rather than fully reverse-engineer that LUT by hand, formal verification can '
        'answer the question directly from the netlist',
    'solver_ignores_structure':
        'The solver did not care about the structure of the problem',
    'scale_unknown':
        'I do not know how well this holds for a design much bigger than this one',
    'klayout_tracer_untested':
        'I never pushed the KLayout path far enough to say whether it would have worked too',
    # the two claims we contradict
    'CLAIM_arefs_present':
        'a lot of them are AREFs (array references) that the pad ring and fill-cell rows use',
    'CLAIM_rom_cells_90deg':
        'Cells in the output ROM block are placed with 90-degree rotation.',
}

results: list[tuple[str, str, object, object]] = []   # (status, label, actual, expected)


def check(label, actual, expected):
    results.append(('PASS' if actual == expected else 'FAIL', label, actual, expected))


def skip(label, why):
    results.append(('SKIP', label, why, ''))


def norm(text: str) -> str:
    return re.sub(r'\s+', ' ', text)


def main() -> int:
    # ---------------- 1. quotes ------------------------------------------------
    if SNAPSHOT_TXT.exists():
        import hashlib
        for name, want in EXPECTED_SHA256.items():
            p = ROOT / 'recon' / 'sources' / name
            got = hashlib.sha256(p.read_bytes()).hexdigest()
            check(f'snapshot sha256 {name}', got, want)
        text = norm(SNAPSHOT_TXT.read_text(encoding='utf-8', errors='replace'))
        for label, q in QUOTES.items():
            check(f'quote present: {label}', norm(q) in text, True)
    else:
        skip('quote verification', f'{SNAPSHOT_TXT} absent (untracked snapshot)')

    # ---------------- 2. target consistency -----------------------------------
    grid = target.grid_from_bits(target.FEED_ORDER)
    check('witness length', len(target.WITNESS_AS_PRINTED), target.FEED_BITS)
    check('feed order reproduces grid', grid, target.GRID)
    check('as-printed is 180-degree rotation',
          target.grid_from_bits(target.WITNESS_AS_PRINTED), target.rot180(target.GRID))
    check('feed/printed distinction is non-vacuous',
          target.rot180(target.GRID) != target.GRID, True)
    c = target.check_constraints(target.GRID)
    check('constraint: 22 ones', c['ones_total'], target.EXPECTED['ones_total'])
    check('constraint: 2 per row', c['per_row_ok'], True)
    check('constraint: 2 per column', c['per_col_ok'], True)
    check('constraint: no adjacency', c['no_adjacency'], True)
    check('answer string', target.ANSWER_STRING, 'TWO STARS')
    check('success cycle', target.SUCCESS_CYCLE, 126)

    # ---------------- 3. discrepancy rows backed by our own data --------------
    inv = json.loads(INVENTORY.read_text(encoding='utf-8'))
    g = inv['gds']
    check('D8: zero AREFs in the top cell', g['array_references'], 0)
    check('D8: all 9875 placements are plain SREFS', g['instances_total'], 9875)
    check('D9: no 90-degree placements',
          any('90' in k or '270' in k for k in g['std_cell_transforms']), False)
    check('D9: orientation set is 0/180 plus mirror',
          sorted(g['std_cell_transforms']),
          ['rot000', 'rot000_mirror', 'rot180', 'rot180_mirror'])
    check('D9: 994 instances mirrored and/or 180-rotated',
          g['std_cell_transforms']['rot000_mirror'] + g['std_cell_transforms']['rot180']
          + g['std_cell_transforms']['rot180_mirror'], 994)
    check('D1: 738 convention reproduced', g['functional_738_convention'], 738)
    check('D11: 728 excluding all physical-only',
          g['logic_cells_excl_all_physical'], 728)

    # ---------------- report ---------------------------------------------------
    width = max(len(r[1]) for r in results)
    n_fail = n_skip = 0
    for status, label, actual, expected in results:
        if status == 'FAIL':
            n_fail += 1
            print(f'  FAIL  {label:<{width}}  got {actual!r}  want {expected!r}')
        elif status == 'SKIP':
            n_skip += 1
            print(f'  SKIP  {label:<{width}}  {actual}')
        else:
            print(f'  PASS  {label}')
    print()
    passed = sum(1 for r in results if r[0] == 'PASS')
    print(f'{passed} passed, {n_fail} failed, {n_skip} skipped')
    if n_fail:
        print(f'STEP 2 GATE: FAIL ({n_fail} failing)')
        return 1
    if n_skip:
        print('STEP 2 GATE: PASS (with skips -- re-fetch the source snapshot to close them)')
        return 0
    print('STEP 2 GATE: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
