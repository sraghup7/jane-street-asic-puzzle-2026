#!/usr/bin/env python3
"""inventory.py -- machine-generated inventory of the Jane Street ASIC puzzle workspace.

Everything the step-1 dossier asserts must be reproducible from this file. Written as
a library (`build_inventory`) plus a CLI that dumps JSON, so the step-1 check can
regenerate the JSON and diff it against the committed copy.

    python tools/inventory.py                     # write recon/inventory.json
    python tools/inventory.py --print             # dump to stdout

Dependencies: gdstk (pip). No PDK, no LEF, no KLayout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from collections import Counter, OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = ROOT / 'asic-puzzle-2026'

PHYSICAL_ONLY = (
    'tapvpwrvgnd',   # well/body tie
    'decap',         # decoupling capacitor
    'fill',          # density fill
    'diode',         # antenna diode
)
SEQUENTIAL_PREFIX = ('df',)          # dfxtp / dfrtp / dfstp
VIA_PREFIX = ('VIA',)
DECORATION_PREFIX = ('INTERNAL',)    # geometry placed outside the die (see recon)
SITE_UM = 0.46                       # sky130_fd_sc_hd row site width, from the warmup DEF

MORSE = {
    '.-': 'A', '-...': 'B', '-.-.': 'C', '-..': 'D', '.': 'E', '..-.': 'F',
    '--.': 'G', '....': 'H', '..': 'I', '.---': 'J', '-.-': 'K', '.-..': 'L',
    '--': 'M', '-.': 'N', '---': 'O', '.--.': 'P', '--.-': 'Q', '.-.': 'R',
    '...': 'S', '-': 'T', '..-': 'U', '...-': 'V', '.--': 'W', '-..-': 'X',
    '-.--': 'Y', '--..': 'Z',
}


def decode_morse_strip(decorations: list[dict]) -> dict | None:
    """The decoration row below the die is Morse: narrow bar = dot, wide = dash.

    Bars sit on a unit grid; a gap of 1 unit separates elements, >=3 units
    separates letters, >=5 units separates words. Derivation is hand-checkable
    straight from the element string this returns.
    """
    if not decorations:
        return None
    bars = sorted(decorations, key=lambda d: d['x_um'])
    unit = min(d['w_um'] for d in bars)
    x0 = bars[0]['x_um']
    els = [(round((d['x_um'] - x0) / unit), round(d['w_um'] / unit)) for d in bars]

    text, cur, prev_end = '', '', None
    for start, width in els:
        if prev_end is not None:
            gap = start - prev_end
            if gap >= 5:
                text += MORSE.get(cur, f'<{cur}>') + ' '
                cur = ''
            elif gap >= 3:
                text += MORSE.get(cur, f'<{cur}>')
                cur = ''
        cur += '-' if width >= 3 else '.'
        prev_end = start + width
    text += MORSE.get(cur, f'<{cur}>')

    return {
        'unit_um': unit,
        'bar_count': len(bars),
        'y_um': bars[0]['y_um'],
        'elements': ''.join('-' if w >= 3 else '.' for _, w in els),
        'decoded': text.strip(),
        'narrow_bar_count': sum(1 for _, w in els if w < 3),
        'wide_bar_count': sum(1 for _, w in els if w >= 3),
        'unmapped_groups': text.count('<'),
    }


def physical_kind(cell_name: str) -> str:
    """Bucket a physical-only cell by family; 'logic' for everything functional."""
    if not cell_name.startswith('sky130_fd_sc_hd__'):
        return 'non_std'
    short = cell_name.split('__', 1)[1]
    for p in PHYSICAL_ONLY:
        if p in short:
            return p
    return 'logic'


# --------------------------------------------------------------------------- utils
def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def git(*args, cwd=UPSTREAM):
    try:
        out = subprocess.run(['git', *args], cwd=str(cwd), capture_output=True,
                             text=True, check=True)
        return out.stdout.strip()
    except Exception as exc:                                  # pragma: no cover
        return f'<unavailable: {exc}>'


def classify(cell_name: str) -> str:
    if cell_name.startswith(DECORATION_PREFIX):
        return 'decoration'
    if cell_name.startswith(VIA_PREFIX):
        return 'via'
    if not cell_name.startswith('sky130_fd_sc_hd__'):
        return 'other'
    if any(p in cell_name for p in PHYSICAL_ONLY):
        return 'physical_only'
    if cell_name.split('__', 1)[1].startswith(SEQUENTIAL_PREFIX):
        return 'sequential'
    return 'combinational'


# ------------------------------------------------------------------- gds inventory
def gds_inventory(path: Path) -> dict:
    import gdstk

    lib = gdstk.read_gds(str(path))
    top = lib.top_level()[0]
    dbu = lib.unit / lib.precision        # database units per micron

    structs = sorted(c.name for c in lib.cells)

    # --- instance census in the top cell (arrays expanded) ---------------------
    census: Counter[str] = Counter()
    physical_by_kind: Counter[str] = Counter()
    arrays = 0
    placements = 0
    rows: Counter[float] = Counter()
    sites: Counter[float] = Counter()
    transforms: Counter[str] = Counter()
    widths_in_sites: Counter[int] = Counter()
    inside = 0
    outside = 0
    decorations = []

    for ref in top.references:
        n = int(getattr(ref, 'columns', 1) or 1) * int(getattr(ref, 'rows', 1) or 1)
        if n > 1:
            arrays += 1
        census[ref.cell_name] += n
        placements += n
        ox, oy = ref.origin
        is_std_cell = ref.cell_name.startswith('sky130_fd_sc_hd__')
        if is_std_cell:
            physical_by_kind[physical_kind(ref.cell_name)] += n
        local_bb = ref.cell.bounding_box() or ((0.0, 0.0), (0.0, 0.0))
        local_w = local_bb[1][0] - local_bb[0][0]
        if ref.cell_name.startswith(DECORATION_PREFIX):
            decorations.append({'cell': ref.cell_name, 'x_um': round(ox, 2),
                                'y_um': round(oy, 2), 'w_um': round(local_w, 2)})
            outside += n
        elif oy < 0:
            outside += n
        else:
            inside += n
        # Only real standard cells sit on the row/site grid; via instances are
        # dropped anywhere along a route and would poison these statistics.
        if is_std_cell:
            rows[round(oy, 2)] += 1
            sites[round(ox - SITE_UM * round(ox / SITE_UM), 4)] += 1
            deg = int(round(math.degrees(ref.rotation))) % 360
            transforms[f'rot{deg:03d}' + ('_mirror' if ref.x_reflection else '')] += 1
            widths_in_sites[int(round(local_w / SITE_UM))] += 1

    by_class: Counter[str] = Counter()
    for name, n in census.items():
        by_class[classify(name)] += n

    std = sum(n for k, n in census.items() if k.startswith('sky130_fd_sc_hd__'))
    sequential = by_class.get('sequential', 0)
    taps = physical_by_kind.get('tapvpwrvgnd', 0)
    decaps = physical_by_kind.get('decap', 0)
    diodes = physical_by_kind.get('diode', 0)
    fills = physical_by_kind.get('fill', 0)

    # --- die outline: the design's own prBndry marker (layer 235/4) ------------
    prbox = None
    for poly in top.get_polygons(layer=235, datatype=4):
        pts = poly.points                      # gdstk returns Polygon, not ndarray
        box = (float(pts[:, 0].min()), float(pts[:, 1].min()),
               float(pts[:, 0].max()), float(pts[:, 1].max()))
        prbox = box if prbox is None else (
            min(prbox[0], box[0]), min(prbox[1], box[1]),
            max(prbox[2], box[2]), max(prbox[3], box[3]))

    # --- layer census over the *placed* design (recursive), not the library ----
    bb = top.bounding_box()
    layers: Counter[tuple[int, int]] = Counter()
    for p in top.get_polygons(depth=None):
        layers[(p.layer, p.datatype)] += 1

    # --- top-level labels -----------------------------------------------------
    labels = []
    for lab in top.labels:
        labels.append({'text': lab.text, 'layer': lab.layer,
                       'texttype': lab.texttype,
                       'x_um': round(lab.origin[0], 2), 'y_um': round(lab.origin[1], 2)})

    # --- which cell *masters* carry the layer-236 boundary marker? -------------
    masters_236 = sorted(c.name for c in lib.cells
                         if any(p.layer == 236 for p in c.polygons))
    std_masters = sorted(c.name for c in lib.cells
                         if c.name.startswith('sky130_fd_sc_hd__'))

    return {
        'file': path.name,
        'bytes': path.stat().st_size,
        'sha256': sha256(path),
        'gds_version': lib.cells and _gds_version(path),
        'library_name': lib.name,
        'dbu_per_um': round(dbu, 6),
        'structure_count': len(structs),
        'top_cell': top.name,
        'standard_cell_masters': len([s for s in structs
                                      if s.startswith('sky130_fd_sc_hd__')]),
        'die_bbox_um': {'x0': round(bb[0][0], 2), 'y0': round(bb[0][1], 2),
                        'x1': round(bb[1][0], 2), 'y1': round(bb[1][1], 2),
                        'w': round(bb[1][0] - bb[0][0], 2),
                        'h': round(bb[1][1] - bb[0][1], 2)},
        'die_prboundary_um': ({'x0': prbox[0], 'y0': prbox[1],
                               'x1': prbox[2], 'y1': prbox[3],
                               'w': round(prbox[2] - prbox[0], 2),
                               'h': round(prbox[3] - prbox[1], 2)} if prbox else None),
        'instances_total': placements,
        'instances_inside_die': inside,
        'instances_outside_die': outside,
        'array_references': arrays,
        'by_class': dict(sorted(by_class.items())),
        'standard_cell_instances': std,
        'physical_by_kind': dict(sorted(physical_by_kind.items())),
        'sequential_instances': sequential,
        'functional_738_convention': std - taps - decaps,
        'logic_cells_excl_all_physical': std - taps - decaps - diodes - fills,
        'census': dict(sorted(census.items(), key=lambda kv: (-kv[1], kv[0]))),
        'std_cell_row_count': len(rows),
        'std_cell_rows': sorted(rows),
        'row_pitch_um': _row_pitch(rows),
        'site_residual_histogram': {str(k): v for k, v in sorted(sites.items())},
        'std_cell_transforms': dict(sorted(transforms.items())),
        'std_cell_widths_in_sites': {str(k): v for k, v in sorted(widths_in_sites.items())},
        'layers': {f'{l}/{d}': n for (l, d), n in sorted(layers.items())},
        'top_labels': labels,
        'decorations_below_die': decorations,
        'morse_strip': decode_morse_strip(decorations),
        'layer_236_masters_count': len(masters_236),
        'layer_236_masters_missing': sorted(set(std_masters) - set(masters_236)),
    }


def _gds_version(path: Path) -> int:
    import struct
    with open(path, 'rb') as fh:
        head = fh.read(6)
    return struct.unpack('>h', head[4:6])[0]


def _row_pitch(rows: Counter) -> float | None:
    ys = sorted(y for y in rows if y > 0)
    if len(ys) < 2:
        return None
    deltas = Counter(round(ys[i + 1] - ys[i], 4) for i in range(len(ys) - 1))
    return deltas.most_common(1)[0][0]


# ------------------------------------------------------------------- vcd inventory
def vcd_inventory(path: Path) -> dict:
    sys.path.insert(0, str(ROOT / 'tools'))
    from vcd_probe import parse_vcd, value_at, decode

    ts, var_defs, changes, maxtime = parse_vcd(str(path))
    by_name = {v['name']: i for i, v in var_defs.items()}
    rises = [t for t, v in changes[by_name['clk']] if v == '1']

    def sample(ident, t):
        return decode(value_at(changes, by_name[ident], t + 1))

    rows = [{'cycle': i, 'rst_n': sample('rst_n', t), 'enable': sample('enable', t),
             'I': sample('I', t), 'O': sample('O', t), 'success': sample('success', t)}
            for i, t in enumerate(rises)]

    def runs(key):
        out = []
        for r in rows:
            if out and out[-1]['v'] == r[key]:
                out[-1]['last'] = r['cycle']
            else:
                out.append({'v': r[key], 'first': r['cycle'], 'last': r['cycle']})
        return out

    feed = [r for r in rows if r['enable'] == '1']
    # contiguous feed runs (the VCD contains two independent trials)
    trials = []
    for r in feed:
        if trials and trials[-1]['last'] + 1 == r['cycle']:
            trials[-1]['last'] = r['cycle']
            trials[-1]['bits'] += (r['I'] or 'x')
        else:
            trials.append({'first': r['cycle'], 'last': r['cycle'],
                           'bits': (r['I'] or 'x')})

    def find(layer, texttype):
        return [l for l in rows if False]  # placeholder, labels parsed below

    obytes = []
    prev = None
    for r in rows:
        if r['O'] is None or r['O'] == prev:
            continue
        prev = r['O']
        n = int(r['O'], 2) if re.fullmatch(r'[01]+', r['O']) else None
        obytes.append({'cycle': r['cycle'], 'bin': r['O'], 'dec': n,
                       'char': (chr(n) if n and 32 <= n < 127 else ('NUL' if n == 0 else '?'))})

    return {
        'file': path.name,
        'sha256': sha256(path),
        'timescale': ts,
        'end_time': maxtime,
        'clock_rising_edges': len(rises),
        'clock_period_ps': (rises[1] - rises[0]) if len(rises) > 1 else None,
        'signals': {v['name']: int(v['width']) for v in var_defs.values()},
        'rst_n_runs': runs('rst_n'),
        'enable_runs': runs('enable'),
        'success_runs': runs('success'),
        'feed_trials': trials,
        'feed_bits_per_trial': [len(t['bits']) for t in trials],
        'output_bytes': obytes,
        'output_message': ''.join(b['char'] for b in obytes),
        'output_messages': _messages(obytes),
        'success_ever_high': any(r['success'] == '1' for r in rows),
    }


def _messages(obytes: list[dict]) -> list[str]:
    """Printable runs between NUL terminators, in emission order."""
    out, cur = [], []
    for b in obytes:
        if b['char'] == 'NUL':
            if cur:
                out.append(''.join(cur))
                cur = []
        elif b['char'] not in ('?',):
            cur.append(b['char'])
    if cur:
        out.append(''.join(cur))
    return out


# --------------------------------------------------------------- warmup inventory
def warmup_inventory(d: Path) -> dict:
    files = sorted(p.name for p in d.iterdir() if p.is_file())
    net = (d / '01_netlist.v').read_text(errors='replace')
    census = Counter(re.findall(r'sky130_fd_sc_hd__[a-z0-9_]+', net))
    defc = (d / '03_post_place_and_route.def').read_text(errors='replace')
    die = re.search(r'DIEAREA\s*\(\s*(\d+)\s+(\d+)\s*\)\s*\(\s*(\d+)\s+(\d+)\s*\)', defc)
    units = re.search(r'UNITS\s+DISTANCE\s+MICRONS\s+(\d+)', defc)
    # NB: orientation is two chars ("N" or "FS" for mirrored rows) -> \S+, not \S.
    rows = re.findall(
        r'^ROW\s+(\S+)\s+(\S+)\s+(\d+)\s+(\d+)\s+(\S+)\s+DO\s+(\d+)\s+BY\s+(\d+)'
        r'\s+STEP\s+(\d+)\s+(\d+)', defc, re.M)
    return {
        'files': files,
        'hashes': {p.name: sha256(p) for p in sorted(d.iterdir()) if p.is_file()},
        'netlist_cell_census': dict(sorted(census.items(), key=lambda kv: (-kv[1], kv[0]))),
        'netlist_cell_total': sum(census.values()),
        'def_units_per_micron': int(units.group(1)) if units else None,
        'def_diearea_um': ([int(die.group(1)), int(die.group(2)),
                            int(die.group(3)), int(die.group(4))] if die else None),
        'def_row_count': len(rows),
        'def_row_origins_um': sorted({int(r[3]) / 1000.0 for r in rows}),
        'def_site_width_um': (int(rows[0][7]) / 1000.0) if rows else None,
        'def_sites_per_row': int(rows[0][5]) if rows else None,
    }


# ------------------------------------------------------------------- env inventory
def env_inventory() -> dict:
    import importlib
    pkgs = {}
    for name in ('gdstk', 'klayout', 'numpy', 'scipy', 'networkx', 'matplotlib', 'shapely'):
        try:
            m = importlib.import_module(name)
            pkgs[name] = getattr(m, '__version__', 'present')
        except Exception:
            pkgs[name] = None
    tools = {}
    for name in ('iverilog', 'vvp', 'verilator', 'yosys', 'sby', 'klayout',
                 'magic', 'netgen', 'make', 'git', 'wsl.exe'):
        tools[name] = shutil.which(name)
    return {'python': sys.version.split()[0], 'packages': pkgs,
            'executables_on_path': tools}


# --------------------------------------------------------------------------- main
def build_inventory() -> dict:
    return {
        'upstream_commit': git('rev-parse', 'HEAD'),
        'upstream_commit_date': git('log', '-1', '--format=%ad'),
        'upstream_remote': git('config', '--get', 'remote.origin.url'),
        'upstream_files': {p.relative_to(ROOT).as_posix():
                           {'bytes': p.stat().st_size, 'sha256': sha256(p)}
                           for p in sorted(UPSTREAM.rglob('*'))
                           if p.is_file() and '.git' not in p.parts},
        'gds': gds_inventory(UPSTREAM / 'puzzle.gds'),
        'vcd': vcd_inventory(UPSTREAM / 'example_inputs.vcd'),
        'warmup': warmup_inventory(UPSTREAM / 'warmup'),
        'environment': env_inventory(),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(ROOT / 'recon' / 'inventory.json'))
    ap.add_argument('--print', action='store_true')
    a = ap.parse_args()
    inv = build_inventory()
    text = json.dumps(inv, indent=2, sort_keys=True)
    if a.print:
        print(text)
    else:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text + '\n', encoding='utf-8')
        print(f'wrote {a.out}  ({len(text)} bytes)')


if __name__ == '__main__':
    main()
