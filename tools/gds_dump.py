#!/usr/bin/env python3
"""gds_dump.py -- dependency-free GDSII recon for the Jane Street ASIC puzzle.

Dumps: structure list, cell (SREF) instantiation census, top-cell bounding box,
per-element geometry, and every TEXT string on every layer.

Usage:
    python tools/gds_dump.py puzzle.gds
    python tools/gds_dump.py puzzle.gds --srefs --text
"""
import struct
import sys
from collections import Counter, defaultdict

RT = {
    0x00: 'HEADER', 0x01: 'BGNLIB', 0x02: 'LIBNAME', 0x03: 'UNITS', 0x04: 'ENDLIB',
    0x05: 'BGNSTR', 0x06: 'STRNAME', 0x07: 'ENDSTR', 0x08: 'BOUNDARY', 0x09: 'PATH',
    0x0A: 'SREF', 0x0B: 'AREF', 0x0C: 'TEXT', 0x0D: 'LAYER', 0x0E: 'DATATYPE',
    0x0F: 'WIDTH', 0x10: 'XY', 0x11: 'ENDEL', 0x12: 'SNAME', 0x13: 'COLROW',
    0x16: 'TEXTTYPE', 0x18: 'STRING', 0x19: 'STRANS', 0x1A: 'MAG', 0x1B: 'ANGLE',
    0x17: 'PRESENTATION', 0x2D: 'BGNEXTN', 0x2E: 'ENDEXTN',
}
ELEM = {'BOUNDARY', 'PATH', 'SREF', 'AREF', 'TEXT', 'BOX', 'NODE'}


def records(data):
    """Yield (offset, rectype_name, rectype, datatype, raw_data)."""
    i = 0
    n = len(data)
    while i + 4 <= n:
        ln, rt, dt = struct.unpack('>hBB', data[i:i + 4])
        if ln < 4 or i + ln > n:
            raise ValueError(f'bad record length {ln} at offset {i}')
        yield i, RT.get(rt, f'0x{rt:02x}'), rt, dt, data[i + 4:i + ln]
        if rt == 0x04:  # ENDLIB
            return
        i += ln


def real(blob):
    """MAG/ANGLE payload: 8-byte IEEE double (GDS600), 4-byte float, or 2-byte int."""
    if len(blob) >= 8:
        return struct.unpack('>d', blob[:8])[0]
    if len(blob) == 4:
        return struct.unpack('>f', blob)[0]
    return float(struct.unpack('>h', blob[:2])[0])


def s(blob):
    return blob.split(b'\0')[0].decode('ascii', 'replace')


