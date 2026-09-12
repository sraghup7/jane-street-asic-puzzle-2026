#!/usr/bin/env python3
"""check_step1.py -- executable acceptance gate for Step 1 (problem dossier).

Two jobs:

  1. REGENERATE the inventory from the raw artifacts and diff it against the
     committed `recon/inventory.json`. If that diff is non-empty, every number the
     dossier cites is suspect.
  2. ASSERT every specific claim the dossier makes. Each check is (label, actual,
     expected) so a failure prints what it actually found.

Exit code 0 iff all checks pass.

    .venv/Scripts/python tools/checks/check_step1.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))

from inventory import build_inventory          # noqa: E402

COMMITTED = ROOT / 'recon' / 'inventory.json'

results: list[tuple[bool, str, object, object]] = []


def check(label: str, actual, expected):
    results.append((actual == expected, label, actual, expected))


def main() -> int:
    committed_text = COMMITTED.read_text(encoding='utf-8')
    committed = json.loads(committed_text)

    fresh = build_inventory()
    fresh_text = json.dumps(fresh, indent=2, sort_keys=True) + '\n'
    check('inventory regenerates bit-identically', fresh_text == committed_text, True)

    inv = committed
    g, v, w, env = inv['gds'], inv['vcd'], inv['warmup'], inv['environment']

    # ---- provenance -----------------------------------------------------------
    check('upstream commit', inv['upstream_commit'],
          'ffd53e0ba24e2fc1c1b12dc824e8eac5888c19a9')
    check('upstream file count', len(inv['upstream_files']), 9)
    check('puzzle.gds sha256',
          inv['upstream_files']['asic-puzzle-2026/puzzle.gds']['sha256'],
          '8913ea4be5367b484d5886c3c5f7608942b67544a0dbe364b17223503b8d851a')

    # ---- GDS structure --------------------------------------------------------
    check('GDS version', g['gds_version'], 600)
    check('GDS library name', g['library_name'], 'LIB')
    check('GDS structure count', g['structure_count'], 81)
    check('GDS top cell', g['top_cell'], 'puzzle')
    check('GDS dbu per micron', g['dbu_per_um'], 1000.0)

    # ---- die ------------------------------------------------------------------
    check('die prBndry w', g['die_prboundary_um']['w'], 200.0)
    check('die prBndry h', g['die_prboundary_um']['h'], 300.0)
    check('die prBndry origin',
          (g['die_prboundary_um']['x0'], g['die_prboundary_um']['y0']), (0.0, 0.0))

    # ---- placement ------------------------------------------------------------
    check('total instances', g['instances_total'], 9875)
    check('instances outside die', g['instances_outside_die'], 36)
    check('array references', g['array_references'], 0)
    check('standard-cell instances', g['standard_cell_instances'], 1618)
    check('sequential instances', g['sequential_instances'], 92)
    check('738 convention count', g['functional_738_convention'], 738)
    check('tap cells', g['physical_by_kind'].get('tapvpwrvgnd'), 676)
    check('decap cells', g['physical_by_kind'].get('decap'), 204)
    check('antenna diodes', g['physical_by_kind'].get('diode'), 10)
    check('via instances', g['by_class'].get('via'), 8221)

    # ---- grid / transforms ----------------------------------------------------
    check('all std cells on the site grid',
          g['site_residual_histogram'], {'0.0': 1618})
    check('transform set',
          sorted(g['std_cell_transforms']),
          ['rot000', 'rot000_mirror', 'rot180', 'rot180_mirror'])
    check('transform total', sum(g['std_cell_transforms'].values()), 1618)
    check('no 90-degree placements',
          any('90' in k or '270' in k for k in g['std_cell_transforms']), False)
    check('populated row count', g['std_cell_row_count'], 52)
    check('populated row pitch', g['row_pitch_um'], 5.44)

    # ---- ports ----------------------------------------------------------------
    ports = {l['text'] for l in g['top_labels'] if l['layer'] == 70}
    check('top-level port labels', sorted(ports),
          ['I', 'O[0]', 'O[1]', 'O[2]', 'O[3]', 'O[4]', 'O[5]', 'O[6]', 'O[7]',
           'clk', 'enable', 'rst_n', 'success'])

    # ---- the hidden Morse strip ----------------------------------------------
    m = g['morse_strip']
    check('layer-236 masters', g['layer_236_masters_count'], 68)
    check('master lacking layer 236', g['layer_236_masters_missing'],
          ['sky130_fd_sc_hd__tapvpwrvgnd_1'])
    check('morse bar count', m['bar_count'], 36)
    check('morse bar y (below the die)', m['y_um'], -52.72)
    check('morse unit', m['unit_um'], 1.38)
    check('morse decodes', m['decoded'], 'PER ARENAM AD ASTRA')
    check('morse fully mapped', m['unmapped_groups'], 0)
    check('morse element string', m['elements'],
          '.--...-..-.-..-..---.--...-...-.-..-')

    # ---- VCD ------------------------------------------------------------------
    check('VCD timescale', v['timescale'], '1ps')
    check('VCD clock rising edges', v['clock_rising_edges'], 312)
    check('VCD clock period (ps)', v['clock_period_ps'], 10000)
    check('VCD feed bits per trial', v['feed_bits_per_trial'], [121, 121])
    check('VCD messages', v['output_messages'], ['TRY AGAIN', 'TRY AGAIN'])
    check('VCD success never high', v['success_ever_high'], False)
    check('VCD first reset run (cycles)', (v['rst_n_runs'][0]['first'],
                                           v['rst_n_runs'][0]['last']), (0, 2))
    check('VCD first feed run (cycles)', (v['enable_runs'][1]['first'],
                                          v['enable_runs'][1]['last']), (4, 124))

    # ---- warm-up --------------------------------------------------------------
    check('warmup file set', w['files'],
          ['00_source.v', '01_netlist.v', '02_netlist_with_power_rails.v',
           '03_post_place_and_route.def', '04_final.gds'])
    check('warmup netlist cell total', w['netlist_cell_total'], 230)
    check('warmup DEF rows', w['def_row_count'], 29)
    check('warmup DEF site width', w['def_site_width_um'], 0.46)
    check('warmup DEF sites per row', w['def_sites_per_row'], 173)
    check('warmup DEF diearea', w['def_diearea_um'], [0, 0, 100000, 100000])

    # ---- environment ----------------------------------------------------------
    check('gdstk available', env['packages']['gdstk'] is not None, True)
    check('shapely available', env['packages']['shapely'] is not None, True)
    check('iverilog available', env['executables_on_path']['iverilog'] is not None, True)

    width = max(len(r[1]) for r in results)
    failures = 0
    for ok, label, actual, expected in results:
        mark = 'PASS' if ok else 'FAIL'
        if not ok:
            failures += 1
            print(f'  {mark}  {label:<{width}}  got {actual!r}  want {expected!r}')
        else:
            print(f'  {mark}  {label}')
    print()
    print(f'{len(results) - failures}/{len(results)} checks passed')
    if failures:
        print(f'STEP 1 GATE: FAIL ({failures} failing)')
        return 1
    print('STEP 1 GATE: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
