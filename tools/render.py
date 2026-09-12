#!/usr/bin/env python3
"""render.py -- rasterise the puzzle GDS (or any layer subset) to PNG for visual recon.

    .venv/Scripts/python tools/render.py <gds> <outdir> [--layers 67,68,69,70,236] [--zoom x0,y0,x1,y1]

With no --layers it draws every layer present, coloured, with a legend.
"""
import os
import sys
from collections import defaultdict

import gdstk
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPoly
from matplotlib.collections import PatchCollection

CMAP = [
    '#e6194b', '#3cb44b', '#ffe119', '#4363d8', '#f58231', '#911eb4',
    '#46f0f0', '#f032e6', '#bcf60c', '#fabebe', '#008080', '#e6beff',
    '#9a6324', '#fffac8', '#800000', '#aaffc3', '#808000', '#ffd8b1',
    '#000075', '#808080',
]


def polys_of(cell, layer, datatype):
    out = []
    for p in cell.get_polygons(layer=layer, datatype=datatype, depth=None):
        out.append(p)
    return out


def draw(ax, cell, layer, datatype, color, alpha=0.7, lw=0.0):
    patches = []
    skipped = 0
    for p in polys_of(cell, layer, datatype):
        pts = getattr(p, 'points', None)
        if pts is None or len(pts) < 3:
            skipped += 1
            continue
        patches.append(MplPoly(pts, closed=True))
    if skipped:
        print(f'    [skip] {skipped} degenerate polygon(s) on {layer}/{datatype}')
    if patches:
        ax.add_collection(PatchCollection(patches, facecolor=color,
                                          edgecolor=color, alpha=alpha,
                                          linewidths=lw))
    return len(patches)


def main():
    gds = sys.argv[1]
    outdir = sys.argv[2]
    zoom = None
    layers = None
    if '--zoom' in sys.argv:
        z = sys.argv[sys.argv.index('--zoom') + 1]
        zoom = tuple(float(v) for v in z.split(','))
    if '--layers' in sys.argv:
        layers = [int(v) for v in sys.argv[sys.argv.index('--layers') + 1].split(',')]
    os.makedirs(outdir, exist_ok=True)

    lib = gdstk.read_gds(gds)
    top = lib.top_level()[0]
    print(f'top cell {top.name!r}  bbox {top.bounding_box()}')

    # collect every (layer, datatype) present anywhere
    seen = defaultdict(int)
    for c in lib.cells:
        for p in c.get_polygons(depth=0):
            seen[(p.layer, p.datatype)] += 1
    allpairs = sorted(k for k, v in seen.items() if v)
    if layers:
        allpairs = [k for k in allpairs if k[0] in layers]
    print('layers to draw:', allpairs)

    color = {k: CMAP[i % len(CMAP)] for i, k in enumerate(allpairs)}

    fig, ax = plt.subplots(figsize=(14, 14))
    total = 0
    for k in allpairs:
        n = draw(ax, top, k[0], k[1], color[k])
        total += n
        print(f'  layer {k[0]}/{k[1]}: {n} polygons')
    ax.set_aspect('equal')
    ax.autoscale_view()
    if zoom:
        ax.set_xlim(zoom[0], zoom[2])
        ax.set_ylim(zoom[1], zoom[3])
    else:
        ax.set_xlim(0, 200)
        ax.set_ylim(0, 300)
    ax.set_facecolor('#101010')
    fig.patch.set_facecolor('#101010')
    ax.tick_params(colors='#888')
    for sp in ax.spines.values():
        sp.set_color('#444')
    handles = [MplPoly([(0, 0)], facecolor=color[k], edgecolor='none') for k in allpairs]
    ax.legend(handles, [f'{k[0]}/{k[1]}' for k in allpairs], loc='upper left',
              fontsize=6, facecolor='#202020', labelcolor='#ddd', framealpha=0.85,
              ncol=2)
    ax.set_title(f'{os.path.basename(gds)}  ({total} polygons)', color='#ddd')
    out = os.path.join(outdir, 'overview.png')
    fig.savefig(out, dpi=110, facecolor=fig.get_facecolor())
    print('wrote', out)
    plt.close(fig)

    # per-layer images
    for k in allpairs:
        if k[1] not in (0, 4, 5, 20, 44, 16, 23, 59):
            continue
        fig, ax = plt.subplots(figsize=(12, 12))
        n = draw(ax, top, k[0], k[1], '#ffcc33' if k[1] != 0 else '#ff4444', alpha=0.9)
        ax.set_aspect('equal')
        ax.set_xlim(0, 200)
        ax.set_ylim(0, 300)
        ax.set_facecolor('#101010')
        fig.patch.set_facecolor('#101010')
        ax.tick_params(colors='#888')
        ax.set_title(f'layer {k[0]}/{k[1]}  ({n} polygons)', color='#ddd')
        f = os.path.join(outdir, f'layer_{k[0]}_{k[1]}.png')
        fig.savefig(f, dpi=110, facecolor=fig.get_facecolor())
        plt.close(fig)
        print('wrote', f)


if __name__ == '__main__':
    main()
