#!/usr/bin/env python3
"""kl_recon.py -- authoritative recon of the puzzle GDS via the KLayout Python API.

    .venv/Scripts/python tools/kl_recon.py asic-puzzle-2026/puzzle.gds

Prints: units / dbu, layer census, top-cell die box, every structure name and
whether it is a leaf standard cell or a hierarchy, the placement census, and the
bounding box + instance count of each *non-standard-cell* sub-block (the INTERNAL_*
macros) so they can be located on the die.
"""
import sys
from collections import Counter, defaultdict

import klayout.db as kdb

STD_PREFIXES = ('sky130_fd_sc_hd__',)


def um(v, dbu):
    return v * dbu


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'puzzle.gds'
    ly = kdb.Layout()
    ly.read(path)

    dbu = ly.dbu
    print(f'file         : {path}')
    print(f'dbu          : {dbu:g} um/dbu   ({dbu * 1e6:.6f} nm/dbu)')
    print(f'dbu (exact)  : {dbu}')
    print(f'top cells    : {[c.name for c in ly.top_cells()]}')
    print(f'cells        : {ly.cells()}   layers: {ly.layers()}')
    print()

    print('=== LAYERS ===')
    for li in ly.layer_indexes():
        info = ly.get_info(li)
        n = 0
        cnt = defaultdict(int)
        for c in ly.each_cell():
            it = c.begin_shapes_rec(li)
            while not it.at_end():
                cnt[it.shape().type()] += 1
                it.next()
        tot = sum(cnt.values())
        if tot:
            print(f'  {info.layer:4}/{info.datatype:<3} {info.name or "":26} '
                  f'shapes={tot:7}  {dict(cnt)}')
    print()

    print('=== STRUCTURES ===')
    std, other = [], []
    for c in ly.each_cell():
        insts = sum(1 for _ in c.each_inst())
        (std if c.name.startswith(STD_PREFIXES) else other).append((c.name, insts, c.bbox()))
    print(f'  standard-cell masters: {len(std)}')
    print(f'  other structures     : {len(other)}')
    print()
    print('  --- non-standard-cell structures ---')
    for name, insts, bb in sorted(other, key=lambda x: -x[1]):
        box = bb.to_dtype(dbu)
        print(f'  {name:38} inst_in_cell={insts:6}  '
              f'bbox={box.left:.2f},{box.bottom:.2f}..{box.right:.2f},{box.top:.2f} um  '
              f'({box.width():.2f} x {box.height():.2f})')
    print()

    top = ly.top_cell()
    tb = top.bbox().to_dtype(dbu)
    print('=== TOP CELL DIE ===')
    print(f'  {top.name}: {tb.left:.3f},{tb.bottom:.3f} .. {tb.right:.3f},{tb.top:.3f} um'
          f'   ->  {tb.width():.2f} x {tb.height():.2f} um')
    print()

    print('=== PLACEMENT CENSUS (top cell) ===')
    cnt = Counter()
    for inst in top.each_inst():
        cnt[inst.cell.name] += 1
    sc = sum(v for k, v in cnt.items() if k.startswith(STD_PREFIXES))
    vi = sum(v for k, v in cnt.items() if k.startswith('VIA'))
    ot = sum(v for k, v in cnt.items() if not k.startswith(STD_PREFIXES) and not k.startswith('VIA'))
    print(f'  standard-cell instances : {sc}')
    print(f'  via instances           : {vi}')
    print(f'  other instances         : {ot}')
    print(f'  total                   : {sum(cnt.values())}')
    print()

    # --- logic-cell-only census (drop taps / decaps / diodes / vias / fillers) ---
    skip = ('tapvpwrvgnd', 'decap', 'diode', 'fill', 'conb', 'VIA')
    logic = Counter({k: v for k, v in cnt.items()
                     if not any(x in k for x in skip)})
    print('=== LOGIC CELL CENSUS (excl. taps/decaps/diodes/vias/fillers) ===')
    tot = 0
    flops = 0
    for k, v in logic.most_common():
        tot += v
        if 'df' in k.split('__')[-1][:3] or k.split('__')[-1].startswith('df'):
            flops += v
        print(f'  {k:36} {v:4}')
    print(f'  {"TOTAL logic cells":36} {tot:4}   (of which sequential: {flops})')
    print()

    # --- textual annotations ---
    print('=== TEXT ANNOTATIONS (per layer, distinct strings) ===')
    for li in ly.layer_indexes():
        info = ly.get_info(li)
        strings = Counter()
        for c in ly.each_cell():
            it = c.begin_shapes_rec(li)
            while not it.at_end():
                sh = it.shape()
                if sh.is_text():
                    strings[sh.text.string] += 1
                it.next()
        if strings:
            print(f'  layer {info.layer}/{info.datatype} {info.name or ""}: '
                  f'{sum(strings.values())} labels, {len(strings)} distinct')
            for t, n in strings.most_common(40):
                print(f'      {t!r} x{n}')
    print()

    # --- label geometry: where are the top-level TEXT labels? ---
    print('=== TOP-CELL TEXT LABEL POSITIONS (um) ===')
    for li in ly.layer_indexes():
        info = ly.get_info(li)
        it = top.begin_shapes_rec(li)
        while not it.at_end():
            sh = it.shape()
            if sh.is_text():
                bb = sh.bbox().to_dtype(dbu)
                print(f'  layer {info.layer:4} {sh.text.string!r:28} '
                      f'at ({bb.center().x:.2f}, {bb.center().y:.2f}) um')
            it.next()
        it = None


if __name__ == '__main__':
    main()