def ints2(blob):
    return list(struct.unpack(f'>{len(blob) // 2}h', blob[:len(blob) // 2 * 2]))


def coords(blob):
    """XY record -> list of (x, y) in database units."""
    n = len(blob) // 8
    if n * 8 != len(blob):
        print(f'  [warn] XY record length {len(blob)} is not a multiple of 8 '
              f'({n} pairs, {len(blob) - n * 8} trailing bytes ignored)', file=sys.stderr)
    v = struct.unpack(f'>{n * 2}i', blob[:n * 8])   # 2 int32 per point
    return list(zip(v[0::2], v[1::2]))


def parse(path):
    data = open(path, 'rb').read()
    units = None
    libname = None
    header = None
    structs = {}          # name -> dict
    order = []
    cur = None
    pend = defaultdict(list)   # records pending for current element
    # each element: dict(kind, layer, datatype, xy, sname, text, strans, mag, angle, path)
    el = None

    def flush():
        nonlocal el
        if el and cur is not None:
            cur['elems'].append(el)
        el = None

    for off, name, rt, dt, blob in records(data):
        if name == 'HEADER':
            header = ints2(blob)[0]
        elif name == 'LIBNAME':
            libname = s(blob)
        elif name == 'UNITS':
            vals = struct.unpack('>dd', blob[:16])
            units = vals
        elif name == 'BGNSTR':
            cur = None
        elif name == 'STRNAME':
            flush()
            cur = {'name': s(blob), 'elems': [], 'off': off}
            structs[cur['name']] = cur
            order.append(cur['name'])
        elif name == 'ENDSTR':
            flush()
            cur = None
        elif name in ELEM:
            flush()
            if cur is None:
                raise ValueError(f'element {name} outside structure at {off}')
            el = {'kind': name}
        elif el is not None:
            if name == 'LAYER':
                el['layer'] = ints2(blob)[0]
            elif name == 'DATATYPE':
                el['datatype'] = ints2(blob)[0]
            elif name == 'XY':
                el['xy'] = coords(blob)
            elif name == 'SNAME':
                el['sname'] = s(blob)
            elif name == 'STRING':
                el['text'] = s(blob)
            elif name == 'STRANS':
                el['strans'] = ints2(blob)[0]
            elif name == 'MAG':
                el['mag'] = real(blob)
            elif name == 'ANGLE':
                el['angle'] = real(blob)
            elif name == 'WIDTH':
                el['width'] = ints2(blob)[0]
            elif name == 'PATHTYPE':
                el['pathtype'] = ints2(blob)[0]
            elif name == 'PRESENTATION':
                el['presentation'] = ints2(blob)[0]
    return dict(header=header, libname=libname, units=units,
                structs=structs, order=order, nbytes=len(data))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'puzzle.gds'
    G = parse(path)
    su, du = (G['units'] or ((1e-3 / 1000), 1e-6))
    dbu = su / du                      # database units per micron is 1/du
    print(f'file            : {path}  ({G["nbytes"]:,} bytes)')
    print(f'GDS version     : {G["header"]}   library name: {G["libname"]!r}')
    print(f'units           : user={su:g} m  db={du:g} m   -> 1 dbu = {du * 1e6:g} um')
    print(f'structures      : {len(G["order"])}')

    # classify: leaf cells (no SREF) vs hierarchy
    leaves, hier = [], []
    for name in G['order']:
        S = G['structs'][name]
        has_ref = any(e['kind'] in ('SREF', 'AREF') for e in S['elems'])
        (hier if has_ref else leaves).append(name)
    top = max(hier or G['order'], key=lambda n: sum(
        1 for e in G['structs'][n]['elems'] if e['kind'] in ('SREF', 'AREF')))
    print(f'leaf cells      : {len(leaves)}   hierarchical: {len(hier)}   top cell: {top!r}')
    print()

    print('=== CELL INSTANTIATION CENSUS (master cell -> #SREF/AREF in the design) ===')
    T = G['structs'][top]
    inst = Counter(e.get('sname', '?') for e in T['elems'] if e['kind'] == 'SREF')
    aref = Counter(e.get('sname', '?') for e in T['elems'] if e['kind'] == 'AREF')
    tot = 0
    for k, v in inst.most_common():
        tot += v
        print(f'  {k:34} {v:5}' + (f'   (AREF x{aref[k]})' if k in aref else ''))
    for k, v in aref.items():
        if k not in inst:
            print(f'  {k:34} {0:5}   (AREF x{v})')
    print(f'  {"TOTAL placed instances":34} {tot + sum(aref.values()):5}')
    print()

    # bounding box of top cell from all geometry (recursive over SREF placement)
    bbox = None
    def walk(cell, x0, y0, depth=0):
        nonlocal bbox
        for e in G['structs'][cell]['elems']:
            if e['kind'] == 'SREF':
                px, py = e['xy'][0]
                walk(e.get('sname', cell), x0 + px, y0 + py, depth + 1)
            elif e['kind'] in ('BOUNDARY', 'PATH', 'BOX', 'TEXT'):
                for x, y in e.get('xy', []):
                    ax, ay = x0 + x, y0 + y
                    if bbox is None:
                        bbox = [ax, ay, ax, ay]
                    else:
                        bbox[0] = min(bbox[0], ax); bbox[1] = min(bbox[1], ay)
                        bbox[2] = max(bbox[2], ax); bbox[3] = max(bbox[3], ay)
    walk(top, 0, 0)
    if bbox:
        w = (bbox[2] - bbox[0]) * du * 1e6
        h = (bbox[3] - bbox[1]) * du * 1e6
        print('=== DIE BOUNDING BOX (top cell, recursive) ===')
        print(f'  dbu: {bbox}')
        print(f'  um : x {bbox[0] * du * 1e6:.2f} .. {bbox[2] * du * 1e6:.2f}   '
              f'y {bbox[1] * du * 1e6:.2f} .. {bbox[3] * du * 1e6:.2f}   ->  {w:.2f} x {h:.2f} um')
        print()

    if '--srefs' in sys.argv or True:
        print('=== TOP-CELL SREF PLACEMENTS (first 40, dbu) ===')
        n = 0
        for e in T['elems']:
            if e['kind'] == 'SREF':
                x, y = e['xy'][0]
                f = e.get('strans', 0)
                print(f'  {e.get("sname", "?"):34} at ({x:9},{y:9})'
                      f' strans={f} mag={e.get("mag", 1.0)} angle={e.get("angle", 0.0)}')
                n += 1
                if n >= 40:
                    break
        print(f'  ... ({len(inst)} distinct masters, {tot + sum(aref.values())} total placements)')
        print()

    print('=== TEXT STRINGS BY LAYER ===')
    bylayer = defaultdict(Counter)
    for name in G['order']:
        for e in G['structs'][name]['elems']:
            if e['kind'] == 'TEXT':
                bylayer[e.get('layer')][e.get('text', '')] += 1
    for lay in sorted(bylayer, key=lambda k: (k is None, k)):
        c = bylayer[lay]
        print(f'  layer {lay}: {sum(c.values())} strings, {len(c)} distinct')
        for t, k in c.most_common(30):
            print(f'      {t!r} x{k}')
    print()

    print('=== GEOMETRY CENSUS BY (layer, kind) ===')
    cen = Counter()
    for name in G['order']:
        for e in G['structs'][name]['elems']:
            cen[(e.get('layer'), e['kind'])] += 1
    for (lay, kind), k in sorted(cen.items(), key=lambda x: (x[0][0] is None, x[0][0], x[0][1])):
        print(f'  layer {str(lay):>4}  {kind:9} {k}')


if __name__ == '__main__':
    main()
